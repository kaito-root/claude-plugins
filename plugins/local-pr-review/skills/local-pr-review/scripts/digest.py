"""Writes digest.md: the diff as numbered text, for the reviewer to read and cite lines from."""

from __future__ import annotations

from pathlib import Path

MARKERS = {"ctx": " ", "add": "+", "del": "-"}
NOTES = {
    "sensitive": "content left out: this file usually holds credentials",
    "size-limit": "content left out: the review reached its size limit",
    "not-a-file": "content left out: not a regular file",
}


def cell(number) -> str:
    return f"{number:>6}" if number is not None else " " * 6


def file_heading(entry: dict) -> str:
    name = f"{entry['old_path']} → {entry['path']}" if entry["old_path"] else entry["path"]
    tags = [entry["status"], f"+{entry['additions']} −{entry['deletions']}"]
    if entry["untracked"]:
        tags.append("untracked")
    return f"## [{entry['id']}] {name} ({', '.join(tags)})"


def file_body(entry: dict) -> list:
    if entry["excluded"]:
        return [f"({NOTES[entry['excluded']]})"]
    if entry["binary"]:
        return ["(binary file, no text diff)"]
    lines = []
    for segment in entry["segments"]:
        if segment["kind"] == "gap":
            hidden = len(segment["lines"]) + segment["skipped"]
            lines.append(f"{'':6} {'':6}   ⋮ {hidden} unchanged lines")
            continue
        header = f"@@ -{segment['old_start']},{segment['old_count']} +{segment['new_start']},{segment['new_count']} @@"
        lines.append(f"{header} {segment['section']}".rstrip())
        for line in segment["lines"]:
            lines.append(f"{cell(line['o'])} {cell(line['n'])} {MARKERS[line['t']]} {line['s']}".rstrip())
    if entry["truncated"]:
        shown, total = entry["truncated"]["shown"], entry["truncated"]["total"]
        lines.append(f"(cut: {shown} of {total} diff lines shown)")
    return lines


def write_digest(changes: dict, target: Path):
    scope, repo, stats = changes["scope"], changes["repo"], changes["stats"]
    lines = [
        "# Local PR Review digest",
        "",
        f"- repository: {repo['name']} ({repo['root']})",
        f"- branch: {repo['branch']}",
        f"- scope: {scope['label']}",
        f"- base commit: {scope['base_commit'] or 'none (empty tree)'}",
        f"- target: {scope['target']}",
        f"- size: {stats['files']} files, +{stats['additions']} −{stats['deletions']}",
    ]
    lines += [f"- warning: {warning}" for warning in changes["warnings"]]
    if changes["untracked_not_included_count"]:
        lines.append(f"- untracked files NOT in this review: {changes['untracked_not_included_count']}")
    lines += [
        "",
        "Columns are OLD line, NEW line, marker, code. Cite a line as (path, side, line):",
        'side "new" takes the NEW column, side "old" takes the OLD column and is only for deleted lines.',
        "",
    ]
    for entry in changes["files"]:
        lines += [file_heading(entry), "", "```"] + file_body(entry) + ["```", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
