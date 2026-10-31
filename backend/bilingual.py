"""Synchronize independently edited English and Chinese against paired baselines.

Persisted source/target fields retain their historical English/Chinese meaning.
Requests are oriented to the edited side; commits advance only that side's units.
"""

from dataclasses import replace

from .models import Document, Segment
from .language import untranslated_ids
from .translation import PatchError, project_sources
from .translation import prepare_request as prepare_one_way, apply_response as apply_one_way
from .sentences import prepare_sentences, apply_sentences


class ConflictingEdits(PatchError):
    def __init__(self, ids=()):
        super().__init__("同一句的中英文都有修改，请选择这一句以哪边为准。")
        self.ids = tuple(ids)


def projections(document):
    """Project both current texts onto their common alignment without committing."""
    units = tuple(document.segments)
    reverse = tuple(Segment(s.id, s.target, s.source) for s in units)
    english, en_changed = project_sources(units, document.baseline, document.source)
    chinese, zh_changed = project_sources(reverse, document.target_baseline, document.target)
    return english, chinese, set(en_changed), set(zh_changed)


def sentence_conflicts(document):
    """Edits in different clauses of the same sentence also need a choice."""
    if document.source == document.baseline or document.target == document.target_baseline:
        return set()
    requests = []
    for current, baseline, units, language in (
        (document.source, document.baseline, tuple(document.segments), "zh"),
        (document.target, document.target_baseline,
         tuple(Segment(s.id, s.target, s.source) for s in document.segments), "en")):
        view = Document(source=current, baseline=baseline, segments=list(units))
        request = prepare_sentences(prepare_one_way(view, target_language=language,
                                                     protected_ids={s.id for s in units}))
        requests.append(request)
    if any(r is None for r in requests):
        return set()
    left, right = requests
    overlap = set(left.affected_ids) & set(right.affected_ids)
    return {sid for r in requests for sentence in r.sentences
            if overlap.intersection(sentence.segment_ids) for sid in sentence.segment_ids}


def prepare_request(document):
    protected_ids = set()
    english_changed = document.source != document.baseline
    chinese_changed = document.target != document.target_baseline
    if not document.segments:
        if document.source.strip() and document.target.strip():
            raise ConflictingEdits()
        chinese = bool(document.target.strip()) or (chinese_changed and not english_changed)
        source = document.target if chinese else document.source
        view = Document(id=document.id, revision=document.revision, source=source,
                        needs_structure=document.needs_structure, structure_source=document.structure_source)
    else:
        en, zh, en_ids, zh_ids = projections(document)
        overlap = sentence_conflicts(document)
        if overlap:
            raise ConflictingEdits(overlap)
        reverse = tuple(Segment(s.id, s.target, s.source) for s in document.segments)
        zh_repairs = untranslated_ids(reverse, "en")
        chinese = chinese_changed or (not english_changed and bool(zh_repairs))
        protected_ids = en_ids if chinese else zh_ids
        current_other = {s.id: s.source for s in (en if chinese else zh)}
        oriented = reverse if chinese else tuple(document.segments)
        view = Document(id=document.id, revision=document.revision,
                        source=document.target if chinese else document.source,
                        baseline=document.target_baseline if chinese else document.baseline,
                        segments=[replace(s, target=current_other[s.id]) for s in oriented])
    request = prepare_sentences(prepare_one_way(view, target_language="en" if chinese else "zh", protected_ids=protected_ids),
                                protected_ids=protected_ids)
    if request is None:
        return None
    return replace(request, source_language="zh" if chinese else "en", target_language="en" if chinese else "zh",
                   english_snapshot=document.source, chinese_snapshot=document.target)


def apply_response(document, request, response):
    if (document.id != request.document_id or document.revision != request.revision
            or document.source != request.english_snapshot or document.target != request.chinese_snapshot):
        return False
    chinese = request.source_language == "zh"
    view = Document(id=document.id, revision=document.revision, source=request.source_after,
                    baseline=request.source_before,
                    segments=list(request.segments) if request.mode in ("revise", "sentence") else [],
                    needs_structure=document.needs_structure, structure_source=document.structure_source)
    apply = apply_sentences if request.mode == "sentence" else apply_one_way
    if not apply(view, request, response):
        return False
    if request.mode == "structure":
        (document.edit_target if chinese else document.edit)(view.source)
        document.structure_source = view.structure_source
        return True
    old = {s.id: s for s in document.segments}
    affected = set(request.affected_ids)
    committed = []
    for unit in view.segments:
        if request.mode == "initial" or unit.id in affected:
            committed.append(Segment(unit.id, unit.target, unit.source) if chinese else unit)
        else:
            committed.append(old[unit.id])
    # Unprocessed manual edits on the opposite side stay in the live text.
    if chinese:
        document.source = view.target
    else:
        document.target_text = view.target
    document.segments = committed
    document.baseline = "".join(s.source for s in committed)
    if request.mode == "initial":
        document.needs_structure = False
        document.structure_source = ""
    if document.target == document.target_baseline:
        document.target_text = None
    return True


def resolve_conflicts(document, preferred):
    """Discard only conflicting edits on the explicitly deselected side."""
    if preferred not in ("zh", "en"):
        raise ValueError("Unknown editing side")
    if not document.segments:
        (document.edit if preferred == "zh" else document.edit_target)("")
        return
    en, zh, en_ids, zh_ids = projections(document)
    overlap = sentence_conflicts(document)
    old = {s.id: s for s in document.segments}
    if preferred == "zh":
        document.edit("".join(old[s.id].source if s.id in overlap else s.source for s in en))
    else:
        document.edit_target("".join(old[s.id].target if s.id in overlap else s.source for s in zh))
