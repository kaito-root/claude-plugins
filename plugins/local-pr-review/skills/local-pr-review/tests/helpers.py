"""Shared test helpers: throwaway Git repos and a way to run the skill's scripts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))


def isolated_git_env() -> dict:
    """Git must not pick up the developer's own config (aliases, diff drivers, signing)."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    return env


class Repo:
    """A temporary Git repository."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="lpr-test-")).resolve()
        self.git("init", "-q", "-b", "main")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            env=isolated_git_env(),
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", "replace")

    def write(self, path: str, content, mode: str = "w") -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            with open(target, mode, newline="") as handle:
                handle.write(content)
        return target

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, "--allow-empty")
        return self.git("rev-parse", "HEAD").strip()

    def snapshot(self) -> dict:
        """Everything a read-only review must leave untouched."""
        files = {}
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root).as_posix()
            if relative.startswith(".git/") or relative.startswith(".local-reviews"):
                continue
            if path.is_file():
                files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        index = self.root / ".git" / "index"
        return {
            "files": files,
            "index": hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None,
            "staged": self.git("ls-files", "-s"),
            "status": self.git("status", "--porcelain=v1", "--untracked-files=all"),
            "head": self.git("rev-parse", "--verify", "-q", "HEAD").strip(),
            "refs": self.git("for-each-ref"),
        }


def run_script(name: str, *args: str, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        env=isolated_git_env(),
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def collect(repo: Repo, *args: str) -> dict:
    """Run the collector and return the parsed changes.json (or the summary for an empty diff)."""
    result = run_script("collect_diff.py", "--repo", str(repo.root), *args)
    if result.returncode != 0:
        raise AssertionError(f"collect_diff failed ({result.returncode}): {result.stderr}")
    summary = json.loads(result.stdout)
    if summary["empty"]:
        return {"summary": summary, "files": []}
    changes = json.loads(Path(summary["changes"]).read_text(encoding="utf-8"))
    changes["summary"] = summary
    return changes


def file_named(changes: dict, path: str) -> dict:
    for entry in changes["files"]:
        if entry["path"] == path:
            return entry
    raise AssertionError(f"{path!r} not in {[entry['path'] for entry in changes['files']]}")


def hunk_lines(entry: dict) -> list:
    return [line for segment in entry["segments"] if segment["kind"] == "hunk" for line in segment["lines"]]


def lines_of(entry: dict, kind: str) -> list:
    """(line number on its own side, text) for every added or deleted line."""
    number = "n" if kind == "add" else "o"
    return [(line[number], line["s"]) for line in hunk_lines(entry) if line["t"] == kind]
