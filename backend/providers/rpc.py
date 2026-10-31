"""Small stdlib JSON-RPC client for a local `codex app-server` process."""

import itertools
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time

from .base import TranslationCancelled, TranslationError


def discover_codex() -> str:
    command = shutil.which("codex.exe") or shutil.which("codex")
    if command:
        return command
    home = Path.home()
    candidates = [
        home / ".codex" / ".sandbox-bin" / "codex.exe",
        home / "AppData/Local/Programs/Codex/resources/codex.exe",
        home / "AppData/Local/Programs/Codex/resources/bin/codex.exe",
        home / ".local/bin/codex",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


class CodexRPC:
    def __init__(self, executable: str = "", timeout: float = 90, fast_mode: bool = False):
        self.executable = executable or discover_codex()
        self.timeout = timeout
        self.fast_mode = fast_mode
        self._process = None
        self._counter = itertools.count(1)
        self._pending: dict[int, queue.Queue] = {}
        self.events: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader = None
        self.cancel_event = None

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.executable:
            raise TranslationError("未找到 Codex，请在设置中选择 codex.exe。")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        # Translation does not need the user's coding MCP servers. Disable them
        # for this subprocess only; never edit their global Codex configuration.
        overrides = {"features.shell_tool": False, "features.code_mode": False,
                     "features.multi_agent": False, "apps._default.enabled": False,
                     "features.fast_mode": self.fast_mode}
        try:
            config_text = (Path.home() / ".codex/config.toml").read_text(encoding="utf-8")
            names = set(re.findall(r"^\[mcp_servers\.([A-Za-z0-9_-]+)(?:\.|\])", config_text, re.MULTILINE))
            overrides.update({f"mcp_servers.{name}.enabled": False for name in names})
        except OSError:
            pass
        command = [self.executable]
        for key, value in overrides.items():
            command.extend(["-c", f"{key}={str(value).lower()}"])
        command.extend(["app-server", "--stdio"])
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                text=True, bufsize=1, creationflags=flags,
            )
        except OSError as error:
            raise TranslationError(f"无法启动 Codex：{error}") from error
        self.events = queue.Queue()
        self._reader = threading.Thread(target=self._read, args=(self._process,), daemon=True)
        self._reader.start()
        try:
            self.request("initialize", {
                "clientInfo": {"name": "bilingual_desktop", "title": "翻译对照", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def _read(self, process) -> None:
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if "id" in message and "method" not in message:
                    with self._lock:
                        destination = self._pending.get(message["id"])
                    if destination:
                        destination.put(message)
                elif "id" in message:
                    # This application never grants tools or handles tool input.
                    self._send({"id": message["id"], "error": {
                        "code": -32601, "message": "Translation client does not support tool requests"}})
                else:
                    self.events.put(message)
        except (OSError, ValueError):
            pass
        finally:
            failure = {"error": {"message": "Codex 进程已退出，请重新连接。"}}
            if process is self._process:
                with self._lock:
                    destinations = list(self._pending.values())
                for destination in destinations:
                    destination.put(failure)
                self.events.put({"method": "client/disconnected"})

    def _send(self, payload: dict) -> None:
        with self._write_lock:
            if self._process is None or self._process.poll() is not None:
                raise TranslationError("Codex 未连接，请重新连接。")
            try:
                self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except (OSError, ValueError) as error:
                raise TranslationError("Codex 连接已断开，请重新连接。") from error

    def request(self, method: str, params: dict | None = None, timeout: float | None = None,
                cancellable: bool = True) -> dict:
        if cancellable and self.cancel_event is not None and self.cancel_event.is_set():
            raise TranslationCancelled("已取消，现有译文已保留。")
        request_id = next(self._counter)
        destination: queue.Queue = queue.Queue()
        with self._lock:
            self._pending[request_id] = destination
        try:
            self._send({"id": request_id, "method": method, "params": params or {}})
            try:
                deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
                while True:
                    if cancellable and self.cancel_event is not None and self.cancel_event.is_set():
                        raise TranslationCancelled("已取消，现有译文已保留。")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    try:
                        response = destination.get(timeout=min(0.1, remaining))
                        break
                    except queue.Empty:
                        continue
            except queue.Empty as error:
                raise TranslationError(f"Codex 请求超时（{method}），请重试。") from error
            if "error" in response:
                raise TranslationError(str(response["error"].get("message", "Codex 请求失败")))
            return response.get("result", {})
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if self._reader:
            self._reader.join(timeout=0.5)
        if process.stdin:
            process.stdin.close()
        # A coding plugin child may still hold an inherited stdout handle.
        # Closing a TextIOWrapper during a blocked readline can deadlock.
        if process.stdout and (not self._reader or not self._reader.is_alive()):
            process.stdout.close()
        self._process = None
