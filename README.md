# Claude Code plugins

Plugins for [Claude Code](https://claude.com/claude-code).

| Plugin | What it does |
|---|---|
| `local-pr-review` | Reviews your local Git changes and builds one HTML page that reads like a pull request: the diff, Claude's explanations and review comments next to the code, in Vietnamese and English. Nothing is pushed and nothing leaves your machine. |

## Install

This repository is private: you need an invitation to it, and a working `gh auth login` or SSH key on GitHub.

In Claude Code:

```
/plugin marketplace add kaito-root/claude-plugins
/plugin install local-pr-review@kaito-tools
```

To get a newer version later:

```
/plugin marketplace update kaito-tools
```

## local-pr-review

Needs `git` and `python3` (3.9 or newer). No other dependency, no API key, no network.

```
/local-pr-review:local-pr-review                      working tree vs HEAD (staged + unstaged)
/local-pr-review:local-pr-review --staged             only staged changes
/local-pr-review:local-pr-review --unstaged           only unstaged changes
/local-pr-review:local-pr-review --base main          your branch vs the merge base with main
/local-pr-review:local-pr-review --commit <sha>       one commit vs its parent
```

Add `--include-untracked` to review new files that are not tracked yet, or
`--untracked-path <file-or-folder>` to add only some of them.

The page is written to `.local-reviews/<time>-<branch>/review.html` in the repository you review.
That folder ignores itself, so it never shows up in `git status`.

What to expect:

- The review is read-only. It does not touch your files, the index or the history.
- Files that usually hold credentials (`.env*`, `*.pem`, `*.key`, ...) are listed without their content,
  and values that look like secrets are redacted.
- Every comment is checked against the diff before the page is built, so a note can't land next to
  the wrong line.
- The page has a VI / EN switch, a unified and a side-by-side view, and a dark theme.

## Working on a plugin

```
python3 -m unittest discover -s plugins/local-pr-review/skills/local-pr-review/tests
claude plugin validate ./plugins/local-pr-review
claude plugin validate .
```

The browser tests need Chrome or Chromium and the page-script tests need Node.js. Both are skipped
when the tool is missing.

To try your changes before pushing, add your local checkout as a marketplace:
`/plugin marketplace add /path/to/this/repo`.
