---
name: local-pr-review
description: Use when the user wants their local Git changes reviewed like a pull request, without pushing or opening a PR — "/local-pr-review", "review my diff", "review what I just changed", "xem lại code trước khi commit", staged or unstaged changes, a branch against main, or one commit — and wants a page to read rather than `git diff` in the terminal.
argument-hint: "[--staged | --unstaged | --base <ref> | --commit <sha>] [--include-untracked]"
---

# Local PR Review

Turns local Git changes into one self-contained HTML page that reads like a GitHub pull request, with
your review next to the code. Three parts, kept apart on purpose:

| Part | Who | Output |
|---|---|---|
| Collect the diff | `scripts/collect_diff.py` | `changes.json`, `digest.md` |
| Review it | **you**, in this session | `analysis.json` |
| Draw the page | `scripts/render_review.py` | `review.html` |

The scripts never write review text. Whatever the page says about the code, you wrote, so it has to be
true. Nothing here edits source files, the index or history, and nothing leaves the machine.

## 1. Collect

Pass the flags the user typed through unchanged, each ref or path as its own quoted argument.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/collect_diff.py $ARGUMENTS
```

| Flags | Compares |
|---|---|
| none | working tree vs `HEAD` (staged + unstaged, tracked files) |
| `--staged` | index vs `HEAD` |
| `--unstaged` | working tree vs index |
| `--base main` | working tree vs the merge base with `main` |
| `--commit <sha>` | one commit vs its parent |
| `--include-untracked` | adds every untracked file |
| `--untracked-path <file-or-folder>` | adds only those untracked paths (repeatable) |

Add `--repo <path>` to review another checkout or worktree. `--help` lists the size limits.

It prints a JSON summary. Read it before going on:

- `"empty": true`: nothing to review. Tell the user which scope was empty and stop.
- `untracked_not_included` is not empty and the user typed no flags: new files are missing from the
  review. Judge each path by its name and place (a new class, job, migration or test next to the changed
  code belongs; notes, exports and scratch files do not), and open a file when the name does not settle
  it. If every listed path belongs, run again with `--include-untracked`. If only some do, run again
  with one `--untracked-path` per file or folder that does. On that second run add
  `--out <run_dir of the first run>`, so it replaces the first run instead of leaving a second folder.
  Say what you added.
- `truncated_files`, `omitted_files`, `sensitive_files`: the page discloses these. Repeat them in your
  report, because your review does not cover that content.

## 2. Review

Read `digest.md` from the run folder, all of it (in chunks when it is long). It is the diff with both
line numbers on every line, already redacted. Then read the code around the important changes with your
normal tools: callers, callbacks, the job that runs later, the migration, the tests. A finding about
code you did not open is a guess.

What to look for, in this order of weight:

1. Does it do what it is meant to do? Wrong branch, wrong state, missing case.
2. Transactions and locks: what is inside the transaction, what runs after commit, what a rollback
   leaves behind, external calls made while a lock is held.
3. Concurrency and jobs: two requests at once, a job that runs twice, a job that dies half way,
   retries, idempotency.
4. Errors: what is rescued, what is swallowed, what the caller sees.
5. Data: N+1 queries, missing indexes, unsafe migrations, performance regressions.
6. Security: authorization, input reaching SQL or a shell, secrets.
7. Tests: which changed behaviour has no test, and which test would still pass if the code were wrong.

For another stack, keep the order and swap the vocabulary.

## 3. Write `analysis.json`

Write it into the run folder (the summary's `analysis` path). The example shows every field there is.
Required: `title`; `path`, `line`, `title` and `what` on an explanation; `path`, `line`, `severity`,
`title` and `body` on a comment; `title` on a flow; `path` and `summary` on a file. Leave out any other
field you have nothing true to put in.

```json
{
  "title": "One line, like a PR title",
  "overview": "2-4 short paragraphs: what the change does and the design choice behind it.",
  "behavior_changes": ["One observable change per item."],
  "risks": ["One concrete risk per item."],
  "suggested_tests": ["One test per item: the situation and the expected result."],
  "files": [{ "path": "app/services/pay.rb", "summary": "One or two sentences on this file's part." }],
  "explanations": [{
    "path": "app/services/pay.rb", "line": 12, "end_line": 18,
    "title": "Row lock around the status change",
    "what": "What the code now does.",
    "why": "Why it matters.",
    "why_basis": "confirmed",
    "how": "Step by step, when the flow is not obvious.",
    "risks": "What can still go wrong here.",
    "improvements": "Only when the benefit is clear."
  }],
  "comments": [{
    "path": "app/services/pay.rb", "line": 14, "severity": "major",
    "title": "The charge runs before the transaction commits",
    "body": "The situation, the state that triggers it, and the wrong outcome.",
    "suggestion": "The smallest fix, with code when it helps."
  }],
  "flows": [{
    "title": "From payment to fax",
    "description": "One or two sentences.",
    "before": ["Step in the old flow."],
    "after": ["Step in the new flow."],
    "sequence": {
      "actors": ["Controller", "Service", "Job"],
      "messages": [{ "from": "Controller", "to": "Service", "label": "call", "kind": "call" }]
    }
  }]
}
```

- **Lines.** `line` is the NEW column of the digest. For a deleted line, add `"side": "old"` and use the
  OLD column. The line must be in the digest. An explanation can cover a range with `end_line`; a
  comment points at one line.
- **`explanations`** teach: one for each change a reader has to understand to follow the review. Skip
  renames, formatting and fixtures. `what` is required. `why_basis` is `confirmed` when code or a test
  in the repository shows it, `inferred` when it is your reading of the intent.
- **`comments`** are findings. Each one names the state or input that goes wrong and what happens then,
  from code you read. `severity` is `critical` (data loss, security hole, money), `major` (a bug or a
  gap that needs fixing before merge), `minor` (worth fixing), `nit` (wording, style) or `question`
  (you need the author to answer). A change with no problems gets an empty `comments` list. When a
  finding depends on code that is not in the repository (a gem, another service), say so in the body
  and make it a `question`.
- **`flows`** only when the change touches business logic, a transaction, a job or an external API.
  `kind` is `call`, `async` or `return`. Keep each `label` under 40 characters, or the diagram cuts it.
  The page draws the diagram itself and works offline.
- **Two languages.** The page has a VI / EN switch, so write every prose field twice, as
  `{"vi": "...", "en": "..."}` in place of the plain string the example shows:

  ```json
  "title": { "vi": "Charge chạy trước khi transaction commit", "en": "The charge runs before the transaction commits" }
  ```

  That covers `title`, `overview`, `summary`, `what`, `why`, `how`, `risks`, `improvements`, `body`,
  `suggestion`, `description`, message `label`s and every item of `behavior_changes`, `risks`,
  `suggested_tests`, `before` and `after`. Both sides say the same thing; neither is a summary of the
  other. In the Vietnamese text keep technical terms in English (`transaction`, `row lock`,
  `idempotency`, class and method names). A value that is only code or a name stays one plain string,
  and so do `actors`. If the user asks for one language only, write plain strings.
- **Formatting.** Put code and identifiers in backticks. Text fields take `` `code` ``, `**bold**`,
  lists and fenced code blocks.

## 4. Render

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/render_review.py <run_dir> --open
```

