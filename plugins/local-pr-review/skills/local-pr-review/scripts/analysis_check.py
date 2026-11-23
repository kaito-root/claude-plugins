"""Checks the reviewer's analysis.json against the collected diff.

The page only shows what the reviewer wrote. This check makes sure every note points at a file
and a line that really are in the diff, so nothing lands next to the wrong code.
"""

from __future__ import annotations

import difflib

LANGUAGES = ("vi", "en")
SEVERITIES = ("critical", "major", "minor", "nit", "question")
SIDES = ("new", "old")
WHY_BASES = ("confirmed", "inferred")
MESSAGE_KINDS = ("call", "async", "return")
EXPLANATION_TEXTS = ("what", "why", "how", "risks", "improvements")


class Checker:
    def __init__(self, changes: dict, allow_unanchored: bool = False):
        self.errors = []
        self.allow_unanchored = allow_unanchored
        self.files = {}
        for entry in changes["files"]:
            self.files[entry["path"]] = entry
            if entry["old_path"]:
                self.files.setdefault(entry["old_path"], entry)
        self.lines = {entry["id"]: anchor_lines(entry) for entry in changes["files"]}

    # -- small readers ---------------------------------------------------------------------

    def text(self, source: dict, key: str, where: str, required: bool = False):
        """A text is one string, or {"vi": ..., "en": ...} when the reader can switch language."""
        value = source.get(key)
        if isinstance(value, dict):
            return self._both_languages(value, key, where, required)
        if value is None or value == "":
            if required:
                self.errors.append(f"{where}: {key} is missing")
            return ""
        if not isinstance(value, str):
            self.errors.append(f"{where}: {key} must be text, or an object with vi and en")
            return ""
        return value

    def _both_languages(self, value: dict, key: str, where: str, required: bool):
        unknown = sorted(str(language) for language in set(value) - set(LANGUAGES))
        if unknown:
            self.errors.append(f"{where}: {key} has a language the page does not offer: {', '.join(unknown)}")
            return ""
        missing = [lang for lang in LANGUAGES if not isinstance(value.get(lang), str) or not value[lang].strip()]
        if missing == list(LANGUAGES) and not required:
            return ""
        if missing:
            self.errors.append(f"{where}: {key} is missing its {' and '.join(missing)} text")
            return ""
        return {language: value[language] for language in LANGUAGES}

    def texts(self, source: dict, key: str, where: str) -> list:
        value = source.get(key) or []
        if not isinstance(value, list):
            self.errors.append(f"{where}: {key} must be a list of text")
            return []
        items = [self.text({key: item}, key, f"{where}[{number}]", required=True) for number, item in enumerate(value)]
        return [item for item in items if item]

    def items(self, source: dict, key: str) -> list:
        value = source.get(key) or []
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            self.errors.append(f"{key} must be a list of objects")
            return []
        return value

    def choice(self, source: dict, key: str, allowed: tuple, where: str, default=None):
        value = source.get(key, default)
        if value not in allowed:
            self.errors.append(f"{where}: {key} must be one of {', '.join(allowed)} (got {value!r})")
            return default or allowed[0]
        return value

    # -- anchors ---------------------------------------------------------------------------

    def file_of(self, source: dict, where: str):
        path = source.get("path")
        entry = self.files.get(path) if isinstance(path, str) else None
        if entry is None:
            close = difflib.get_close_matches(str(path), list(self.files), n=1)
            hint = f" Did you mean {close[0]}?" if close else ""
            self.errors.append(f"{where}: {path!r} is not a file in this review.{hint}")
        return entry

    def line_of(self, source: dict, key: str, entry: dict, side: str, where: str) -> tuple:
        """(line, is_anchored). A line that is not in the diff is an error unless the caller allows it."""
        line = source.get(key)
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            self.errors.append(f"{where}: {key} must be a line number")
            return None, False
        known = self.lines[entry["id"]][side]
        if line in known:
            return line, True
        if not self.allow_unanchored:
            nearest = min(known, key=lambda number: abs(number - line)) if known else None
            hint = f" The nearest line shown is {nearest}." if nearest else " No lines of this file are shown."
            self.errors.append(f"{where}: {entry['path']} has no line {line} on the {side} side of the diff.{hint}")
        return line, False

    # -- sections --------------------------------------------------------------------------

    def comment(self, source: dict, number: int) -> dict:
        where = f"comments[{number}]"
        note = {
            "id": f"c{number}",
            "severity": self.choice(source, "severity", SEVERITIES, where),
            "side": self.choice(source, "side", SIDES, where, "new"),
            "title": self.text(source, "title", where, required=True),
            "body": self.text(source, "body", where, required=True),
            "suggestion": self.text(source, "suggestion", where),
        }
        self._anchor(note, source, where)
        return note

    def explanation(self, source: dict, number: int) -> dict:
        where = f"explanations[{number}]"
        note = {
            "id": f"e{number}",
            "side": self.choice(source, "side", SIDES, where, "new"),
            "title": self.text(source, "title", where, required=True),
            "why_basis": self.choice(source, "why_basis", WHY_BASES, where, "inferred"),
        }
        for key in EXPLANATION_TEXTS:
            note[key] = self.text(source, key, where, required=key == "what")
        entry = self._anchor(note, source, where)
        if entry and source.get("end_line") is not None:
            end_line, anchored = self.line_of(source, "end_line", entry, note["side"], where)
            if end_line is not None and note.get("line") and end_line < note["line"]:
                self.errors.append(f"{where}: end_line {end_line} is before line {note['line']}")
            note["end_line"] = end_line
            note["unanchored"] = note["unanchored"] or not anchored
        return note

    def _anchor(self, note: dict, source: dict, where: str):
        entry = self.file_of(source, where)
        note.update(file=None, path=source.get("path"), line=None, unanchored=True)
        if entry:
            line, anchored = self.line_of(source, "line", entry, note["side"], where)
            note.update(file=entry["id"], path=entry["path"], line=line, unanchored=not anchored)
        return entry

    def file_summaries(self, analysis: dict) -> dict:
        summaries = {}
        for number, source in enumerate(self.items(analysis, "files")):
            where = f"files[{number}]"
            entry = self.file_of(source, where)
            if entry:
                summaries[entry["id"]] = self.text(source, "summary", where, required=True)
        return summaries

    def flow(self, source: dict, number: int) -> dict:
        where = f"flows[{number}]"
        flow = {
            "title": self.text(source, "title", where, required=True),
            "description": self.text(source, "description", where),
            "before": self.texts(source, "before", where),
            "after": self.texts(source, "after", where),
            "sequence": None,
        }
        sequence = source.get("sequence")
        if isinstance(sequence, dict):
            flow["sequence"] = self._sequence(sequence, where)
        elif sequence is not None:
            self.errors.append(f"{where}: sequence must be an object")
        return flow

    def _sequence(self, source: dict, where: str) -> dict:
        # Actors are names that messages refer to, so they stay one plain string each.
        actors = [actor for actor in self.texts(source, "actors", f"{where}.sequence") if isinstance(actor, str)]
        messages = []
        for number, message in enumerate(self.items(source, "messages")):
            spot = f"{where}.sequence.messages[{number}]"
            for end in ("from", "to"):
                if message.get(end) not in actors:
                    self.errors.append(f"{spot}: {end} {message.get(end)!r} is not one of the actors")
            messages.append(
                {
                    "from": message.get("from"),
                    "to": message.get("to"),
                    "label": self.text(message, "label", spot, required=True),
                    "kind": self.choice(message, "kind", MESSAGE_KINDS, spot, "call"),
                }
            )
        return {"actors": actors, "messages": messages}


