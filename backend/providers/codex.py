"""Luna through local Codex subscription authentication, not Platform API keys."""

import json
from pathlib import Path
import queue
import threading
import time

from backend.models import TranslationRequest, TranslationResponse
from .base import TranslationCancelled, TranslationEngine, TranslationError
from .rpc import CodexRPC
from .sessions import DocumentSessions
from .protocol import INSTRUCTIONS, build_prompt, local_response, output_schema, parse_response




class SessionBusyError(TranslationError):
    """A different Codex process owns this document's persisted thread."""




class CodexEngine(TranslationEngine):
    name = "Codex · Luna"
    supports_session_reset = True
    requires_connection = True
    MODEL = "gpt-6-luna"

    def __init__(self, executable: str = "", timeout: float = 90, sessions_path: Path | None = None,
                 fast_updates: bool = False, timings_path: Path | None = None):
        self.rpc = CodexRPC(executable, timeout, fast_mode=fast_updates)
        self._connected = False
        self._lock = threading.Lock()
        self.sessions = DocumentSessions(sessions_path)
        self._loaded_threads: dict[str, str] = {}
        self.fast_updates = fast_updates
        self._fast_tier: str | None = None
        self.timings_path = timings_path
        self.last_timing: dict = {}
        self._active_timing: dict | None = None
        self._generation_started = 0.0
        self._active_turn = None

    def begin_operation(self):
        super().begin_operation()
        self.rpc.cancel_event = self._cancel_event

    def connection_label(self) -> str:
        if self.fast_updates:
            speed = "后续 Fast" if self._fast_tier else "Fast 不可用"
        else:
            speed = "标准速度"
        return f"Codex · Luna low · {speed}"

    def session_for(self, document_id: str) -> str | None:
        return self.sessions.get(document_id)

    def connect(self) -> str:
        with self._lock:
            return self._connect()

    def _connect(self) -> str:
        if self._connected and self.rpc._process and self.rpc._process.poll() is None:
            return self.connection_label()
        self.rpc.start()
        self._loaded_threads.clear()
        self._fast_tier = None
        account = self.rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            self.rpc.close()
            raise TranslationError("Codex 尚未使用 ChatGPT 订阅登录。请先执行 codex login，再点击“翻译 / 更新”重试。")
        models = []
        cursor = None
        seen_cursors = set()
        while True:
            params = {"limit": 100, "includeHidden": True}
            if cursor:
                params["cursor"] = cursor
            page = self.rpc.request("model/list", params)
            models.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise TranslationError("Codex 模型列表分页异常，请重新连接。")
            seen_cursors.add(cursor)
        luna = next((m for m in models if m.get("model") == self.MODEL), None)
        if luna is None:
            self.rpc.close()
            raise TranslationError("当前 Codex 账号未提供 gpt-6-luna，未切换到其他模型。")
        # Use the advertised wire ID (currently 'priority'), not the UI name.
        self._fast_tier = next((tier["id"] for tier in luna.get("serviceTiers", [])
                                if isinstance(tier, dict) and isinstance(tier.get("id"), str)
                                and (tier.get("name", "").lower() == "fast"
                                     or tier["id"] in ("fast", "priority"))), None)
        self._connected = True
        return self.connection_label()

    def _thread_options(self) -> dict:
        return {
            "model": self.MODEL, "modelProvider": "openai",
            "approvalPolicy": "never", "sandbox": "read-only",
            "baseInstructions": INSTRUCTIONS,
            "config": {"features.shell_tool": False, "features.code_mode": False,
                       "features.multi_agent": False, "apps._default.enabled": False},
        }

    def _thread_for(self, document_id: str) -> str:
        if document_id in self._loaded_threads:
            return self._loaded_threads[document_id]
        existing = self.sessions.get(document_id)
        if existing:
            try:
                resumed = self.rpc.request("thread/resume", {
                    **self._thread_options(), "threadId": existing,
                })
                thread_id = resumed["thread"]["id"]
                if thread_id != existing:
                    raise TranslationError("Codex 恢复了错误的文档会话，已停止翻译。")
                self._loaded_threads[document_id] = existing
                return existing
            except TranslationError as error:
                message = str(error).lower()
                if "already has an active writer" in message:
                    raise SessionBusyError(
                        "文档的 Codex 会话被其他进程占用。关闭占用的会话后重试，"
                        "或点击“重建会话”；现有译文已保留。") from error
                missing = ("no rollout found" in message or "thread not found" in message
                           or "unknown thread" in message
                           or ("thread" in message and "does not exist" in message))
                if not missing:
                    raise
        return self._start_thread_for(document_id)

    def _start_thread_for(self, document_id: str) -> str:
        started = self.rpc.request("thread/start", {**self._thread_options(), "ephemeral": False})
        thread_id = started["thread"]["id"]
        self.sessions.bind(document_id, thread_id)
        self._loaded_threads[document_id] = thread_id
        return thread_id

    def recreate_session(self, document_id: str) -> str:
        """Explicit recovery: replace only the binding; never delete the old thread."""
        with self._lock:
            try:
                self._connect()
                return self._start_thread_for(document_id)
            except Exception:
                self._connected = False
                self._loaded_threads.clear()
                self.rpc.close()
                raise

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        self._check_cancelled()
        self._log_request(request)
        local = local_response(request)
        if local is not None:
            self.last_timing = {"mode": request.mode, "outcome": "local", "total_seconds": 0.0}
            self._log("本地排版或删除完成", response=local)
            return local
        with self._lock:
            started = time.monotonic()
            timing = {"mode": request.mode, "model": self.MODEL, "effort": "low",
                      "service_tier": "default", "prompt_characters": 0,
                      "connection_seconds": 0.0, "session_seconds": 0.0,
                      "submit_seconds": 0.0, "generation_seconds": 0.0,
                      "first_output_seconds": None, "reconnection_events": 0,
                      "total_seconds": 0.0, "outcome": "error"}
            self._active_timing = timing
            self._generation_started = 0.0
            try:
                try:
                    self._connect()
                    self._check_cancelled()
                finally:
                    timing["connection_seconds"] = time.monotonic() - started
                tier = "default"
                if request.mode in ("revise", "sentence") and self.fast_updates:
                    if not self._fast_tier:
                        raise TranslationError("当前 Luna 未提供 Fast 模式，请关闭 Fast 开关或换用其他引擎。")
                    tier = self._fast_tier
                timing["service_tier"] = tier
                while True:
                    try:
                        self.rpc.events.get_nowait()
                    except queue.Empty:
                        break
                phase = time.monotonic()
                try:
                    thread_id = self._thread_for(request.document_id)
                finally:
                    timing["session_seconds"] = time.monotonic() - phase
                prompt = build_prompt(request)
                timing["prompt_characters"] = len(prompt)
                phase = time.monotonic()
                try:
                    turn = self.rpc.request("turn/start", {
                        "threadId": thread_id,
                        "model": self.MODEL, "effort": "low",
                        # Per-turn override prevents inheriting a global/old Fast setting.
                        # Initial translation always stays at standard speed.
                        "serviceTierForTurn": tier,
                        "input": [{"type": "text", "text": prompt}],
                        "outputSchema": output_schema(request.mode),
                    })
                finally:
                    timing["submit_seconds"] = time.monotonic() - phase
                self._generation_started = time.monotonic()
                turn_id = turn["turn"]["id"]
                self._active_turn = (thread_id, turn_id)
                try:
                    raw = self._wait_turn(thread_id, turn_id)
                    self._check_cancelled()
                except TranslationCancelled:
                    try:
                        self.rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id},
                                         timeout=2, cancellable=False)
                    except TranslationError:
                        pass
                    raise
                finally:
                    self._active_turn = None
                    timing["generation_seconds"] = time.monotonic() - self._generation_started
                    self._generation_started = 0.0
                self._log("模型原始返回", raw=raw)
                response = self._parse(request, raw)
                self._log("返回解析成功", response=response)
                timing["outcome"] = "completed"
                return response
            except Exception as error:
                # A failed or timed-out generation must not continue on a reused transport.
                if isinstance(error, TranslationCancelled):
                    timing["outcome"] = "cancelled"
                self._connected = False
                self._loaded_threads.clear()
                self.rpc.close()
                raise
            finally:
                self._finish_timing(timing, started)

    def _finish_timing(self, timing: dict, started: float):
        timing["total_seconds"] = time.monotonic() - started
        timing = {key: round(value, 3) if isinstance(value, float) else value
                  for key, value in timing.items()}
        self.last_timing = timing
        self._log("请求耗时", timing=timing)
        self._active_timing = None
        if self.timings_path:
            try:
                self.timings_path.parent.mkdir(parents=True, exist_ok=True)
                with self.timings_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(timing, ensure_ascii=False) + "\n")
            except OSError:
                # Diagnostics must never make a valid translation fail.
                pass

    def _note_output(self):
        if self._active_timing is not None and self._active_timing["first_output_seconds"] is None:
            self._active_timing["first_output_seconds"] = time.monotonic() - self._generation_started

    def _wait_turn(self, thread_id: str, turn_id: str) -> str:
        deadline = time.monotonic() + self.rpc.timeout
        texts = {}
        deltas = {}
        last_error = ""
        while time.monotonic() < deadline:
            self._check_cancelled()
            try:
                event = self.rpc.events.get(timeout=min(1, max(0.01, deadline-time.monotonic())))
            except queue.Empty:
                continue
            method = event.get("method")
            params = event.get("params", {})
            if method == "client/disconnected":
                raise TranslationError("Codex 进程退出，原译文已保留，请重试。")
            if params.get("threadId") != thread_id:
                continue
            if method.startswith("item/") and params.get("turnId") != turn_id:
                continue
            if method == "item/agentMessage/delta":
                if params.get("delta"):
                    self._note_output()
                item_id = params.get("itemId", "message")
                deltas[item_id] = deltas.get(item_id, "") + params.get("delta", "")
            elif method == "error":
                last_error = (params.get("error") or {}).get("message", "")
                if self._active_timing is not None and "reconnecting" in last_error.lower():
                    self._active_timing["reconnection_events"] += 1
            elif method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    if item.get("text"):
                        self._note_output()
                    texts[item.get("id", "message")] = item.get("text", "")
            elif method == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                turn = params["turn"]
                if turn.get("status") != "completed":
                    error = turn.get("error") or {}
                    raise TranslationError(error.get("message", "Codex 翻译中断，请重试。"))
                raw = next(reversed(texts.values()), "") if texts else next(reversed(deltas.values()), "")
                if not raw:
                    raise TranslationError("Codex 没有返回译文。")
                return raw
        try:
            self.rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=2)
        except TranslationError:
            pass
        reason = f"（{last_error}）" if last_error else ""
        raise TranslationError(f"Luna 翻译超时{reason}，原译文已保留，请重试。")

    @staticmethod
    def _parse(request: TranslationRequest, raw: str) -> TranslationResponse:
        return parse_response(request, raw, "Luna")

    def close(self) -> None:
        self._connected = False
        self._loaded_threads.clear()
        self.rpc.close()

    def cancel(self):
        super().cancel()
        if self._active_turn is not None:
            return  # The worker interrupts the turn before closing its transport.
        # Terminate only this application's app-server; cleanup stays on the worker.
        process = self.rpc._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
