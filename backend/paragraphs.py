"""Visual bilingual paragraphs without changing document text or retranslating."""

from dataclasses import dataclass

from .models import Segment
from .markdown import analyze_markdown
from .translation import project_sources, split_source


@dataclass(frozen=True)
class ParagraphPair:
    source: str
    target: str


def _parts(segment: Segment):
    source_lines = segment.source.splitlines(keepends=True) or [""]
    target_lines = segment.target.splitlines(keepends=True) or [""]
    if not segment.target:
        return [(line, "") for line in source_lines]
    if len(source_lines) == len(target_lines):
        return list(zip(source_lines, target_lines))
    # Keep a coarse alignment together while a newly inserted line break is
    # awaiting a Chinese patch; never guess a cut inside existing Chinese.
    return [(segment.source, segment.target)]


def paragraph_pairs(source: str, baseline: str = "", segments: tuple[Segment, ...] = (),
                    max_chars: int = 360) -> list[ParagraphPair]:
    if segments:
        units, _ = project_sources(segments, baseline, source)
    else:
        units = split_source(source)
    result = []
    english, chinese = "", ""

    def flush():
        nonlocal english, chinese
        if not english and not chinese:
            return
        if not english.strip() and not chinese.strip() and result:
            previous = result.pop()
            result.append(ParagraphPair(previous.source + english, previous.target + chinese))
        else:
            result.append(ParagraphPair(english, chinese))
        english, chinese = "", ""

    for unit in units:
        for source_part, target_part in _parts(unit):
            english += source_part
            chinese += target_part
            if source_part.endswith(("\n", "\r")) or len(english) >= max_chars:
                flush()
    flush()
    return result or [ParagraphPair("", "")]


def markdown_pairs(source: str, baseline: str = "", segments: tuple[Segment, ...] = ()):
    """Group existing alignment by Markdown blocks, never guess target cuts."""
    blocks, _ = analyze_markdown(source)
    boundaries = []
    for block in blocks:
        if block.kind == "blank" and boundaries:
            boundaries[-1] = block.end
        else:
            boundaries.append(block.end)
    pairs = paragraph_pairs(source, baseline, segments, max_chars=10**9)
    result = []
    english = chinese = ""
    offset = boundary = 0
    for pair in pairs:
        english += pair.source
        chinese += pair.target
        offset += len(pair.source)
        if boundary < len(boundaries) and offset >= boundaries[boundary]:
            result.append(ParagraphPair(english, chinese))
            english = chinese = ""
            while boundary < len(boundaries) and offset >= boundaries[boundary]:
                boundary += 1
    if english or chinese:
        result.append(ParagraphPair(english, chinese))
    return result or [ParagraphPair("", "")]