def anchor_lines(entry: dict) -> dict:
    """Every line number the page can show for a file, per side."""
    lines = {"new": set(), "old": set()}
    for segment in entry["segments"]:
        if segment["kind"] == "gap":
            count = len(segment["lines"])
            lines["old"].update(range(segment["o"], segment["o"] + count))
            lines["new"].update(range(segment["n"], segment["n"] + count))
            continue
        for line in segment["lines"]:
            if line["o"] is not None:
                lines["old"].add(line["o"])
            if line["n"] is not None:
                lines["new"].add(line["n"])
    return lines


def check_analysis(analysis, changes: dict, allow_unanchored: bool = False) -> tuple:
    """Returns (analysis ready for the page, list of problems)."""
    if not isinstance(analysis, dict):
        return None, ["analysis.json must hold one JSON object"]
    checker = Checker(changes, allow_unanchored)
    checked = {
        "title": checker.text(analysis, "title", "analysis", required=True),
        "overview": checker.text(analysis, "overview", "analysis"),
        "behavior_changes": checker.texts(analysis, "behavior_changes", "analysis"),
        "risks": checker.texts(analysis, "risks", "analysis"),
        "suggested_tests": checker.texts(analysis, "suggested_tests", "analysis"),
        "files": checker.file_summaries(analysis),
        "explanations": [checker.explanation(item, n) for n, item in enumerate(checker.items(analysis, "explanations"))],
        "comments": [checker.comment(item, n) for n, item in enumerate(checker.items(analysis, "comments"))],
        "flows": [checker.flow(item, n) for n, item in enumerate(checker.items(analysis, "flows"))],
    }
    return checked, checker.errors
