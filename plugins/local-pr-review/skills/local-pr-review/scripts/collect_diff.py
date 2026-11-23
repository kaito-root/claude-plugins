#!/usr/bin/env python3
"""Collects local Git changes into changes.json plus a digest the reviewer reads.

Read-only: it never writes to the working tree, the index or the history. Its only output is a
new folder under .local-reviews/, which ignores itself.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from digest import write_digest
from git_scope import Git, ReviewError, resolve_scope
from patch_parser import added_file_hunks, decode, parse_patch
from redaction import Redactor, is_sensitive_path

STATUS_NAMES = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "typechange",
    "U": "unmerged",
}
GENERATED = (
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "npm-shrinkwrap.json",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
)
NULL_SHA = re.compile(r"^0+$")
REVIEW_FOLDER = ".local-reviews"
LINE_LENGTH_LIMIT = 1000
READABLE_FILE_BYTES = 2_000_000
EMBEDDED_GAP_LINES = 300
EDGE_CONTEXT_LINES = 20
LISTED_UNTRACKED = 50


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="only staged changes")
    mode.add_argument("--unstaged", action="store_true", help="only unstaged changes")
    mode.add_argument("--base", metavar="REF", help="merge base with REF vs the working tree")
    mode.add_argument("--commit", metavar="SHA", help="one commit vs its parent")
    parser.add_argument("--include-untracked", action="store_true", help="also review every untracked file")
    parser.add_argument(
        "--untracked-path",
        action="append",
        default=[],
        metavar="PATH",
        help="also review this untracked file or folder (repeatable), instead of all of them",
    )
    parser.add_argument("--repo", default=".", help="folder inside the repository (default: current folder)")
    parser.add_argument("--out", help="write the run into this folder instead of .local-reviews/<run>")
    parser.add_argument("--context", type=int, default=3, help="unchanged lines around each change (default 3)")
    parser.add_argument("--max-file-lines", type=int, default=1500, help="diff lines kept per file")
    parser.add_argument("--max-total-lines", type=int, default=20000, help="diff lines kept for the whole review")
    return parser.parse_args(argv)


def split_nul(output: bytes) -> list:
    return [decode(token) for token in output.split(b"\0") if token]


def changed_entries(git: Git, diff_args: list) -> list:
    """One entry per changed path, with exact paths (NUL separated, so any character is safe)."""
    tokens = split_nul(git.run("diff", "--raw", "-z", "--no-abbrev", "-M", *diff_args, "--"))
    entries = {}
    position = 0
    while position < len(tokens):
        old_mode, new_mode, old_sha, new_sha, status = tokens[position][1:].split(" ")
        letter = status[0]
        if letter in "RC":
            old_path, path = tokens[position + 1], tokens[position + 2]
            position += 3
        else:
            old_path, path = None, tokens[position + 1]
            position += 2
        known = entries.get(path)
        if known and known["status"] != "unmerged":
            continue
        entries[path] = {
            "path": path,
            "old_path": old_path,
            "status": STATUS_NAMES.get(letter, "modified"),
            "new_sha": new_sha,
        }
    return list(entries.values())


def line_counts(git: Git, diff_args: list) -> dict:
    """path -> (additions, deletions, is_binary), straight from git."""
    tokens = split_nul(git.run("diff", "--numstat", "-z", "-M", *diff_args, "--"))
    counts = {}
    position = 0
    while position < len(tokens):
        added, deleted, path = tokens[position].split("\t", 2)
        if path:
            position += 1
        else:
            path = tokens[position + 2]
            position += 3
        binary = added == "-"
        counts[path] = (0 if binary else int(added), 0 if binary else int(deleted), binary)
    return counts


def is_generated(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in GENERATED)


def new_entry(path, status, old_path=None, untracked=False) -> dict:
    return {
        "path": path,
        "old_path": old_path,
        "status": status,
        "untracked": untracked,
        "binary": False,
        "additions": 0,
        "deletions": 0,
        "excluded": "sensitive" if is_sensitive_path(path) or (old_path and is_sensitive_path(old_path)) else None,
        "collapsed": "generated" if is_generated(path) else None,
        "truncated": None,
        "segments": [],
    }


class Collector:
    def __init__(self, git: Git, scope, options):
        self.git = git
        self.scope = scope
        self.options = options
        self.lines_used = 0
        self.redactions = 0
        self.warnings = list(scope.warnings)

    # -- tracked changes -------------------------------------------------------------------

    def tracked(self) -> list:
        counts = line_counts(self.git, self.scope.diff_args)
        files = []
        for change in changed_entries(self.git, self.scope.diff_args):
            entry = new_entry(change["path"], change["status"], change["old_path"])
            entry["additions"], entry["deletions"], entry["binary"] = counts.get(change["path"], (0, 0, False))
            if change["status"] == "unmerged":
                self.warnings.append(f"{change['path']} has unresolved merge conflicts.")
            if self._wants_content(entry):
                self._fill_from_patch(entry, change)
            files.append(entry)
        return files

    def _wants_content(self, entry: dict) -> bool:
        if entry["excluded"] or entry["binary"]:
            return False
        if self.lines_used >= self.options.max_total_lines:
            entry["excluded"] = "size-limit"
            return False
        return True

    def _fill_from_patch(self, entry: dict, change: dict):
        paths = [path for path in (change["old_path"], change["path"]) if path]
        patch = self.git.run(
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "-M",
            f"-U{max(self.options.context, 0)}",
            *self.scope.diff_args,
            "--",
            *paths,
        )
        hunks, entry["binary"] = parse_patch(patch)
        new_lines = None if entry["status"] == "deleted" else self._new_side(change)
        self._store(entry, hunks, new_lines)

    def _new_side(self, change: dict):
        """The file as it is after the change, to show unchanged lines around the hunks."""
        try:
            if NULL_SHA.match(change["new_sha"]):
                target = self.git.root / change["path"]
                if target.is_symlink() or not target.is_file() or target.stat().st_size > READABLE_FILE_BYTES:
                    return None
                content = target.read_bytes()
            else:
                content = self.git.run("cat-file", "blob", change["new_sha"])
        except (OSError, ReviewError):
            return None
        if len(content) > READABLE_FILE_BYTES:
            return None
        return split_lines(content)

    # -- untracked files -------------------------------------------------------------------

    def untracked_paths(self) -> list:
        paths = split_nul(self.git.run("ls-files", "--others", "--exclude-standard", "-z"))
        return sorted(path for path in paths if not path.startswith(REVIEW_FOLDER + "/"))

    def untracked(self, paths: list) -> list:
        files = []
        for path in paths:
            entry = new_entry(path, "added", untracked=True)
            if self._wants_content(entry):
                self._fill_from_file(entry)
            files.append(entry)
        return files

    def _fill_from_file(self, entry: dict):
        target = self.git.root / entry["path"]
        if target.is_symlink() or not target.is_file():
            entry["excluded"] = "not-a-file"
            return
        if target.stat().st_size > READABLE_FILE_BYTES:
            entry["excluded"] = "size-limit"
            return
        content = target.read_bytes()
        if b"\0" in content[:8000]:
            entry["binary"] = True
            return
        hunks = added_file_hunks(content)
        entry["additions"] = sum(len(hunk["lines"]) for hunk in hunks)
        self._store(entry, hunks, None)

    # -- shared ----------------------------------------------------------------------------

    def _store(self, entry: dict, hunks: list, new_lines):
        total = sum(len(hunk["lines"]) for hunk in hunks)
        limit = self.options.max_file_lines
        if total > limit:
            hunks = cut_hunks(hunks, limit)
            entry["truncated"] = {"shown": limit, "total": total}
            new_lines = None
        segments = with_gaps(hunks, new_lines) if new_lines is not None else hunks
        redactor = Redactor()
        for segment in segments:
            if segment["kind"] == "gap":
                segment["lines"] = [clean(text, redactor)[0] for text in segment["lines"]]
                continue
            for line in segment["lines"]:
                line["s"], was_cut = clean(line["s"], redactor)
                if was_cut:
                    line["cut"] = True
        self.redactions += redactor.count
        self.lines_used += min(total, limit)
        entry["segments"] = segments


def split_lines(content: bytes) -> list:
    lines = content.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return [decode(line) for line in lines]


def clean(text: str, redactor: Redactor) -> tuple:
    text = redactor.redact(text)
    if len(text) <= LINE_LENGTH_LIMIT:
        return text, False
    return f"{text[:LINE_LENGTH_LIMIT]}… [+{len(text) - LINE_LENGTH_LIMIT} chars]", True


def cut_hunks(hunks: list, limit: int) -> list:
    kept, room = [], limit
    for hunk in hunks:
        if room <= 0:
            break
        kept.append(dict(hunk, lines=hunk["lines"][:room]))
        room -= len(hunk["lines"])
    return kept


def side_range(hunk: dict, side: str) -> tuple:
    """(first line, line after the last) the hunk covers on one side."""
    start, count = hunk[f"{side}_start"], hunk[f"{side}_count"]
    # With a count of 0, git names the line BEFORE the change.
    first = start if count else start + 1
    return first, first + count


def matches_file(hunks: list, new_lines: list) -> bool:
    """The file may have changed since the diff was taken. Then its lines can't be trusted as context."""
    for hunk in hunks:
        for line in hunk["lines"]:
            number = line["n"]
            if number is not None and (number > len(new_lines) or new_lines[number - 1] != line["s"]):
                return False
    return True


