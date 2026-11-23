"""The conversation between the reviewer and Claude about one review, kept in discussion.json.

The reviewer's comments arrive from the page through serve_review.py. Claude answers by adding a
message to each waiting thread. Both sides are checked against the diff before the page shows them.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from analysis_check import SIDES, Checker

AUTHORS = ("you", "claude")
BODY_LIMIT = 5000
QUOTE_LIMIT = 300
FILE_NAME = "discussion.json"


def read(folder: Path) -> tuple:
    """(raw discussion or None when there is none yet, problem)."""
    path = folder / FILE_NAME
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, f"{FILE_NAME} could not be read: {error}"


def save(folder: Path, discussion: dict):
    # Written next to the file and moved into place, so a reader never sees half of it.
    partial = folder / (FILE_NAME + ".partial")
    partial.write_text(json.dumps(discussion, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, folder / FILE_NAME)


def waiting(threads: list) -> list:
    """Ids of the threads whose last word is the reviewer's, so Claude owes an answer."""
    return [thread["id"] for thread in threads if thread["messages"] and thread["messages"][-1]["author"] == "you"]


def check_discussion(raw, changes: dict) -> tuple:
    """Returns (discussion ready for the page, list of problems)."""
    if raw is None:
        return {"threads": []}, []
    if not isinstance(raw, dict) or not isinstance(raw.get("threads"), list):
        return None, [f"{FILE_NAME} must hold an object with a list of threads"]
    checker = Checker(changes)
    threads, seen = [], set()
    for number, source in enumerate(raw["threads"]):
        where = f"discussion.threads[{number}]"
        if not isinstance(source, dict):
            checker.errors.append(f"{where} must be an object")
            continue
        thread = _thread(checker, source, where)
        if thread["id"] in seen:
            checker.errors.append(f"{where}: the id {thread['id']!r} is used twice")
        seen.add(thread["id"])
        threads.append(thread)
    return {"threads": threads}, checker.errors


def _thread(checker: Checker, source: dict, where: str) -> dict:
    thread = {
        "id": checker.text(source, "id", where, required=True),
        "side": checker.choice(source, "side", SIDES, where, "new"),
        "re": source.get("re") if isinstance(source.get("re"), str) else None,
        "file": None,
        "path": source.get("path"),
        "line": None,
        "unanchored": True,
        "messages": [],
    }
    entry = checker.file_of(source, where)
    if entry:
        line, anchored = checker.line_of(source, "line", entry, thread["side"], where)
        thread.update(file=entry["id"], path=entry["path"], line=line, unanchored=not anchored)
    messages = source.get("messages")
    if not isinstance(messages, list) or not messages:
        checker.errors.append(f"{where}: messages must be a list with at least one message")
        messages = []
    for number, message in enumerate(messages):
        spot = f"{where}.messages[{number}]"
        if not isinstance(message, dict):
            checker.errors.append(f"{spot} must be an object")
            continue
        author = checker.choice(message, "author", AUTHORS, spot)
        body = checker.text(message, "body", spot, required=True)
        if author == "you" and not isinstance(body, str):
            checker.errors.append(f"{spot}: the reviewer's own words stay as they were written, as one text")
        thread["messages"].append({"author": author, "body": body, "at": message.get("at")})
    return thread


def add_comments(raw, comments, changes: dict) -> tuple:
    """Adds the reviewer's submitted comments. Returns (discussion, problems, ids of the threads touched).

    All or nothing: one bad comment keeps the whole submission out.
    """
    discussion = raw if isinstance(raw, dict) and isinstance(raw.get("threads"), list) else {"threads": []}
    threads = {thread.get("id"): thread for thread in discussion["threads"] if isinstance(thread, dict)}
    if not isinstance(comments, list) or not comments:
        return discussion, ["a review needs at least one comment"], []
    checker = Checker(changes)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    additions, touched = [], []
    for number, comment in enumerate(comments):
        where = f"comments[{number}]"
        if not isinstance(comment, dict):
            checker.errors.append(f"{where} must be an object")
            continue
        body = comment.get("body")
        if not isinstance(body, str) or not body.strip():
            checker.errors.append(f"{where}: the comment is empty")
            continue
        if len(body) > BODY_LIMIT:
            checker.errors.append(f"{where}: the comment is longer than {BODY_LIMIT} characters")
            continue
        message = {"author": "you", "body": body.strip(), "at": now}
        if "thread" in comment:
            if comment["thread"] not in threads:
                checker.errors.append(f"{where}: there is no thread {comment['thread']!r}")
                continue
            additions.append((comment["thread"], message, None))
            continue
        entry = checker.file_of(comment, where)
        side = checker.choice(comment, "side", SIDES, where, "new")
        if entry:
            line, anchored = checker.line_of(comment, "line", entry, side, where)
            quote = comment.get("re") if isinstance(comment.get("re"), str) else None
            if anchored:
                new = {"path": entry["path"], "side": side, "line": line, "re": quote[:QUOTE_LIMIT] if quote else None}
                additions.append((None, message, new))
    if checker.errors:
        return discussion, checker.errors, []
    numbers = [int(key[1:]) for key in threads if isinstance(key, str) and key[1:].isdigit()]
    next_number = max(numbers, default=0) + 1
    for thread_id, message, new in additions:
        if new:
            thread_id = f"t{next_number}"
            next_number += 1
            threads[thread_id] = {"id": thread_id, **new, "messages": []}
            discussion["threads"].append(threads[thread_id])
        threads[thread_id]["messages"].append(message)
        touched.append(thread_id)
    return discussion, [], touched
