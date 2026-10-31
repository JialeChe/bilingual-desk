"""Shared data contracts for every translation provider."""

from dataclasses import asdict, dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class Segment:
    id: str
    source: str
    target: str = ""


@dataclass(frozen=True)
class SourceEdit:
    start: int
    end: int
    before: str
    after: str


@dataclass(frozen=True)
class TargetPatch:
    segment_id: str
    before: str
    after: str
    left: str = ""
    right: str = ""


@dataclass(frozen=True)
class ParagraphStart:
    segment_id: str
    kind: str


@dataclass(frozen=True)
class SentenceUpdate:
    segment_ids: tuple[str, ...]
    source_before: str
    source_after: str
    target_text: str
    needs_translation: bool

    @property
    def id(self):
        return self.segment_ids[0]


@dataclass(frozen=True)
class TranslationRequest:
    document_id: str
    revision: int
    mode: str
    source_before: str
    source_after: str
    segments: tuple[Segment, ...]
    edits: tuple[SourceEdit, ...] = ()
    updated_sources: tuple[Segment, ...] = ()
    affected_ids: tuple[str, ...] = ()
    repair_ids: tuple[str, ...] = ()
    source_language: str = "en"
    target_language: str = "zh"
    english_snapshot: str | None = None
    chinese_snapshot: str | None = None
    unit_kinds: tuple[tuple[str, str], ...] = ()
    context_before: str = ""
    context_after: str = ""
    sentences: tuple[SentenceUpdate, ...] = ()


@dataclass(frozen=True)
class TranslationResponse:
    document_id: str
    revision: int
    translations: dict[str, str] = field(default_factory=dict)
    patches: tuple[TargetPatch, ...] = ()
    paragraphs: tuple[ParagraphStart, ...] = ()


@dataclass
class Document:
    id: str = field(default_factory=lambda: uuid4().hex)
    title: str = "未命名文档"
    source: str = ""
    baseline: str = ""
    revision: int = 0
    segments: list[Segment] = field(default_factory=list)
    needs_structure: bool = False
    structure_source: str = ""
    target_text: str | None = None

    @property
    def target(self) -> str:
        return self.target_text if self.target_text is not None else self.target_baseline

    @property
    def target_baseline(self) -> str:
        return "".join(segment.target for segment in self.segments)

    @property
    def pending(self) -> bool:
        return self.source != self.baseline or self.target != self.target_baseline

    def edit_target(self, text: str) -> bool:
        if text == self.target:
            return False
        self.target_text = text
        self.revision += 1
        return True

    def edit(self, text: str) -> bool:
        if text == self.source:
            return False
        self.source = text
        self.revision += 1
        return True

    def to_dict(self) -> dict:
        return {"format_version": 2, **asdict(self)}