def with_gaps(hunks: list, new_lines: list) -> list:
    """Puts the unchanged lines between hunks back in, so the page can expand them."""
    if not hunks or not matches_file(hunks, new_lines):
        return hunks
    segments = []
    old_next = new_next = 1
    for hunk in hunks:
        old_first, old_after = side_range(hunk, "old")
        new_first, new_after = side_range(hunk, "new")
        if new_first - new_next != old_first - old_next:
            return hunks
        segments.append(gap("mid" if segments else "lead", old_next, new_next, new_first - new_next, new_lines))
        segments.append(hunk)
        old_next, new_next = old_after, new_after
    segments.append(gap("trail", old_next, new_next, len(new_lines) - new_next + 1, new_lines))
    return [segment for segment in segments if segment["kind"] == "hunk" or segment["lines"] or segment["skipped"]]


def gap(position: str, old_start: int, new_start: int, count: int, new_lines: list) -> dict:
    count = max(count, 0)
    offset, shown = 0, count
    if position == "lead":
        shown = min(count, EDGE_CONTEXT_LINES)
        offset = count - shown
    elif position == "trail":
        shown = min(count, EDGE_CONTEXT_LINES)
    elif count > EMBEDDED_GAP_LINES:
        shown = 0
    first = new_start + offset
    return {
        "kind": "gap",
        "pos": position,
        "o": old_start + offset,
        "n": first,
        "lines": new_lines[first - 1 : first - 1 + shown],
        "skipped": count - shown,
    }


