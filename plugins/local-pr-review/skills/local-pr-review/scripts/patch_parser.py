"""Turns the patch text of ONE file into hunks whose lines carry both line numbers."""

from __future__ import annotations

import re

HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
BINARY_MARKERS = (b"Binary files ", b"GIT binary patch")


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def parse_patch(patch: bytes) -> tuple:
    """Returns (hunks, is_binary). Paths are not read from the patch: `git diff --raw` gives them exactly."""
    hunks = []
    hunk = None
    old_number = new_number = 0
    # Only "\n" ends a line. splitlines() would also break on form feeds and lone carriage returns.
    for raw in patch.split(b"\n"):
        header = HUNK_HEADER.match(raw)
        if header:
            old_number, new_number = int(header.group(1)), int(header.group(3))
            hunk = {
                "kind": "hunk",
                "old_start": old_number,
                "old_count": int(header.group(2) or 1),
                "new_start": new_number,
                "new_count": int(header.group(4) or 1),
                "section": decode(header.group(5)),
                "lines": [],
            }
            hunks.append(hunk)
            continue
        if hunk is None:
            if raw.startswith(BINARY_MARKERS):
                return [], True
            continue
        marker, text = raw[:1], decode(raw[1:])
        if marker == b" ":
            hunk["lines"].append({"t": "ctx", "o": old_number, "n": new_number, "s": text})
            old_number += 1
            new_number += 1
        elif marker == b"-":
            hunk["lines"].append({"t": "del", "o": old_number, "n": None, "s": text})
            old_number += 1
        elif marker == b"+":
            hunk["lines"].append({"t": "add", "o": None, "n": new_number, "s": text})
            new_number += 1
        elif marker == b"\\" and hunk["lines"]:
            hunk["lines"][-1]["nonl"] = True
    return hunks, False


def added_file_hunks(content: bytes) -> list:
    """An untracked file has no patch, so it is shown as one hunk of added lines."""
    if not content:
        return []
    texts = content.split(b"\n")
    ends_with_newline = texts[-1] == b""
    if ends_with_newline:
        texts.pop()
    lines = [{"t": "add", "o": None, "n": number, "s": decode(raw)} for number, raw in enumerate(texts, start=1)]
    if not ends_with_newline:
        lines[-1]["nonl"] = True
    return [{"kind": "hunk", "old_start": 0, "old_count": 0, "new_start": 1, "new_count": len(lines), "section": "", "lines": lines}]
