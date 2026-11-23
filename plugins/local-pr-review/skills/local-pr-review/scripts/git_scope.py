"""Read-only access to a Git repository, and the choice of what is compared with what."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class ReviewError(Exception):
    """A problem the user can fix: wrong folder, unknown ref, flags that don't go together."""


class Git:
    def __init__(self, folder: Path):
        self._env = dict(os.environ)
        # Nothing here may write: no index refresh, no prompts, no pager, no path globbing.
        self._env.update(
            GIT_OPTIONAL_LOCKS="0",
            GIT_LITERAL_PATHSPECS="1",
            GIT_TERMINAL_PROMPT="0",
            GIT_PAGER="cat",
            LC_ALL="C",
        )
        self._folder = str(folder)
        try:
            self.root = Path(self.text("rev-parse", "--show-toplevel"))
        except (ReviewError, FileNotFoundError, NotADirectoryError):
            raise ReviewError(f"{folder} is not a Git repository") from None
        self._folder = str(self.root)

    def run(self, *args: str) -> bytes:
        # Arguments go to git as a list, never through a shell, so a ref or path can't run anything.
        command = [
            "git",
            "-C",
            self._folder,
            "-c",
            "core.quotepath=off",
            "-c",
            "diff.autoRefreshIndex=false",
            "-c",
            "color.ui=never",
            *args,
        ]
        result = subprocess.run(command, env=self._env, capture_output=True)
        if result.returncode != 0:
            raise ReviewError(result.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
        return result.stdout

    def text(self, *args: str) -> str:
        return self.run(*args).decode("utf-8", "replace").strip()

    def try_text(self, *args: str):
        try:
            return self.text(*args)
        except ReviewError:
            return None

    def commit(self, ref: str) -> str:
        """The full commit id for a ref the user typed."""
        if not ref or ref.startswith("-") or any(char in ref for char in "\0\n\r"):
            raise ReviewError(f"{ref!r} is not a valid Git ref")
        sha = self.try_text("rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
        if not sha:
            raise ReviewError(f"{ref!r} is not a commit in this repository")
        return sha

    def empty_tree(self) -> str:
        return self.text("hash-object", "-t", "tree", os.devnull)


@dataclass
class Scope:
    mode: str
    label: str
    diff_args: list
    base_commit: str
    target: str
    base_ref: str = None
    can_include_untracked: bool = True
    warnings: list = field(default_factory=list)


def resolve_scope(git: Git, staged: bool, unstaged: bool, base: str, commit: str) -> Scope:
    head = git.try_text("rev-parse", "--verify", "--quiet", "HEAD")
    if commit:
        return _commit_scope(git, commit)
    if base:
        return _base_scope(git, head, base)
    if unstaged:
        return Scope("unstaged", "Unstaged changes vs index", [], head, "working tree")
    start = head or git.empty_tree()
    if staged:
        return Scope("staged", "Staged changes vs HEAD", ["--cached", start], head, "index", can_include_untracked=False)
    return Scope("working-tree", "Working tree vs HEAD (staged + unstaged)", [start], head, "working tree")


def _base_scope(git: Git, head: str, base: str) -> Scope:
    if not head:
        raise ReviewError("--base needs at least one commit on the current branch")
    base_sha = git.commit(base)
    merge_base = git.try_text("merge-base", head, base_sha)
    if not merge_base:
        raise ReviewError(f"the current branch and {base!r} share no history")
    return Scope(
        "base", f"Working tree vs merge base with {base}", [merge_base], merge_base, "working tree", base_ref=base
    )


def _commit_scope(git: Git, commit: str) -> Scope:
    sha = git.commit(commit)
    parents = git.text("rev-list", "--parents", "-n", "1", sha).split()[1:]
    start = parents[0] if parents else git.empty_tree()
    scope = Scope(
        "commit",
        f"Commit {sha[:10]} vs its parent" if parents else f"Root commit {sha[:10]}",
        [start, sha],
        parents[0] if parents else None,
        sha,
        can_include_untracked=False,
    )
    if len(parents) > 1:
        scope.warnings.append("This is a merge commit. It is compared with its first parent only.")
    return scope