def choose_untracked(paths: list, options) -> tuple:
    """(untracked paths to review, untracked paths left out)."""
    if options.include_untracked:
        return paths, []
    folders = tuple(chosen.rstrip("/") + "/" for chosen in options.untracked_path)
    wanted = [path for path in paths if path in options.untracked_path or path.startswith(folders)]
    return wanted, [path for path in paths if path not in wanted]


def repo_info(git: Git) -> dict:
    head = git.try_text("rev-parse", "--verify", "--quiet", "HEAD")
    branch = git.try_text("symbolic-ref", "--quiet", "--short", "HEAD")
    return {
        "root": str(git.root),
        "name": git.root.name,
        "branch": branch or (f"detached at {head[:10]}" if head else "no commits yet"),
        "head": head,
        "head_subject": git.try_text("log", "-1", "--format=%s") if head else None,
    }


def build_changes(git: Git, scope, options) -> dict:
    collector = Collector(git, scope, options)
    files = collector.tracked()
    left_out = []
    if scope.can_include_untracked:
        wanted, left_out = choose_untracked(collector.untracked_paths(), options)
        files += collector.untracked(wanted)
    files.sort(key=lambda entry: entry["path"])
    for number, entry in enumerate(files):
        entry["id"] = f"f{number}"
    target = scope.target if scope.mode != "commit" else scope.target[:10]
    return {
        "schema": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": repo_info(git),
        "scope": {
            "mode": scope.mode,
            "label": scope.label,
            "base_ref": scope.base_ref,
            "base_commit": scope.base_commit,
            "target": target,
            "include_untracked": any(entry["untracked"] for entry in files),
            "context": options.context,
        },
        "stats": {
            "files": len(files),
            "additions": sum(entry["additions"] for entry in files),
            "deletions": sum(entry["deletions"] for entry in files),
        },
        "limits": {
            "max_file_lines": options.max_file_lines,
            "max_total_lines": options.max_total_lines,
            "truncated_files": [entry["path"] for entry in files if entry["truncated"]],
            "omitted_files": [entry["path"] for entry in files if entry["excluded"] == "size-limit"],
            "sensitive_files": [entry["path"] for entry in files if entry["excluded"] == "sensitive"],
        },
        "warnings": collector.warnings,
        "untracked_not_included": left_out[:LISTED_UNTRACKED],
        "untracked_not_included_count": len(left_out),
        "redactions": collector.redactions,
        "files": files,
    }


