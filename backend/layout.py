"""Whitespace belongs to layout, not generated translations."""

import re


def edges(text: str) -> tuple[str, str, str]:
    core = text.strip()
    if not core:
        return text, "", ""
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return text[:start], text[start:end], text[end:]


def content(text: str) -> str:
    return re.sub(r"\s+", "", text)


def layout_only(before: str, after: str) -> bool:
    return content(before) == content(after)


def restore_edges(source: str, target: str) -> str:
    leading, core, trailing = edges(source)
    return leading + target.strip() + trailing if core else source


def sync_layout(before: str, after: str, target: str) -> str:
    """Mirror changed boundaries, without inventing word-to-character alignment."""
    if before.strip() == target.strip() and layout_only(before, after):
        return after
    left, _, right = edges(before)
    new_left, _, new_right = edges(after)
    target_left, core, target_right = edges(target)
    return (new_left if left != new_left else target_left) + core + (
        new_right if right != new_right else target_right)
