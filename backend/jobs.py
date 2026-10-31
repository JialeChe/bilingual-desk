"""One background worker; UI reads results from a queue on its own thread."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import queue
from typing import Any, Callable
from .providers.base import TranslationCancelled


@dataclass(frozen=True)
class JobResult:
    kind: str
    context: Any
    value: Any = None
    error: Exception | None = None


class JobRunner:
    def __init__(self, diagnostics=None):
        self.diagnostics = diagnostics
        self.results: queue.Queue[JobResult] = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translation")

    def submit(self, kind: str, context: Any, action: Callable):
        def work():
            details = {"kind": kind, "document_id": getattr(context, "document_id", None),
                       "revision": getattr(context, "revision", None)}
            if self.diagnostics is not None:
                self.diagnostics.record("后台任务开始", **details)
            try:
                result = JobResult(kind, context, value=action())
                if self.diagnostics is not None:
                    self.diagnostics.record("后台任务完成", **details)
            except Exception as error:
                result = JobResult(kind, context, error=error)
                if self.diagnostics is not None:
                    if isinstance(error, TranslationCancelled):
                        self.diagnostics.record("后台任务已取消", **details)
                    else:
                        self.diagnostics.error("后台任务失败", error, **details)
            self.results.put(result)
        self._executor.submit(work)

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
