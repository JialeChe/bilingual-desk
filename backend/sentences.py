"""Expand edits to aligned complete sentences and replace their translations."""

from dataclasses import replace
import re

from .layout import edges, layout_only, sync_layout
from .language import untranslated_ids
from .markdown import analyze_markdown
from .models import Segment, SentenceUpdate
from .translation import PatchError


def sentence_boundaries(text):
    """Sentence ends and Markdown block boundaries, including following whitespace.

    Commas, semicolons, soft line wraps and arbitrary character limits never
    terminate a sentence. Keep decimals, common abbreviations and code intact.
    """
    blocks, styles = analyze_markdown(text)
    protected = [(b.start, b.end) for b in blocks if b.kind == "code"]
    protected += [(s.start, s.end) for s in styles if s.style == "inline_code"]
    ends = {0, len(text)}
    for block in blocks:
        ends.update((block.start, block.end))
    pattern = r'''[。！？]+[”’"'）)\]]*|[.!?]+[”’"')\]]*(?=\s|$)'''
    for match in re.finditer(pattern, text):
        if any(a <= match.start() < b for a, b in protected):
            continue
        if match.group() == ".":
            word = re.search(r"([A-Za-z.]+)$", text[:match.start()])
            if word and (word[1].lower() in {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "e.g", "i.e"}
                         or (len(word[1]) == 1 and word[1].isupper())):
                continue
        ends.add(match.end())
    # Old alignment may keep whitespace either with the sentence or separately.
    for end in tuple(ends):
        while end < len(text) and text[end].isspace():
            end += 1
            ends.add(end)
    return ends


def prepare_sentences(request, protected_ids=()):
    if request is None or request.mode != "revise":
        return request
    old_ends = sentence_boundaries(request.source_before)
    new_ends = sentence_boundaries(request.source_after)
    updated = {s.id: s for s in request.updated_sources}
    affected = set(request.affected_ids)
    repairs = set(request.repair_ids)
    sentences, group = [], []
    old_offset = new_offset = 0

    def flush():
        if any(s.id in affected for s in group):
            if (any(s.id in protected_ids for s in group)
                    and all(s.source == updated[s.id].source for s in group)):
                group.clear()
                return  # Defer automatic repair of an opposite-side manual edit.
            before = "".join(s.source for s in group)
            after = "".join(updated[s.id].source for s in group)
            target = "".join(s.target for s in group)
            needs_translation = bool(after.strip()) and (
                not layout_only(before, after) or any(s.id in repairs for s in group))
            sentences.append(SentenceUpdate(tuple(s.id for s in group), before, after, target, needs_translation))
        group.clear()

    for unit in request.segments:
        group.append(unit)
        old_offset += len(unit.source)
        new_offset += len(updated[unit.id].source)
        if old_offset in old_ends and new_offset in new_ends:
            flush()
    if group:
        flush()
    expanded = {sid for sentence in sentences for sid in sentence.segment_ids}
    return replace(request, mode="sentence", sentences=tuple(sentences),
                   affected_ids=tuple(s.id for s in request.segments if s.id in expanded))


def apply_sentences(document, request, response):
    """Validate complete sentence results before changing the document."""
    if (document.id != request.document_id or document.revision != request.revision
            or document.source != request.source_after
            or response.document_id != request.document_id or response.revision != request.revision):
        return False
    expected = {s.id for s in request.sentences if s.needs_translation}
    if response.patches or response.paragraphs or set(response.translations) != expected:
        raise PatchError("整句翻译返回的句子不完整或包含额外内容，现有内容已保留，请重试。",
                         expected_ids=sorted(expected), received_ids=sorted(response.translations))
    replacements, consumed = {}, set()
    for sentence in request.sentences:
        if sentence.needs_translation:
            target = response.translations[sentence.id]
            if not isinstance(target, str) or not target.strip():
                raise PatchError("整句翻译返回了空内容，现有内容已保留，请重试。", segment_id=sentence.id)
            # Preserve existing target edges (including English inter-sentence
            # spaces), then mirror only boundary whitespace changed by the user.
            leading, _, trailing = edges(sentence.target_text)
            if not sentence.target_text:
                leading, _, trailing = edges(sentence.source_after)
            target = sync_layout(sentence.source_before, sentence.source_after, leading + target.strip() + trailing)
        elif not sentence.source_after.strip():
            target = sentence.source_after
        else:
            target = sync_layout(sentence.source_before, sentence.source_after, sentence.target_text)
        replacements[sentence.id] = Segment(sentence.id, sentence.source_after, target)
        consumed.update(sentence.segment_ids[1:])
    result = [replacements.get(s.id, s) for s in request.updated_sources if s.id not in consumed]
    if "".join(s.source for s in result) != request.source_after:
        raise PatchError("整句对应关系不完整，现有内容已保留。")
    copied = untranslated_ids(result, request.target_language)
    if expected.intersection(copied):
        raise PatchError("翻译引擎原样返回了待翻译句子，现有内容已保留，请重试。")
    document.segments = [s for s in result if s.source or s.target]
    document.baseline = request.source_after
    return True
