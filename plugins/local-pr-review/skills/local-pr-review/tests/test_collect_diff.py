from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import Repo, collect, file_named, hunk_lines, lines_of, run_script

TWENTY_LINES = "".join(f"line {number}\n" for number in range(1, 21))


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)


class ChangeKinds(RepoCase):
    def test_modified_line_keeps_the_line_number_of_each_side(self):
        self.repo.write("app.rb", TWENTY_LINES)
        self.repo.commit()
        self.repo.write("app.rb", TWENTY_LINES.replace("line 10\n", "line ten\nline ten and a half\n"))

        entry = file_named(collect(self.repo), "app.rb")

        self.assertEqual("modified", entry["status"])
        self.assertEqual([(10, "line 10")], lines_of(entry, "del"))
        self.assertEqual([(10, "line ten"), (11, "line ten and a half")], lines_of(entry, "add"))
        self.assertEqual((2, 1), (entry["additions"], entry["deletions"]))

    def test_context_after_an_insertion_is_numbered_on_both_sides(self):
        self.repo.write("app.rb", TWENTY_LINES)
        self.repo.commit()
        self.repo.write("app.rb", TWENTY_LINES.replace("line 10\n", "line 10\nextra\n"))

        after = [line for line in hunk_lines(file_named(collect(self.repo), "app.rb")) if line["s"] == "line 11"]

        self.assertEqual([{"t": "ctx", "o": 11, "n": 12, "s": "line 11"}], after)

    def test_added_file(self):
        self.repo.commit("empty")
        self.repo.write("new.rb", "one\ntwo\n")
        self.repo.git("add", "new.rb")

        entry = file_named(collect(self.repo), "new.rb")

        self.assertEqual("added", entry["status"])
        self.assertEqual([(1, "one"), (2, "two")], lines_of(entry, "add"))

    def test_deleted_file(self):
        self.repo.write("gone.rb", "one\ntwo\n")
        self.repo.commit()
        (self.repo.root / "gone.rb").unlink()

        entry = file_named(collect(self.repo), "gone.rb")

        self.assertEqual("deleted", entry["status"])
        self.assertEqual([(1, "one"), (2, "two")], lines_of(entry, "del"))

    def test_renamed_file_keeps_its_old_path_and_only_shows_the_edit(self):
        self.repo.write("old_name.rb", TWENTY_LINES)
        self.repo.commit()
        self.repo.git("mv", "old_name.rb", "new_name.rb")
        self.repo.write("new_name.rb", TWENTY_LINES.replace("line 5\n", "line five\n"))

        entry = file_named(collect(self.repo), "new_name.rb")

        self.assertEqual(("renamed", "old_name.rb"), (entry["status"], entry["old_path"]))
        self.assertEqual([(5, "line five")], lines_of(entry, "add"))

    def test_binary_file_is_listed_without_content(self):
        self.repo.commit("empty")
        self.repo.write("logo.png", b"\x89PNG\r\n\x00\x01\x02\x03" * 20)
        self.repo.git("add", "logo.png")

        entry = file_named(collect(self.repo), "logo.png")

        self.assertTrue(entry["binary"])
        self.assertEqual([], entry["segments"])

    def test_file_without_a_trailing_newline_is_flagged(self):
        self.repo.write("a.txt", "one\n")
        self.repo.commit()
        self.repo.write("a.txt", "one\ntwo")

        last = hunk_lines(file_named(collect(self.repo), "a.txt"))[-1]

        self.assertEqual(("two", True), (last["s"], last.get("nonl")))

    def test_empty_diff_reports_empty_and_writes_nothing(self):
        self.repo.write("a.txt", "one\n")
        self.repo.commit()

        summary = collect(self.repo)["summary"]

        self.assertTrue(summary["empty"])
        self.assertFalse((self.repo.root / ".local-reviews").exists())


