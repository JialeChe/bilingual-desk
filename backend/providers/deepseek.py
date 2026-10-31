"""Official DeepSeek API, with thinking explicitly disabled and pooled HTTP."""

import asyncio
import json
import os
from pathlib import Path
import threading
import time

import httpx

from backend.models import TranslationRequest, TranslationResponse
from .base import TranslationCancelled, TranslationEngine, TranslationError
from .protocol import INSTRUCTIONS, build_prompt, local_response, output_schema, parse_response


class DeepSeekEngine(TranslationEngine):
    name = "DeepSeek · Flash"
    MODEL = "deepseek-flash"
    BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str = "", timeout: float = 90,
                 timings_path: Path | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self._api_key = (api_key or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        self.timeout = timeout
        self.timings_path = timings_path
        self.last_timing: dict = {}
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._task = None
        self._closing = threading.Event()
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, transport=transport,
                                    timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                                    follow_redirects=False)

    def connection_label(self) -> str:
        return "DeepSeek · Flash · 关闭思考"

    def _headers(self) -> dict:
        if not self._api_key:
            raise TranslationError("请在设置中填写 DeepSeek API Key，或设置 DEEPSEEK_API_KEY 环境变量。")
        if "\n" in self._api_key or "\r" in self._api_key:
            raise TranslationError("DeepSeek API Key 包含换行，请重新填写。")
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _check_status(response: httpx.Response):
        if response.status_code == 200:
            return
        reasons = {400: "请求参数被拒绝，请检查模型是否支持当前接口",
                   401: "API Key 无效，请在设置中重新填写",
                   402: "余额不足，请检查 DeepSeek API 账户余额",
                   403: "API 访问被拒绝，请检查账号权限",
                   429: "请求限流，请稍后重试",
                   500: "服务异常，请稍后重试", 503: "服务繁忙，请稍后重试"}
        # Never display server bodies: they can echo credentials or document text.
        reason = reasons.get(response.status_code, f"请求失败（HTTP {response.status_code}）")
        raise TranslationError(f"DeepSeek {reason}；原译文已保留。")

    def connect(self) -> str:
        # Kept as a harmless common-interface method; APIs have no handshake.
        return self.connection_label()

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
            timing = {"mode": request.mode, "model": self.MODEL, "effort": "none",
                      "thinking": "disabled", "prompt_characters": 0,
                      "first_output_seconds": None,
                      "generation_seconds": 0.0, "total_seconds": 0.0, "outcome": "error"}
            try:
                prompt = build_prompt(request)
                timing["prompt_characters"] = len(prompt)
                body = {"model": self.MODEL,
                        "thinking": {"type": "disabled"}, "reasoning_effort": "none",
                        "stream": True, "max_tokens": 8192, "response_format": {"type": "json_object"},
                        "messages": [{"role": "system", "content": INSTRUCTIONS
                                      + "\nReturn JSON matching this schema:\n"
                                      + json.dumps(output_schema(request.mode), separators=(",", ":"))},
                                     {"role": "user", "content": prompt}]}
                generation_started = time.monotonic()
                try:
                    self._task = self._loop.create_task(self._generate(body, timing, generation_started))
                    try:
                        raw = self._loop.run_until_complete(self._task)
                    except asyncio.CancelledError as error:
                        raise TranslationCancelled("已取消，原文和现有译文已保留。") from error
                    finally:
                        self._task = None
                    self._check_cancelled()
                finally:
                    timing["generation_seconds"] = time.monotonic() - generation_started
                self._log("模型原始返回", raw=raw)
                response = parse_response(request, raw, "DeepSeek")
                self._log("返回解析成功", response=response)
                timing["outcome"] = "completed"
                return response
            except TranslationCancelled:
                timing["outcome"] = "cancelled"
                raise
            except httpx.TimeoutException as error:
                raise TranslationError("DeepSeek 请求超时，原译文已保留，请重试。") from error
            except httpx.HTTPError as error:
                raise TranslationError("DeepSeek 连接中断，原译文已保留，请重试。") from error
            finally:
                timing["total_seconds"] = time.monotonic() - started
                self.last_timing = {key: round(value, 3) if isinstance(value, float) else value
                                    for key, value in timing.items()}
                self._log("请求耗时", timing=self.last_timing)
                if self.timings_path:
                    try:
                        self.timings_path.parent.mkdir(parents=True, exist_ok=True)
                        with self.timings_path.open("a", encoding="utf-8") as output:
                            output.write(json.dumps(self.last_timing) + "\n")
                    except OSError:
                        pass
                if self._closing.is_set():
                    self._close_transport()

    async def _generate(self, body: dict, timing: dict, started: float) -> str:
        self._check_cancelled()
        parts = []
        finished = None
        done = False
        async with self._client.stream("POST", "/chat/completions", headers=self._headers(), json=body) as response:
            self._check_cancelled()
            self._log("HTTP 响应", status_code=response.status_code)
            self._check_status(response)
            async for line in response.aiter_lines():
                self._check_cancelled()
                if time.monotonic() - started > self.timeout:
                    raise TranslationError("DeepSeek 翻译超时，原译文已保留，请重试。")
                if not line.startswith("data:"):
                    continue  # Empty lines and keep-alive comments are not output.
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                try:
                    chunk = json.loads(data)
                    if not isinstance(chunk, dict) or "error" in chunk:
                        raise ValueError("stream error")
                    choices = chunk["choices"]
                    if not choices:
                        continue
                    choice = choices[0]
                    content = choice.get("delta", {}).get("content")
                    if content is not None and not isinstance(content, str):
                        raise ValueError("invalid content")
                    if content:
                        if timing["first_output_seconds"] is None:
                            timing["first_output_seconds"] = time.monotonic() - started
                        parts.append(content)
                    finished = choice.get("finish_reason") or finished
                except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
                    self._log("流格式异常", "ERROR", chunk=data, partial_output="".join(parts))
                    raise TranslationError("DeepSeek 返回的流格式异常，原译文已保留，请重试。") from error
        if not done or finished != "stop":
            self._log("流返回未完成", "ERROR", done=done, finish_reason=finished, partial_output="".join(parts))
            raise TranslationError("DeepSeek 返回中断或超出输出长度，未写入部分译文，请重试。")
        raw = "".join(parts)
        if not raw.strip():
            raise TranslationError("DeepSeek 没有返回译文，请重试。")
        return raw

    def close(self):
        self._closing.set()
        self.cancel()
        if self._lock.acquire(blocking=False):
            try:
                self._close_transport()
            finally:
                self._lock.release()

    def _close_transport(self):
        if not self._loop.is_closed():
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.run_until_complete(self._client.aclose())
            self._loop.close()

    def cancel(self):
        super().cancel()
        task = self._task
        if task is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # A concurrent close may have already stopped the loop.