It checks every path and line in `analysis.json` against the diff and exits 1 with the list of
problems. Fix `analysis.json` (take the line from the digest again) and run it again until it passes.
Leave `--open` off when the user asked not to open a browser or there is no display.

## 5. Report

Tell the user, briefly and in the language they write in: the scope that was reviewed (the summary's
`scope`, base commit, file and line counts), the path of `review.html`, the comments at `critical` and
`major` with their `path:line`, and anything the review did not cover (untracked files left out, cut or
omitted files).

## Good to know

- Output goes to `.local-reviews/<time>-<branch>/` in the reviewed repository. `.local-reviews/` holds
  its own `.gitignore`, so the repository's ignore file is never touched.
- Files that usually hold credentials (`.env*`, `*.pem`, `*.key`, `id_rsa*`, ...) are listed without
  content. Secret-looking values inside ordinary code are replaced with `«redacted:...»`. Do not open
  those files yourself to fill the gap.
- The page opens in Vietnamese for a Vietnamese browser and in English otherwise, and remembers the
  reader's choice. `review.html#lang=vi` or `#lang=en` forces one.
- Keys on the page: `j` / `k` next and previous file, `/` filter files.
- To change how the page looks, edit `assets/review.css` and `assets/review.js`. To change what counts
  as sensitive or generated, edit `scripts/redaction.py` and `GENERATED` in `scripts/collect_diff.py`.
  Run the tests after any change: `python3 -m unittest discover -s ${CLAUDE_SKILL_DIR}/tests`.
