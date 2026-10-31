"""Versioned local JSON files; no database."""

import json
import os
from pathlib import Path
import tempfile

from .models import Document, Segment


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def save_document(path: Path, document: Document) -> None:
    write_json(path, document.to_dict())


def load_document(path: Path) -> Document:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("文档应为 JSON 对象。")
    if data.get("format_version") not in (1, 2):
        raise ValueError("无法识别文档版本。")
    segments = [Segment(**value) for value in data.get("segments", [])]
    document = Document(id=data["id"], title=data["title"], source=data["source"],
                        baseline=data["baseline"], revision=data["revision"], segments=segments,
                        needs_structure=data.get("needs_structure", False),
                        structure_source=data.get("structure_source", ""),
                        target_text=data.get("target_text"))
    if document.target_text is not None and not isinstance(document.target_text, str):
        raise ValueError("中文内容格式不正确。")
    if type(document.needs_structure) is not bool or not isinstance(document.structure_source, str):
        raise ValueError("文档初始化状态格式不正确。")
    if not all(isinstance(value, str) for value in (document.id, document.title, document.source, document.baseline)):
        raise ValueError("文档内容格式不正确。")
    if type(document.revision) is not int or document.revision < 0:
        raise ValueError("文档版本号不正确。")
    if any(not all(isinstance(v, str) for v in (s.id, s.source, s.target)) for s in segments):
        raise ValueError("文档片段格式不正确。")
    if len({s.id for s in segments}) != len(segments):
        raise ValueError("文档片段标识重复。")
    if "".join(s.source for s in segments) != document.baseline:
        raise ValueError("文档的中英文对应关系不完整。")
    return document