def run_folder(git: Git, changes: dict, out) -> Path:
    if out:
        folder = Path(out).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    reviews = git.root / REVIEW_FOLDER
    reviews.mkdir(exist_ok=True)
    ignore = reviews / ".gitignore"
    if not ignore.exists():
        # The folder ignores itself, so the repository's own .gitignore is never edited.
        ignore.write_text("*\n", encoding="utf-8")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", changes["repo"]["branch"]).strip("-")[:60] or "review"
    name = f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"
    folder, attempt = reviews / name, 1
    while folder.exists():
        attempt += 1
        folder = reviews / f"{name}-{attempt}"
    folder.mkdir()
    return folder


def summary_of(changes: dict, folder) -> dict:
    summary = {
        "empty": not changes["files"],
        "scope": changes["scope"]["label"],
        "branch": changes["repo"]["branch"],
        "base_commit": changes["scope"]["base_commit"],
        "target": changes["scope"]["target"],
        "files": changes["stats"]["files"],
        "additions": changes["stats"]["additions"],
        "deletions": changes["stats"]["deletions"],
        "warnings": changes["warnings"],
        "untracked_not_included": changes["untracked_not_included"],
        "untracked_not_included_count": changes["untracked_not_included_count"],
        "truncated_files": changes["limits"]["truncated_files"],
        "omitted_files": changes["limits"]["omitted_files"],
        "sensitive_files": changes["limits"]["sensitive_files"],
        "redactions": changes["redactions"],
    }
    if folder:
        summary.update(
            run_dir=str(folder),
            changes=str(folder / "changes.json"),
            digest=str(folder / "digest.md"),
            analysis=str(folder / "analysis.json"),
        )
    return summary


def main(argv=None) -> int:
    options = parse_args(argv)
    try:
        git = Git(Path(options.repo))
        scope = resolve_scope(git, options.staged, options.unstaged, options.base, options.commit)
        if (options.include_untracked or options.untracked_path) and not scope.can_include_untracked:
            raise ReviewError("--include-untracked and --untracked-path only fit a review of the working tree")
        changes = build_changes(git, scope, options)
        folder = None
        if changes["files"]:
            folder = run_folder(git, changes, options.out)
            (folder / "changes.json").write_text(
                json.dumps(changes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
            write_digest(changes, folder / "digest.md")
    except ReviewError as error:
        print(f"local-pr-review: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary_of(changes, folder), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
