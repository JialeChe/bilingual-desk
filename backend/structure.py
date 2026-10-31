"""Validate model-selected boundaries and add only paragraph newlines."""

import re

from .markdown import analyze_markdown
from .providers.base import TranslationError

KINDS = ("heading", "paragraph", "list", "quote", "code")


def eligible_starts(request) -> set[str]:
    """Candidate chunks may end mid-sentence; those are not paragraph boundaries."""
    blocks, spans = analyze_markdown(request.source_after)
    protected = [(b.start, b.end) for b in blocks if b.kind == "code"]
    protected += [(s.start, s.end) for s in spans if s.style == "inline_code"]
    eligible, tail, first, offset = set(), "", True, 0
    for segment in request.segments:
        if segment.source.strip():
            if first or re.search(r"(?:[\r\n]|[.!?。！？][\"'”’)\]]*)\s*$", tail):
                if not any(a < offset < b for a, b in protected):
                    eligible.add(segment.id)
            first = False
        tail = (tail + segment.source)[-256:]
        offset += len(segment.source)
    return eligible


def initialize_source(request, response) -> str:
    if response.translations or response.patches or not response.paragraphs:
        raise TranslationError("结构初始化没有返回有效段落，原文已保留，请重试。")
    positions = {s.id: i for i, s in enumerate(request.segments) if s.source.strip()}
    starts = [positions.get(p.segment_id, -1) for p in response.paragraphs]
    if (any(p.kind not in KINDS for p in response.paragraphs) or -1 in starts
            or starts != sorted(set(starts)) or starts[0] != min(positions.values())):
        raise TranslationError("结构初始化返回了重复、乱序或无效的段落边界，原文已保留。")
    blocks, spans = analyze_markdown(request.source_after)
    offsets, offset = [], 0
    for segment in request.segments:
        offsets.append(offset)
        offset += len(segment.source)
    if offset != len(request.source_after) or "".join(s.source for s in request.segments) != request.source_after:
        raise TranslationError("结构初始化的原文快照不完整，原文已保留。")
    # Never split Markdown code fences, even if the model proposes doing so.
    if any(b.kind == "code" and b.start < offsets[i] < b.end for b in blocks for i in starts[1:]):
        raise TranslationError("结构初始化试图拆分代码块，原文已保留，请重试。")
    if any(s.style == "inline_code" and s.start < offsets[i] < s.end for s in spans for i in starts[1:]):
        raise TranslationError("结构初始化试图拆分行内代码，原文已保留，请重试。")
    eligible = eligible_starts(request)
    if any(p.segment_id not in eligible for p in response.paragraphs):
        raise TranslationError("结构初始化试图从句子中间分段，原文已保留，请重试。")
    newline = "\r\n" if "\r\n" in request.source_after else "\n"
    selected = set(starts[1:])
    pieces, tail = [], ""
    for index, segment in enumerate(request.segments):
        if index in selected:
            leading = re.match(r"\s*", segment.source).group()
            count = len(re.findall(r"\r\n|\r|\n", tail + leading))
            added = newline * max(0, 2 - count)
            pieces.append(added)
            tail += added
        pieces.append(segment.source)
        tail = re.search(r"\s*$", segment.source).group() if segment.source.strip() else tail + segment.source
    return "".join(pieces)
