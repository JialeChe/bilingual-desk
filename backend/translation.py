"""Prepare source diffs and apply bounded Chinese patches transactionally."""

from dataclasses import replace
import re
from uuid import uuid4

from .models import Document, Segment, SourceEdit, TargetPatch, TranslationRequest, TranslationResponse
from .diff import source_opcodes
from .language import untranslated_ids
from .layout import layout_only, restore_edges, sync_layout
from .structure import initialize_source
from .providers.base import TranslationError


class PatchError(TranslationError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def split_source(source: str) -> tuple[Segment, ...]:
    """Use clauses, not paragraphs, as alignment anchors. Preserve whitespace."""
    ends = [match.end() for match in re.finditer(r"[。！？；，][ \t]*|[.!?;,](?=\s|$)[ \t]*|\n", source)]
    if not ends or ends[-1] != len(source):
        ends.append(len(source))
    segments = []
    start = 0
    for end in ends:
        piece = source[start:end]
        start = end
        while len(piece) > 160:
            boundary = piece.rfind(" ", 0, 161)
            boundary = boundary + 1 if boundary > 0 else 160
            segments.append(Segment(uuid4().hex[:12], piece[:boundary]))
            piece = piece[boundary:]
        if piece:
            segments.append(Segment(uuid4().hex[:12], piece, piece if not piece.strip() else ""))
    return tuple(segments)


def source_edits(before: str, after: str) -> tuple[SourceEdit, ...]:
    return tuple(
        SourceEdit(a, b, before[a:b], after[c:d])
        for kind, a, b, c, d in source_opcodes(before, after)
        if kind != "equal"
    )


def project_sources(segments: tuple[Segment, ...], before: str, after: str):
    if "".join(s.source for s in segments) != before:
        raise PatchError("文档的中英文对应关系不完整，请重新打开已保存文档。")
    if before == after:
        return segments, ()
    spans = []
    offset = 0
    for segment in segments:
        spans.append((offset, offset + len(segment.source), segment.id))
        offset += len(segment.source)
    rebuilt = {segment.id: "" for segment in segments}
    affected: set[str] = set()
    for kind, a, b, c, d in source_opcodes(before, after):
        if kind == "equal":
            for start, end, segment_id in spans:
                if max(a, start) < min(b, end):
                    rebuilt[segment_id] += before[max(a, start):min(b, end)]
            continue
        owners = [segment_id for start, end, segment_id in spans if max(a, start) < min(b, end)]
        if not owners:
            owners = [next((sid for start, end, sid in spans if start <= a < end), spans[-1][2])]
        affected.update(owners)
        rebuilt[owners[0]] += after[c:d]
    updated = tuple(replace(segment, source=rebuilt[segment.id]) for segment in segments)
    if "".join(s.source for s in updated) != after:
        raise PatchError("无法定位这次修改，原译文已保留。")
    return updated, tuple(s.id for s in segments if s.id in affected)


def prepare_request(document: Document, target_language="zh", protected_ids=()) -> TranslationRequest | None:
    repairs = tuple(sid for sid in untranslated_ids(document.segments, target_language) if sid not in protected_ids)
    if not document.pending and not repairs:
        return None
    if (document.needs_structure and not document.segments and document.source.strip()
            and document.source != document.structure_source):
        return TranslationRequest(document.id, document.revision, "structure", "", document.source,
                                  split_source(document.source))
    # Blank lines are not an existing translation. The first actual English
    # input still needs initial alignment, even if whitespace was synced first.
    if not document.segments or (not document.baseline.strip() and not document.target.strip()):
        return TranslationRequest(document.id, document.revision, "initial", "", document.source,
                                  split_source(document.source))
    segments = tuple(document.segments)
    updated, affected = project_sources(segments, document.baseline, document.source)
    affected = tuple(s.id for s in segments if s.id in affected or s.id in repairs)
    return TranslationRequest(document.id, document.revision, "revise", document.baseline,
                              document.source, segments, source_edits(document.baseline, document.source),
                              updated, affected, repairs)


def locate_patch(text: str, patch: TargetPatch) -> tuple[int, int, str]:
    """Exact anchors avoid relying on model-generated Chinese character offsets."""
    if text and not patch.before and not (patch.left or patch.right):
        raise PatchError("翻译引擎未说明新增中文的插入位置，原译文已保留；请点击“翻译 / 更新”重试。")
    matches = []
    for position in range(len(text) + 1):
        end = position + len(patch.before)
        if (text[position:end] == patch.before
                and (not patch.left or text[:position].endswith(patch.left))
                and (not patch.right or text[end:].startswith(patch.right))):
            matches.append(position)
    if len(matches) != 1:
        raise PatchError("中文修改位置不唯一或已过期，原译文已保留，请重试。",
                         segment_id=patch.segment_id, match_count=len(matches),
                         candidate_positions=matches[:100], patch=patch, current_target=text)
    start = matches[0]
    end = start + len(patch.before)
    before, after = patch.before, patch.after
    # Strip redundant unchanged characters even if the provider supplied them.
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    before, after = before[prefix:], after[prefix:]
    start += prefix
    suffix = 0
    while suffix < min(len(before), len(after)) and before[-1-suffix] == after[-1-suffix]:
        suffix += 1
    if suffix:
        before, after = before[:-suffix], after[:-suffix]
        end -= suffix
    return start, end, after


def _english_boundaries(segments, code_ids):
    """Chinese clauses have no boundary spaces; English words need separators."""
    result = list(segments)
    for index in range(len(result) - 1):
        left, right = result[index], result[index + 1]
        if left.id in code_ids or right.id in code_ids:
            continue
        if left.source.strip() == left.target.strip() and right.source.strip() == right.target.strip():
            continue  # Preserve copied identifiers, URLs and other literal text.
        if (re.search(r"[A-Za-z0-9.,;:!?\)\]\}" + '\"\'`' + r"]$", left.target)
                and re.match(r"[A-Za-z0-9\(\[" + '\"\'`' + r"]", right.target)):
            result[index] = replace(left, target=left.target + " ")
    return result


def apply_response(document: Document, request: TranslationRequest,
                   response: TranslationResponse) -> bool:
    """Validate everything before commit; stale or invalid results never overwrite text."""
    if (document.id != request.document_id or document.revision != request.revision
            or document.source != request.source_after
            or response.document_id != request.document_id or response.revision != request.revision):
        return False
    if request.mode == "structure":
        normalized = initialize_source(request, response)
        document.edit(normalized)
        document.structure_source = normalized
        return True
    if response.paragraphs:
        raise PatchError("翻译阶段返回了结构数据，原译文已保留。")
    if request.mode == "initial":
        expected = {s.id for s in request.segments if s.source.strip()}
        if set(response.translations) != expected or response.patches:
            raise PatchError("初次翻译返回的片段不完整，未写入文档。")
        result = []
        for segment in request.segments:
            target = response.translations.get(segment.id, segment.source)
            if not isinstance(target, str) or (segment.source.strip() and not target.strip()):
                raise PatchError("初次翻译返回了空片段，未写入文档。")
            target = restore_edges(segment.source, target)
            result.append(replace(segment, target=target))
    else:
        if response.translations:
            raise PatchError("修订需要返回修改对应的中文补丁，原译文已保留。")
        originals = {s.id: s for s in request.segments}
        updated = {s.id: s for s in request.updated_sources}
        grouped: dict[str, list] = {}
        for patch in response.patches:
            if (patch.segment_id not in request.affected_ids
                    or (layout_only(updated[patch.segment_id].source, originals[patch.segment_id].source)
                        and patch.segment_id not in request.repair_ids)):
                raise PatchError("模型尝试修改未受影响的中文，修改已拒绝。")
            segment = originals[patch.segment_id]
            # Fully removed text has a deterministic target. No model anchors
            # are needed to remove its translation; retain remaining whitespace.
            if not updated[patch.segment_id].source.strip():
                if patch.after.strip():
                    raise PatchError("模型尝试为已删除的英文新增中文，修改已拒绝。")
                continue
            location = locate_patch(segment.target, patch)
            grouped.setdefault(patch.segment_id, []).append(location)
        result = []
        for segment in request.updated_sources:
            old = originals[segment.id]
            edits = sorted(grouped.get(segment.id, []), key=lambda item: (item[0], item[1]))
            for previous, current in zip(edits, edits[1:]):
                if previous[1] > current[0] or previous[0] == current[0]:
                    raise PatchError("中文修改片段重叠，原译文已保留。")
            target = old.target
            for start, end, replacement in reversed(edits):
                target = target[:start] + replacement + target[end:]
            if segment.id in request.affected_ids and not segment.source.strip():
                target = segment.source
            elif segment.id in request.affected_ids:
                target = sync_layout(old.source, segment.source, target)
            if segment.source.strip() and not target.strip():
                raise PatchError("修改将清空仍有英文的译文，修改已拒绝。")
            if segment.source or target:
                result.append(replace(segment, target=target))
    code_ids = {sid for sid, kind in request.unit_kinds if kind == "code"}
    if request.mode == "initial" and request.target_language == "en":
        # Full-document validation also handles boundaries between batches.
        from .markdown import analyze_markdown
        blocks, _ = analyze_markdown(request.source_after)
        offset = block_index = 0
        for segment in result:
            while block_index < len(blocks)-1 and offset >= blocks[block_index].end:
                block_index += 1
            if blocks and blocks[block_index].kind == "code":
                code_ids.add(segment.id)
            offset += len(segment.source)
        result = _english_boundaries(result, code_ids)
    untranslated = tuple(sid for sid in untranslated_ids(result, request.target_language) if sid not in code_ids)
    if untranslated and (request.mode == "initial" or any(sid in request.affected_ids for sid in untranslated)):
        raise PatchError("翻译引擎原样返回了外语句子，未写入译文；请点击“翻译 / 更新”重试。")
    document.segments = result
    document.baseline = request.source_after
    if request.mode == "initial":
        document.needs_structure = False
        document.structure_source = ""
    return True
