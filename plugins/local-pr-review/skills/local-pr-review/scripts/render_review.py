#!/usr/bin/env python3
"""Builds review.html from changes.json (the diff) and analysis.json (the reviewer's notes).

The page is one file with its style, script and data inside. It loads nothing from the network.
This script adds no review text of its own: without analysis.json the page shows the diff only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from pathlib import Path

from analysis_check import check_analysis

ASSETS = Path(__file__).resolve().parent.parent / "assets"
# Inside a <script> block only these can end the block or start markup. JSON reads them back unchanged.
DATA_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", " ": "\\u2028", " ": "\\u2029"}


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="folder written by collect_diff.py")
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    parser.add_argument(
        "--allow-unanchored",
        action="store_true",
        help="keep notes whose line is not in the diff (shown at the top of their file) instead of failing",
    )
    return parser.parse_args(argv)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, f"{path.name} could not be read: {error}"


def data_block(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return re.sub("[<>&  ]", lambda match: DATA_ESCAPES[match.group()], text)


def build_page(changes: dict, analysis) -> str:
    style = (ASSETS / "review.css").read_text(encoding="utf-8")
    script = (ASSETS / "review.js").read_text(encoding="utf-8")
    if "</style" in style.lower() or "</script" in script.lower():
        raise ValueError("the page assets must not contain a closing style or script tag")
    title = (analysis or {}).get("title") or f"{changes['repo']['branch']} ({changes['scope']['label']})"
    if isinstance(title, dict):
        # The page sets the tab title again once it knows the reader's language.
        title = title["en"]
    parts = {
        "TITLE": html.escape(f"{title} · Local PR Review"),
        "CSS": style,
        "JS": script,
        "DATA": data_block({"changes": changes, "analysis": analysis}),
    }
    template = (ASSETS / "template.html").read_text(encoding="utf-8")
    # One pass, so review text that happens to contain "{{JS}}" is never substituted again.
    return re.sub(r"\{\{(TITLE|CSS|JS|DATA)\}\}", lambda match: parts[match.group(1)], template)


def main(argv=None) -> int:
    options = parse_args(argv)
    folder = Path(options.run_dir).resolve()
    changes, problem = read_json(folder / "changes.json")
    if problem:
        print(f"local-pr-review: {problem}. Run collect_diff.py first.", file=sys.stderr)
        return 2

    analysis, notes = None, []
    analysis_file = folder / "analysis.json"
    if analysis_file.exists():
        raw, problem = read_json(analysis_file)
        analysis, errors = (None, [problem]) if problem else check_analysis(raw, changes, options.allow_unanchored)
        if errors:
            # An older page would look like the result of this run, so it goes.
            (folder / "review.html").unlink(missing_ok=True)
            print(f"local-pr-review: analysis.json has {len(errors)} problem(s). Fix them and run again:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        loose = sum(1 for note in analysis["comments"] + analysis["explanations"] if note["unanchored"])
        notes.append(f"notes: {len(analysis['comments'])} comments, {len(analysis['explanations'])} explanations")
        if loose:
            notes.append(f"note: {loose} note(s) point at lines outside the diff and are shown at the top of their file")
    else:
        notes.append(f"note: no analysis.json in {folder}; the page shows the diff only")

    target = folder / "review.html"
    target.write_text(build_page(changes, analysis), encoding="utf-8")
    print(f"review: {target}")
    for note in notes:
        print(note)
    if options.open:
        webbrowser.open(target.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
