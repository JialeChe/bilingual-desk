"""Bound initial output size and retain validated batches for same-document retries."""

from dataclasses import replace
from hashlib import sha256
import json
import time

from .markdown import analyze_markdown
from .models import Document, ParagraphStart, TranslationResponse
from .structure import eligible_starts, initialize_source
from .translation import apply_response
from .providers.base import TranslationCancelled, TranslationError


def batches(request, max_chars=1800, max_units=60):
    if request.mode not in ("initial", "structure"):
        return [request]
    blocks, _ = analyze_markdown(request.source_after)
    kinds, position, block_index = {}, 0, 0
    for unit in request.segments:
        while block_index < len(blocks)-1 and position >= blocks[block_index].end:
            block_index += 1
        kinds[unit.id] = blocks[block_index].kind if blocks else "paragraph"
        position += len(unit.source)
    allowed = eligible_starts(request) if request.mode == "structure" else None
    groups, group, size = [], [], 0
    for unit in request.segments:
        if group and (size + len(unit.source) > max_chars or len(group) >= max_units):
            if allowed is None or unit.id in allowed:
                groups.append(tuple(group))
                group, size = [], 0
        group.append(unit)
        size += len(unit.source)
    if group:
        groups.append(tuple(group))
    result, offset = [], 0
    for group in groups:
        source = "".join(s.source for s in group)
        result.append(replace(request, source_after=source, segments=group,
                              unit_kinds=tuple((s.id, kinds[s.id]) for s in group),
                              context_before=request.source_after[max(0, offset-300):offset],
                              context_after=request.source_after[offset+len(source):offset+len(source)+300]))
        offset += len(source)
    return result or [request]


def translate_document(engine, request):
    if request.mode not in ("initial", "structure"):
        return engine.translate(request)
    chunks = batches(request)
    identity = json.dumps([request.document_id, request.revision, request.mode, request.source_language,
                           request.target_language, request.source_after, request.english_snapshot,
                           request.chinese_snapshot], ensure_ascii=False)
    key = sha256(identity.encode("utf-8")).hexdigest()
    if getattr(engine, "_batch_cache_key", None) != key:
        engine._batch_cache_key, engine._batch_cache = key, {}
    cache = engine._batch_cache
    translations, paragraphs = {}, []
    started = time.monotonic()
    label = "整理段落" if request.mode == "structure" else "首次翻译"
    for index, chunk in enumerate(chunks):
        engine._check_cancelled()
        engine.progress = (label, index, len(chunks))
        if index not in cache:
            try:
                # With no eligible internal boundary there is nothing to organize.
                if chunk.mode == "structure" and len(eligible_starts(chunk)) == 1:
                    first = next(s for s in chunk.segments if s.source.strip())
                    response = TranslationResponse(chunk.document_id, chunk.revision,
                        paragraphs=(ParagraphStart(first.id, dict(chunk.unit_kinds).get(first.id, "paragraph")),))
                else:
                    response = engine.translate(chunk)
                engine._check_cancelled()
                if chunk.mode == "initial":
                    trial = Document(id=chunk.document_id, revision=chunk.revision, source=chunk.source_after)
                    if not apply_response(trial, chunk, response):
                        raise TranslationError("分批结果已经过期。")
                    cache[index] = [response.translations.get(s.id) for s in chunk.segments]
                else:
                    initialize_source(chunk, response)
                    indexes = {s.id: i for i, s in enumerate(chunk.segments)}
                    cache[index] = [(indexes[p.segment_id], p.kind) for p in response.paragraphs]
            except TranslationCancelled:
                raise
            except Exception as error:
                raise TranslationError(f"{label}第 {index+1}/{len(chunks)} 批失败；已完成 {index} 批。"
                                       f"保持内容不变后重试可接着完成。{error}") from error
        if chunk.mode == "initial":
            translations.update({s.id: value for s, value in zip(chunk.segments, cache[index]) if value is not None})
        else:
            paragraphs.extend(ParagraphStart(chunk.segments[i].id, kind) for i, kind in cache[index])
        engine.progress = (label, index+1, len(chunks))
    engine._check_cancelled()
    engine.last_timing = {**engine.last_timing, "total_seconds": round(time.monotonic()-started, 3),
                          "batches": len(chunks), "outcome": "completed"}
    return TranslationResponse(request.document_id, request.revision, translations=translations,
                               paragraphs=tuple(paragraphs))
