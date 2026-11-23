from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import SKILL_DIR, Repo, collect, run_script

PAYLOAD = "</script><script>document.title='PWNED'</script><img src=x onerror=\"document.title='PWNED'\">"
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
)
CHROME = next((path for path in CHROME_CANDIDATES if path and Path(path).exists()), None)

SOURCE = "".join(f"line {number}\n" for number in range(1, 61))


class RenderCase(unittest.TestCase):
    """A repo with one edited file (two hunks), one deleted line and one new file."""

    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)
        self.repo.write("app/service.rb", SOURCE)
        self.repo.commit()
        edited = SOURCE.replace("line 10\n", "line ten\n").replace("line 40\n", "").replace("line 41\n", "line 41 " + PAYLOAD + "\n")
        self.repo.write("app/service.rb", edited)
        self.repo.write("app/new file.rb", "def call\n  1\nend\n")
        self.repo.git("add", "-A")
        self.run_dir = Path(collect(self.repo)["summary"]["run_dir"])

    def analysis(self, **overrides) -> dict:
        analysis = {
            "title": "Tighten the service",
            "overview": "Đổi **line ten** và bỏ `line 40`.",
            "behavior_changes": ["Line ten thay cho line 10."],
            "risks": ["Chưa có test cho nhánh mới."],
            "suggested_tests": ["Test line ten."],
            "files": [{"path": "app/service.rb", "summary": "Hai hunk."}],
            "explanations": [
                {"path": "app/service.rb", "line": 10, "title": "Rename", "what": "Đổi tên.", "why": "Dễ đọc hơn.", "why_basis": "inferred"}
            ],
            "comments": [
                {"path": "app/service.rb", "line": 10, "severity": "major", "title": "Check callers", "body": "Ai gọi `line 10`?"}
            ],
            "flows": [],
        }
        analysis.update(overrides)
        return analysis

    def render(self, analysis=None, *args):
        if analysis is not None:
            (self.run_dir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
        return run_script("render_review.py", str(self.run_dir), *args)

    def html(self) -> str:
        return (self.run_dir / "review.html").read_text(encoding="utf-8")

    def comment(self, **fields) -> dict:
        comment = {"path": "app/service.rb", "line": 10, "severity": "minor", "title": "t", "body": "b"}
        comment.update(fields)
        return comment


class Rendering(RenderCase):
    def test_a_valid_review_becomes_one_html_file(self):
        result = self.render(self.analysis())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("<title>Tighten the service", self.html())
        self.assertIn(str(self.run_dir / "review.html"), result.stdout)

    def test_the_page_needs_nothing_from_the_network(self):
        self.render(self.analysis())
        page = self.html()

        shell = re.sub(r'(<script id="review-data"[^>]*>).*?(</script>)', r"\1\2", page, flags=re.S)

        shell = shell.replace('"http://www.w3.org/2000/svg"', "")  # the SVG namespace, never fetched
        self.assertIsNone(re.search(r"https?://|<link\b|<iframe\b|\bsrc\s*=|@import|url\(", shell))
        self.assertIn("default-src 'none'", page)

    def test_source_code_cannot_close_the_data_block(self):
        self.render(self.analysis())
        page = self.html()

        self.assertNotIn("<script>document.title", page)
        self.assertNotIn("<img src=x", page)
        self.assertEqual(2, len(re.findall(r"<script\b", page)))

    def test_review_text_cannot_inject_markup(self):
        self.render(self.analysis(title=PAYLOAD, overview=PAYLOAD, comments=[self.comment(title=PAYLOAD, body=PAYLOAD, suggestion=PAYLOAD)]))
        page = self.html()

        self.assertNotIn("<script>document.title", page)
        self.assertNotIn("<img src=x", page)
        self.assertEqual(2, len(re.findall(r"<script\b", page)))

    def test_the_data_survives_the_escaping_unchanged(self):
        self.render(self.analysis(overview=PAYLOAD))

        data = json.loads(re.search(r'<script id="review-data" type="application/json">(.*?)</script>', self.html(), re.S).group(1))

        self.assertEqual(PAYLOAD, data["analysis"]["overview"])
        self.assertEqual("app/new file.rb", data["changes"]["files"][0]["path"])

    def test_a_review_without_analysis_still_shows_the_diff(self):
        result = self.render()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no analysis.json", result.stdout)
        self.assertIn('"analysis":null', self.html())

    def test_the_page_script_never_builds_html_from_strings(self):
        script = (SKILL_DIR / "assets" / "review.js").read_text(encoding="utf-8")

        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(unsafe, script)


class Validation(RenderCase):
    def refuse(self, analysis, *args) -> str:
        result = self.render(analysis, *args)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.run_dir / "review.html").exists())
        return result.stderr

    def accept(self, analysis, *args):
        result = self.render(analysis, *args)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def test_a_comment_on_a_file_outside_the_diff_is_refused(self):
        errors = self.refuse(self.analysis(comments=[self.comment(path="app/servise.rb")]))

        self.assertIn("app/servise.rb", errors)
        self.assertIn("app/service.rb", errors)

    def test_a_comment_on_a_line_outside_the_diff_is_refused(self):
        errors = self.refuse(self.analysis(comments=[self.comment(line=99)]))

        self.assertIn("line 99", errors)

    def test_a_deleted_line_is_cited_on_the_old_side(self):
        self.accept(self.analysis(comments=[self.comment(side="old", line=40)]))

    def test_a_deleted_line_does_not_exist_on_the_new_side(self):
        new_numbers_after_deletion = self.analysis(comments=[self.comment(side="old", line=10), self.comment(side="new", line=58)])
        self.accept(new_numbers_after_deletion)

        self.refuse(self.analysis(comments=[self.comment(side="new", line=60)]))

    def test_an_unchanged_line_the_page_can_expand_is_a_valid_anchor(self):
        self.accept(self.analysis(comments=[self.comment(line=20)]))

    def test_a_line_in_the_new_file_is_valid(self):
        self.accept(self.analysis(comments=[self.comment(path="app/new file.rb", line=2)]))

    def test_an_unknown_severity_is_refused(self):
        self.assertIn("severity", self.refuse(self.analysis(comments=[self.comment(severity="scary")])))

    def test_an_explanation_range_must_run_forwards(self):
        explanation = {"path": "app/service.rb", "line": 10, "end_line": 8, "title": "t", "what": "w"}

        self.assertIn("end_line", self.refuse(self.analysis(explanations=[explanation])))

    def test_a_review_without_a_title_is_refused(self):
        analysis = self.analysis()
        del analysis["title"]

        self.assertIn("title", self.refuse(analysis))

    def test_every_problem_is_reported_at_once(self):
        errors = self.refuse(self.analysis(comments=[self.comment(line=99), self.comment(path="nope.rb")]))

        self.assertIn("line 99", errors)
        self.assertIn("nope.rb", errors)

    def test_unanchored_comments_can_be_kept_on_request(self):
        self.accept(self.analysis(comments=[self.comment(line=99)]), "--allow-unanchored")

        data = json.loads(re.search(r'<script id="review-data" type="application/json">(.*?)</script>', self.html(), re.S).group(1))
        self.assertTrue(data["analysis"]["comments"][0]["unanchored"])

    def test_broken_json_is_reported_not_crashed_on(self):
        (self.run_dir / "analysis.json").write_text("{not json", encoding="utf-8")

        result = self.render()

        self.assertEqual(1, result.returncode)
        self.assertIn("analysis.json", result.stderr)