class MultipleHunks(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.write("app.rb", "".join(f"line {number}\n" for number in range(1, 61)))
        self.repo.commit()
        text = (self.repo.root / "app.rb").read_text()
        self.repo.write("app.rb", text.replace("line 10\n", "line ten\n").replace("line 40\n", "line forty\n"))
        self.entry = file_named(collect(self.repo), "app.rb")

    def test_each_hunk_is_kept_with_its_own_range(self):
        hunks = [segment for segment in self.entry["segments"] if segment["kind"] == "hunk"]

        self.assertEqual([(7, 7), (37, 7)], [(hunk["new_start"], hunk["new_count"]) for hunk in hunks])

    def test_unchanged_lines_between_hunks_can_be_expanded(self):
        gap = [segment for segment in self.entry["segments"] if segment.get("pos") == "mid"][0]

        self.assertEqual((14, 14), (gap["o"], gap["n"]))
        self.assertEqual([f"line {number}" for number in range(14, 37)], gap["lines"])

    def test_only_a_window_of_the_lines_before_the_first_hunk_is_embedded(self):
        self.repo.write("app.rb", "".join(f"line {number}\n" for number in range(1, 201)))
        self.repo.commit()
        text = (self.repo.root / "app.rb").read_text()
        self.repo.write("app.rb", text.replace("line 100\n", "line hundred\n"))

        lead = file_named(collect(self.repo), "app.rb")["segments"][0]

        self.assertEqual(("lead", 20, 76), (lead["pos"], len(lead["lines"]), lead["skipped"]))
        self.assertEqual((77, "line 77"), (lead["n"], lead["lines"][0]))


class StaleContext(unittest.TestCase):
    """A file edited after the diff was taken (or rewritten by a line-ending filter) is not used as context."""

    def test_unchanged_lines_are_only_taken_from_a_file_that_still_matches_the_diff(self):
        from collect_diff import with_gaps
        from patch_parser import parse_patch

        hunks, _ = parse_patch(b"@@ -2,3 +2,3 @@\n two\n-three\n+THREE\n four\n")

        fresh = with_gaps(hunks, ["one", "two", "THREE", "four", "five"])
        stale = with_gaps(hunks, ["one", "two", "three again", "four", "five"])

        self.assertEqual(["gap", "hunk", "gap"], [segment["kind"] for segment in fresh])
        self.assertEqual(["hunk"], [segment["kind"] for segment in stale])


class Modes(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.write("staged.rb", "one\n")
        self.repo.write("unstaged.rb", "one\n")
        self.base = self.repo.commit("base")
        self.repo.write("staged.rb", "one\nstaged\n")
        self.repo.git("add", "staged.rb")
        self.repo.write("unstaged.rb", "one\nunstaged\n")
        self.repo.write("untracked.rb", "brand new\n")

    def paths(self, *args):
        return sorted(entry["path"] for entry in collect(self.repo, *args)["files"])

    def test_default_reviews_staged_and_unstaged_tracked_changes(self):
        self.assertEqual(["staged.rb", "unstaged.rb"], self.paths())

    def test_staged_only(self):
        self.assertEqual(["staged.rb"], self.paths("--staged"))

    def test_unstaged_only(self):
        self.assertEqual(["unstaged.rb"], self.paths("--unstaged"))

    def test_untracked_files_are_included_on_request(self):
        changes = collect(self.repo, "--include-untracked")

        entry = file_named(changes, "untracked.rb")
        self.assertEqual(("added", True), (entry["status"], entry["untracked"]))
        self.assertEqual([(1, "brand new")], lines_of(entry, "add"))

    def test_only_the_untracked_paths_asked_for_are_included(self):
        self.repo.write("notes/private.md", "not part of the change\n")
        self.repo.write("app/new/a.rb", "a\n")
        self.repo.write("app/new/b.rb", "b\n")
        self.repo.write("app/newer.rb", "same prefix, other folder\n")

        changes = collect(self.repo, "--untracked-path", "app/new", "--untracked-path", "untracked.rb")

        untracked = sorted(entry["path"] for entry in changes["files"] if entry["untracked"])
        self.assertEqual(["app/new/a.rb", "app/new/b.rb", "untracked.rb"], untracked)
        self.assertEqual(["app/newer.rb", "notes/private.md"], changes["untracked_not_included"])

    def test_untracked_files_left_out_are_named(self):
        self.assertEqual(["untracked.rb"], collect(self.repo)["untracked_not_included"])

    def test_scope_says_what_is_compared(self):
        scope = collect(self.repo)["scope"]

        self.assertEqual(("working-tree", self.base), (scope["mode"], scope["base_commit"]))

    def test_base_compares_the_merge_base_with_the_working_tree(self):
        self.repo.commit("feature work")
        self.repo.git("checkout", "-q", "-b", "feature")
        self.repo.write("feature.rb", "feature\n")
        self.repo.commit("on feature")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("main_only.rb", "main moved on\n")
        self.repo.commit("on main")
        self.repo.git("checkout", "-q", "feature")
        self.repo.write("feature.rb", "feature\nlocal edit\n")

        changes = collect(self.repo, "--base", "main")

        self.assertEqual(["feature.rb"], [entry["path"] for entry in changes["files"]])
        self.assertEqual([(1, "feature"), (2, "local edit")], lines_of(changes["files"][0], "add"))
        self.assertEqual("main", changes["scope"]["base_ref"])

    def test_commit_reviews_one_commit_against_its_parent(self):
        sha = self.repo.commit("second")
        self.repo.write("later.rb", "later\n")
        self.repo.commit("third")

        changes = collect(self.repo, "--commit", sha)

        self.assertEqual(["staged.rb", "unstaged.rb", "untracked.rb"], sorted(e["path"] for e in changes["files"]))
        self.assertEqual(self.base, changes["scope"]["base_commit"])

    def test_root_commit_is_reviewed_against_an_empty_tree(self):
        changes = collect(self.repo, "--commit", self.base)

        self.assertEqual(["staged.rb", "unstaged.rb"], sorted(entry["path"] for entry in changes["files"]))
        self.assertTrue(all(entry["status"] == "added" for entry in changes["files"]))

    def test_repository_without_commits_reviews_everything_staged(self):
        fresh = Repo()
        self.addCleanup(fresh.cleanup)
        fresh.write("first.rb", "hello\n")
        fresh.git("add", "first.rb")

        self.assertEqual(["first.rb"], [entry["path"] for entry in collect(fresh)["files"]])


class UnusualPaths(RepoCase):
    def test_paths_with_spaces_quotes_and_unicode(self):
        names = ["dir with space/ünï côdé 'q' \"dq\".txt", "-dash.txt", "tab\there.txt", "a&b<c>.html"]
        for name in names:
            self.repo.write(name, "one\n")
        self.repo.commit()
        for name in names:
            self.repo.write(name, "one\ntwo\n")

        changes = collect(self.repo)

        self.assertEqual(sorted(names), sorted(entry["path"] for entry in changes["files"]))
        self.assertTrue(all(lines_of(entry, "add") == [(2, "two")] for entry in changes["files"]))

    def test_a_glob_character_in_a_path_does_not_pull_in_other_files(self):
        self.repo.write("star*.txt", "one\n")
        self.repo.write("starry.txt", "one\n")
        self.repo.commit()
        self.repo.write("star*.txt", "one\nglob\n")
        self.repo.write("starry.txt", "one\nplain\n")

        entry = file_named(collect(self.repo), "star*.txt")

        self.assertEqual([(2, "glob")], lines_of(entry, "add"))

    def test_untracked_file_with_spaces(self):
        self.repo.commit("empty")
        self.repo.write("my notes/to do.md", "todo\n")

        entry = file_named(collect(self.repo, "--include-untracked"), "my notes/to do.md")

        self.assertEqual([(1, "todo")], lines_of(entry, "add"))


class ReadOnly(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.write("a.rb", TWENTY_LINES)
        self.repo.write("b.rb", "one\n")
        self.repo.commit()
        self.repo.write("a.rb", TWENTY_LINES.replace("line 3\n", "line three\n"))
        self.repo.git("add", "a.rb")
        self.repo.write("b.rb", "one\ntwo\n")
        self.repo.write("c.rb", "untracked\n")

    def test_no_mode_touches_the_working_tree_index_or_history(self):
        modes = [[], ["--staged"], ["--unstaged"], ["--base", "main"], ["--commit", "HEAD"], ["--include-untracked"]]
        before = self.repo.snapshot()

        for mode in modes:
            collect(self.repo, *mode)

        self.assertEqual(before, self.repo.snapshot())

    def test_the_review_folder_ignores_itself(self):
        collect(self.repo)

        self.assertTrue((self.repo.root / ".local-reviews").is_dir())
        self.assertNotIn(".local-reviews", self.repo.git("status", "--porcelain", "--untracked-files=all"))


class UntrustedInput(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.write("a.rb", "one\n")
        self.repo.commit()
        self.repo.write("a.rb", "one\ntwo\n")

    def refuse(self, *args):
        result = run_script("collect_diff.py", "--repo", str(self.repo.root), *args)
        self.assertEqual(2, result.returncode, result.stdout)
        return result.stderr

    def test_a_ref_that_looks_like_an_option_is_refused(self):
        target = self.repo.root / "pwned.txt"

        self.refuse(f"--base=--output={target}")

        self.assertFalse(target.exists())

    def test_a_ref_with_shell_syntax_is_refused_and_never_run(self):
        target = self.repo.root / "pwned.txt"

        self.refuse("--commit", f"HEAD; touch {target}")
        self.refuse("--base", f"$(touch {target})")

        self.assertFalse(target.exists())

    def test_staged_and_commit_cannot_be_combined(self):
        self.assertIn("not allowed", self.refuse("--staged", "--commit", "HEAD"))

    def test_untracked_files_do_not_fit_a_commit_review(self):
        self.assertIn("--include-untracked", self.refuse("--commit", "HEAD", "--include-untracked"))
        self.assertIn("--untracked-path", self.refuse("--staged", "--untracked-path", "a.rb"))

    def test_a_folder_that_is_not_a_repository_is_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            result = run_script("collect_diff.py", "--repo", folder)

        self.assertEqual(2, result.returncode)
        self.assertIn("not a Git repository", result.stderr)


class Secrets(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.commit("empty")

    def stage(self, path, content):
        self.repo.write(path, content)
        self.repo.git("add", "-f", path)

    def raw_json(self, *args):
        changes = collect(self.repo, *args)
        return changes, Path(changes["summary"]["changes"]).read_text(encoding="utf-8")

    def test_sensitive_files_are_listed_but_their_content_is_left_out(self):
        self.stage(".env.production", "DATABASE_PASSWORD=hunter2-super-secret\n")
        self.stage("config/master.key", "0123456789abcdef0123456789abcdef\n")
        self.stage("deploy/id_rsa", "not really a key\n")

        changes, raw = self.raw_json()

        self.assertEqual(["sensitive"] * 3, [entry["excluded"] for entry in changes["files"]])
        self.assertNotIn("hunter2", raw)
        self.assertNotIn("0123456789abcdef", raw)

    def test_sensitive_content_is_left_out_of_the_digest_too(self):
        self.stage(".env", "API_KEY=abcdef0123456789\n")

        changes = collect(self.repo)

        self.assertNotIn("abcdef0123456789", Path(changes["summary"]["digest"]).read_text(encoding="utf-8"))

    def test_untracked_sensitive_files_are_never_read(self):
        self.repo.write(".env.local", "TOKEN=abcdef0123456789\n")

        changes, raw = self.raw_json("--include-untracked")

        self.assertEqual("sensitive", file_named(changes, ".env.local")["excluded"])
        self.assertNotIn("abcdef0123456789", raw)

    def test_a_secret_inside_ordinary_code_is_redacted_in_place(self):
        self.stage("config.rb", 'first = 1\nAWS_KEY = "AKIAIOSFODNN7EXAMPLE"\npassword = "correct-horse-battery"\nlast = 2\n')

        changes, raw = self.raw_json()

        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", raw)
        self.assertNotIn("correct-horse-battery", raw)
        self.assertEqual([1, 2, 3, 4], [number for number, _ in lines_of(changes["files"][0], "add")])
        self.assertGreaterEqual(changes["redactions"], 2)

    def test_a_private_key_block_is_redacted_to_its_end(self):
        key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7sT\nq9vXyZ0123456789\n-----END RSA PRIVATE KEY-----\n"
        self.stage("notes.md", "before\n" + key + "after\n")

        changes, raw = self.raw_json()

        self.assertNotIn("MIIEpAIBAAKCAQEA7sT", raw)
        self.assertNotIn("q9vXyZ0123456789", raw)
        self.assertEqual("after", lines_of(changes["files"][0], "add")[-1][1])

    def test_ordinary_code_is_not_redacted(self):
        source = 'token = ENV["API_TOKEN"]\nauthorize!(user, password: params[:password])\nsecret_key_base\n'
        self.stage("auth.rb", source)

        changes = collect(self.repo)

        self.assertEqual(source.splitlines(), [text for _, text in lines_of(changes["files"][0], "add")])
        self.assertEqual(0, changes["redactions"])


class LargeDiffs(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.commit("empty")
        for name in ("a.rb", "b.rb", "c.rb"):
            self.repo.write(name, "".join(f"{name} {number}\n" for number in range(1, 101)))
        self.repo.git("add", "-A")

    def test_a_file_over_the_limit_is_cut_and_says_so(self):
        entry = file_named(collect(self.repo, "--max-file-lines", "40"), "a.rb")

        self.assertEqual({"shown": 40, "total": 100}, entry["truncated"])
        self.assertEqual(40, len(hunk_lines(entry)))
        self.assertEqual(100, entry["additions"])

    def test_files_past_the_total_limit_are_listed_without_content(self):
        changes = collect(self.repo, "--max-total-lines", "150")

        self.assertEqual([None, None, "size-limit"], [entry["excluded"] for entry in changes["files"]])
        self.assertEqual(["c.rb"], changes["limits"]["omitted_files"])
        self.assertEqual(300, changes["stats"]["additions"])

    def test_a_very_long_line_is_cut(self):
        self.repo.write("min.js", "x" * 5000 + "\n")
        self.repo.git("add", "min.js")

        line = hunk_lines(file_named(collect(self.repo), "min.js"))[0]

        self.assertLess(len(line["s"]), 1200)
        self.assertTrue(line["cut"])

    def test_lockfiles_start_collapsed(self):
        self.repo.write("Gemfile.lock", "GEM\n")
        self.repo.git("add", "Gemfile.lock")

        self.assertEqual("generated", file_named(collect(self.repo), "Gemfile.lock")["collapsed"])


class Digest(RepoCase):
    def test_the_digest_carries_the_scope_and_both_line_numbers(self):
        self.repo.write("app.rb", TWENTY_LINES)
        self.repo.commit()
        self.repo.write("app.rb", TWENTY_LINES.replace("line 10\n", "line ten\n"))

        changes = collect(self.repo)
        digest = Path(changes["summary"]["digest"]).read_text(encoding="utf-8")

        self.assertIn("Working tree vs HEAD", digest)
        self.assertRegex(digest, r"(?m)^\s+10\s+-\s+line 10$")
        self.assertRegex(digest, r"(?m)^\s+10\s+\+\s+line ten$")
        self.assertRegex(digest, r"(?m)^\s+11\s+11\s+line 11$")

    def test_counts_match_what_git_reports(self):
        self.repo.write("app.rb", TWENTY_LINES)
        self.repo.commit()
        self.repo.write("app.rb", TWENTY_LINES.replace("line 10\n", "a\nb\nc\n").replace("line 15\n", ""))

        changes = collect(self.repo)
        numstat = self.repo.git("diff", "--numstat", "HEAD").split()

        self.assertEqual([int(numstat[0]), int(numstat[1])], [changes["stats"]["additions"], changes["stats"]["deletions"]])
        entry = changes["files"][0]
        self.assertEqual((len(lines_of(entry, "add")), len(lines_of(entry, "del"))), (entry["additions"], entry["deletions"]))


if __name__ == "__main__":
    unittest.main()
