from abc import ABC, abstractmethod
from uuid import uuid4
import threading

from backend.models import TranslationRequest, TranslationResponse


class TranslationError(RuntimeError):
    """An actionable connection, model, or translation error."""


class TranslationCancelled(TranslationError):
    """The user stopped an operation; its response must never be committed."""


class TranslationEngine(ABC):
    """Shared parent for local Codex and future API providers.

    Authentication and transport stay inside the concrete provider.
    Providers propose edits; the document service validates and applies them.
    """

    name = "翻译引擎"
    fast_updates = False
    supports_session_reset = False
    requires_connection = False
    diagnostics = None

    def begin_operation(self):
        self._cancel_event = threading.Event()
        self.progress = None

    def translate_document(self, request):
        from backend.batching import translate_document
        return translate_document(self, request)

    def cancel(self):
        event = getattr(self, "_cancel_event", None)
        if event is not None:
            event.set()

    def _check_cancelled(self):
        event = getattr(self, "_cancel_event", None)
        if event is not None and event.is_set():
            raise TranslationCancelled("已取消，原文和现有译文已保留。")

    def set_diagnostics(self, diagnostics):
        self.diagnostics = diagnostics
        diagnostics.register_secret(getattr(self, "_api_key", ""))

    def _log_request(self, request):
        if self.diagnostics is None:
            return
        self._diagnostic_context = {"attempt": uuid4().hex[:12], "provider": self.name,
                                    "model": self.MODEL, "document_id": request.document_id,
                                    "revision": request.revision, "mode": request.mode}
        from .protocol import build_prompt
        self._log("结构初始化请求" if request.mode == "structure" else "翻译请求", prompt=build_prompt(request))

    def _log(self, event, level="INFO", **details):
        if self.diagnostics is not None:
            self.diagnostics.record(event, level, **getattr(self, "_diagnostic_context", {}), **details)

    def connection_label(self) -> str:
        return f"{self.name} 已连接" if self.requires_connection else self.name

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        raise NotImplementedError

    def connect(self) -> str:
        return "翻译引擎已就绪"

    def close(self) -> None:
        pass