def both(vi: str, en: str) -> dict:
    return {"vi": vi, "en": en}


class TwoLanguages(RenderCase):
    def bilingual(self) -> dict:
        return self.analysis(
            title=both("Siết lại service", "Tighten the service"),
            overview=both("Tổng quan tiếng Việt.", "English overview."),
            behavior_changes=[both("Hành vi mới.", "New behaviour."), "`code_only`"],
            comments=[self.comment(title=both("Kiểm tra caller", "Check the callers"), body=both("Ai gọi?", "Who calls it?"))],
            explanations=[{"path": "app/service.rb", "line": 10, "title": both("Đổi tên", "Rename"), "what": both("Đổi.", "Renamed.")}],
        )

    def data(self) -> dict:
        return json.loads(re.search(r'<script id="review-data" type="application/json">(.*?)</script>', self.html(), re.S).group(1))

    def test_review_text_can_carry_both_languages(self):
        result = self.render(self.bilingual())

        self.assertEqual(0, result.returncode, result.stderr)
        analysis = self.data()["analysis"]
        self.assertEqual(both("Kiểm tra caller", "Check the callers"), analysis["comments"][0]["title"])
        self.assertEqual([both("Hành vi mới.", "New behaviour."), "`code_only`"], analysis["behavior_changes"])

    def test_the_browser_tab_takes_the_english_title(self):
        self.render(self.bilingual())

        self.assertIn("<title>Tighten the service", self.html())

    def test_a_text_missing_one_language_is_refused(self):
        result = self.render(self.analysis(overview={"vi": "Chỉ có tiếng Việt."}))

        self.assertEqual(1, result.returncode)
        self.assertIn("overview", result.stderr)
        self.assertIn("en", result.stderr)

    def test_a_language_the_page_does_not_offer_is_refused(self):
        result = self.render(self.analysis(overview={"vi": "a", "en": "b", "fr": "c"}))

        self.assertEqual(1, result.returncode)
        self.assertIn("fr", result.stderr)

    def test_a_required_text_cannot_be_empty_in_one_language(self):
        result = self.render(self.analysis(comments=[self.comment(body=both("Có nội dung.", ""))]))

        self.assertEqual(1, result.returncode)
        self.assertIn("body", result.stderr)


