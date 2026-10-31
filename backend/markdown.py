"""Small, lossless Markdown structure analysis for the native editor."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    start: int
    end: int
    level: int = 0


@dataclass(frozen=True)
class StyleSpan:
    style: str
    start: int
    end: int


def _line_kind(line):
    body = line.rstrip("\r\n")
    if not body.strip():
        return "blank", 0
    heading = re.match(r"^ {0,3}(#{1,6})(?:\s|$)", body)
    if heading:
        return "heading", len(heading[1])
    if re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", body):
        return "list", 0
    if re.match(r"^ {0,3}>\s?", body):
        return "quote", 0
    return "paragraph", 0


def analyze_markdown(text: str) -> tuple[tuple[MarkdownBlock, ...], tuple[StyleSpan, ...]]:
    """Describe structure/styles by offsets without inserting or removing text."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocks, styles = [], []
    i = 0
    while i < len(lines):
        start = offsets[i]
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", lines[i])
        if fence:
            delimiter = fence[1]
            i += 1
            while i < len(lines):
                closing = re.match(r"^ {0,3}([`~]+)\s*$", lines[i])
                i += 1
                if (closing and closing[1][0] == delimiter[0]
                        and len(closing[1]) >= len(delimiter)
                        and set(closing[1]) == {delimiter[0]}):
                    break
            blocks.append(MarkdownBlock("code", start, offsets[i]))
            styles.append(StyleSpan("code", start, offsets[i]))
            continue
        kind, level = _line_kind(lines[i])
        i += 1
        if kind in ("paragraph", "quote", "blank"):
            while i < len(lines):
                if re.match(r"^ {0,3}(`{3,}|~{3,})", lines[i]) or _line_kind(lines[i])[0] != kind:
                    break
                i += 1
        end = offsets[i]
        blocks.append(MarkdownBlock(kind, start, end, level))
        if kind == "heading":
            styles.append(StyleSpan(f"heading{min(level, 3)}", start, end))
        elif kind == "quote":
            styles.append(StyleSpan("quote", start, end))
        if kind == "list":
            marker = re.match(r"\s*(?:[-+*]|\d+[.)])\s+", text[start:end])
            styles.append(StyleSpan("marker", start, start + marker.end()))
        if kind not in ("blank", "code"):
            fragment = text[start:end]
            inline_code = list(re.finditer(r"(`+)(.+?)\1", fragment))
            for match in inline_code:
                styles.append(StyleSpan("inline_code", start+match.start(), start+match.end()))
            for match in re.finditer(r"\*\*(?=\S)(.+?)\*\*|__(?=\S)(.+?)__", fragment):
                if not any(code.start() <= match.start() < code.end() for code in inline_code):
                    styles.append(StyleSpan("strong", start+match.start(), start+match.end()))
    return tuple(blocks), tuple(styles)
