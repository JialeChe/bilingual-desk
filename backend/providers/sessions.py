"""Document-to-Codex-thread bindings. Only identifiers are stored here."""

import json
from pathlib import Path

from backend.storage import write_json
from .base import TranslationError


class DocumentSessions:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.threads: dict[str, str] = {}
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                bindings = data["documents"]
                if (data.get("format_version") != 1 or not isinstance(bindings, dict)
                        or any(not isinstance(k, str) or not isinstance(v, str) or not v
                               for k, v in bindings.items())):
                    raise ValueError("invalid session bindings")
                self.threads = bindings
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                raise TranslationError("文档会话记录无法读取，原文件已保留，请检查 codex_sessions.json。") from error

    def get(self, document_id: str) -> str | None:
        return self.threads.get(document_id)

    def bind(self, document_id: str, thread_id: str):
        updated = {**self.threads, document_id: thread_id}
        if self.path is not None:
            try:
                write_json(self.path, {"format_version": 1, "documents": updated})
            except OSError as error:
                raise TranslationError("文档会话关联保存失败，请检查本地目录的写入权限。") from error
        self.threads = updated
