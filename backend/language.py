"""Conservative checks for plainly copied prose, not general language scoring."""

import re
import unicodedata

from .markdown import analyze_markdown


def _han(char):
    return "\u3400" <= char <= "\u9fff" or "\U00020000" <= char <= "\U000323af"


def _copied_line(source, target, target_language="zh"):
    blocks, styles = analyze_markdown(source)
    protected = [(b.start, b.end) for b in blocks if b.kind == "code"]
    protected += [(s.start, s.end) for s in styles if s.style == "inline_code"]
    offset = 0
    for raw in source.splitlines(keepends=True):
        end = offset + len(raw)
        if any(a < end and b > offset for a, b in protected):
            offset = end
            continue
        offset = end
        line = re.sub(r"^\s*(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)", "", raw).strip()
        if not line or line not in target or re.search(r"https?://|www\.|[\w.+-]+@[\w.-]+", line):
            continue
        if target_language == "en":
            if sum(_han(c) for c in line) >= 2:
                return True
            continue
        foreign = [c for c in line if unicodedata.category(c).startswith("L") and not _han(c)]
        if not foreign:
            continue
        words = ["".join(c for c in word if not _han(c))
                 for word in re.findall(r"[^\W\d_]+", line, re.UNICODE)]
        words = [word for word in words if word]
        # Avoid rejecting an unchanged brand, abbreviation, or proper name.
        if len(words) >= 2 and not (len(words) <= 3 and all(w[:1].isupper() for w in words)):
            return True
        # Kana/Hangul and other scripts can contain a sentence without spaces.
        if len(foreign) >= 4 and any(ord(c) > 0x024f for c in foreign):
            return True
    return False


def untranslated_ids(segments, target_language="zh"):
    """Find copied natural-language sentences; allow Chinese, names and code."""
    source = "".join(s.source for s in segments)
    blocks, _ = analyze_markdown(source)
    code = [(b.start, b.end) for b in blocks if b.kind == "code"]
    offset = 0
    result = []
    for segment in segments:
        end = offset + len(segment.source)
        if not any(a <= offset and end <= b for a, b in code):
            if _copied_line(segment.source, segment.target, target_language):
                result.append(segment.id)
        offset = end
    return tuple(result)