@unittest.skipUnless(CHROME, "needs Chrome or Chromium")
class InTheBrowser(RenderCase):
    def dom(self, analysis, fragment="") -> str:
        self.render(analysis)
        url = (self.run_dir / "review.html").as_uri() + fragment
        result = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--virtual-time-budget=4000", "--dump-dom", url],
            capture_output=True,
            text=True,
            timeout=90,
        )
        # Only what the reader sees: the data block holds every language at once.
        return re.sub(r'(<script id="review-data"[^>]*>).*?(</script>)', r"\1\2", result.stdout, flags=re.S)

    def test_the_page_renders_every_file_and_runs_no_injected_script(self):
        dom = self.dom(self.analysis(title="Safe " + PAYLOAD, comments=[self.comment(body=PAYLOAD, title=PAYLOAD)]))

        self.assertIn('data-ready="1"', dom)
        self.assertNotIn("<title>PWNED", dom)
        self.assertEqual(2, len(re.findall(r'<section class="file[ "]', dom)))
        self.assertNotIn("<img src=\"x\"", dom)

    def test_a_comment_sits_right_under_its_line(self):
        dom = self.dom(self.analysis())

        row_then_thread = r'<tr[^>]*data-n="10"[^>]*>(?:(?!</tr>).)*</tr><tr class="thread"[^>]*>(?:(?!</tr>).)*Check callers'
        self.assertRegex(dom, row_then_thread)

    def test_both_line_numbers_show_next_to_the_code(self):
        dom = self.dom(self.analysis())

        self.assertRegex(dom, r'<tr class="row del"[^>]*data-o="10"')
        self.assertRegex(dom, r'<tr class="row add"[^>]*data-n="10"')
        self.assertRegex(dom, r'<tr class="row ctx"[^>]*data-o="11"[^>]*data-n="11"')

    def test_the_reader_picks_the_language_of_the_review_and_the_page(self):
        analysis = TwoLanguages.bilingual(self)
        vietnamese = self.dom(analysis, "#lang=vi")
        english = self.dom(analysis, "#lang=en")

        self.assertIn("Kiểm tra caller", vietnamese)
        self.assertNotIn("Check the callers", vietnamese)
        self.assertIn("File thay đổi", vietnamese)
        self.assertIn("Check the callers", english)
        self.assertNotIn("Kiểm tra caller", english)
        self.assertIn("Changed files", english)

    def test_a_review_written_in_one_language_shows_in_both(self):
        self.assertIn("Check callers", self.dom(self.analysis(), "#lang=vi"))

    def test_split_view_puts_the_old_and_new_line_in_one_row(self):
        dom = self.dom(self.analysis(), "#view=split")

        self.assertRegex(dom, r'<tr class="row change"[^>]*data-o="10"[^>]*data-n="10"')


if __name__ == "__main__":
    unittest.main()
