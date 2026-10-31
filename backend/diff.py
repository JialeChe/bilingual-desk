"""Align complete sentences before characters, so deletions keep their owners."""

from difflib import SequenceMatcher
import re


def _parts(text):
    ends = [m.end() for m in re.finditer(r"[。！？][ \t]*|[.!?](?=\s|$)[ \t]*(?:\r?\n[ \t]*)*|\r?\n", text)]
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    offsets = [0]
    pieces = []
    for end in ends:
        if end > offsets[-1]:
            pieces.append(text[offsets[-1]:end])
            offsets.append(end)
    return pieces, offsets


def _replacement_pairs(old, new):
    """Order-preserving approximate matches within a changed sentence region."""
    m, n = len(old), len(new)
    # Wholesale large rewrites do not need expensive similarity alignment.
    if m * n > 4096:
        for i in range(max(m, n)):
            yield (i if i < m else None, i if i < n else None)
        return
    costs = [[0.0] * (n + 1) for _ in range(m + 1)]
    steps = {}
    for i in range(1, m + 1):
        costs[i][0] = i
        steps[i, 0] = "delete"
    for j in range(1, n + 1):
        costs[0][j] = j
        steps[0, j] = "insert"
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            ratio = SequenceMatcher(None, old[i-1], new[j-1], autojunk=False).ratio()
            costs[i][j], _, steps[i, j] = min(
                (costs[i-1][j-1] + 2 * (1-ratio), 0, "replace"),
                (costs[i-1][j] + 1, 1, "delete"),
                (costs[i][j-1] + 1, 2, "insert"))
    result = []
    i, j = m, n
    while i or j:
        step = steps[i, j]
        if step == "replace":
            i -= 1
            j -= 1
            result.append((i, j))
        elif step == "delete":
            i -= 1
            result.append((i, None))
        else:
            j -= 1
            result.append((None, j))
    yield from reversed(result)


def source_opcodes(before, after):
    """Return exact character ranges, preserving unchanged complete sentences."""
    if before == after:
        if before:
            yield "equal", 0, len(before), 0, len(after)
        return
    old, old_offsets = _parts(before)
    new, new_offsets = _parts(after)
    for kind, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if kind != "replace":
            yield kind, old_offsets[a], old_offsets[b], new_offsets[c], new_offsets[d]
            continue
        old_cursor, new_cursor = old_offsets[a], new_offsets[c]
        for i, j in _replacement_pairs(old[a:b], new[c:d]):
            old_piece = old[a+i] if i is not None else ""
            new_piece = new[c+j] if j is not None else ""
            for subkind, x, y, u, v in SequenceMatcher(None, old_piece, new_piece,
                                                      autojunk=False).get_opcodes():
                yield subkind, old_cursor+x, old_cursor+y, new_cursor+u, new_cursor+v
            old_cursor += len(old_piece)
            new_cursor += len(new_piece)
