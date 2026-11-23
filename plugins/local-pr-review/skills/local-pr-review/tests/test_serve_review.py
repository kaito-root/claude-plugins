from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import SCRIPTS, SKILL_DIR, isolated_git_env, run_script
from serve_review import free_port
from test_render_review import CHROME, RenderCase


class Reply:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.text = body.decode("utf-8", "replace")

    def json(self) -> dict:
        return json.loads(self.text)


class ServedCase(RenderCase):
    """The review of RenderCase, served on localhost the way the skill serves it."""

    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.render(self.analysis()).returncode)
        config = json.loads((self.run_dir / "server.json").read_text())
        self.port, self.token = config["port"], config["token"]
        self.server = None

    def serve(self, *args: str):
        self.server = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "serve_review.py"), str(self.run_dir), *args],
            env=isolated_git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop)
        for _ in range(100):
            if self.server.poll() is not None:
                return
            try:
                self.get("/state")
                return
            except OSError:
                time.sleep(0.05)
        self.fail("the review server did not start")

    def stop(self):
        if self.server and self.server.poll() is None:
            self.server.kill()
        if self.server:
            self.server.communicate()

    def request(self, method: str, path: str, body=None, headers=None, host=None) -> Reply:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        if host:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return Reply(response.status, response.read())
        except urllib.error.HTTPError as error:
            with error:
                return Reply(error.code, error.read())

    def get(self, path: str, **options) -> Reply:
        return self.request("GET", path, **options)

    def submit(self, comments: list, token=None, **options) -> Reply:
        headers = {"Content-Type": "application/json", "X-Review-Token": self.token if token is None else token}
        return self.request("POST", "/submit", {"comments": comments}, headers, **options)

    def discussion(self) -> dict:
        return json.loads((self.run_dir / "discussion.json").read_text(encoding="utf-8"))

    def on_line(self, **fields) -> dict:
        comment = {"path": "app/service.rb", "side": "new", "line": 10, "body": "Sao không giữ tên cũ?"}
        comment.update(fields)
        return comment

    def finish(self) -> tuple:
        output, _ = self.server.communicate(timeout=10)
        return self.server.returncode, output


class Serving(ServedCase):
    def test_the_page_is_served_with_the_token_it_needs_to_submit(self):
        self.serve()

        page = self.get("/").text

        self.assertIn("Tighten the service", page)
        self.assertIn(f'<meta name="review-token" content="{self.token}">', page)
        self.assertNotIn(self.token, (self.run_dir / "review.html").read_text(encoding="utf-8"))

    def test_the_state_tells_the_page_when_a_newer_version_is_ready(self):
        self.serve()
        before = self.get("/state").json()["version"]

        time.sleep(0.02)
        self.render(self.analysis(title="A newer title"))

        self.assertNotEqual(before, self.get("/state").json()["version"])

    def test_other_files_of_the_folder_are_not_served(self):
        self.serve()

        self.assertEqual([404, 404, 404], [self.get(path).status for path in ("/server.json", "/changes.json", "/../../etc/passwd")])

    def test_it_only_listens_on_this_machine(self):
        self.serve()

        self.assertEqual("127.0.0.1", json.loads((self.run_dir / "server.json").read_text())["host"])

    def test_it_stops_on_its_own_when_nobody_submits(self):
        self.serve("--timeout", "0.3")

        code, output = self.finish()

        self.assertEqual(3, code)
        self.assertEqual(0, json.loads(output)["submitted"])

    def test_a_folder_without_a_rendered_review_is_refused(self):
        (self.run_dir / "review.html").unlink()

        result = run_script("serve_review.py", str(self.run_dir))

        self.assertEqual(2, result.returncode)
        self.assertIn("render_review.py", result.stderr)


class Submitting(ServedCase):
    def test_a_submitted_comment_starts_a_thread_and_hands_over_to_claude(self):
        self.serve()

        reply = self.submit([self.on_line(re="Check callers")])
        code, output = self.finish()

        self.assertEqual(200, reply.status, reply.text)
        thread = self.discussion()["threads"][0]
        self.assertEqual(("t1", "app/service.rb", "new", 10, "Check callers"), tuple(thread[key] for key in ("id", "path", "side", "line", "re")))
        self.assertEqual([("you", "Sao không giữ tên cũ?")], [(message["author"], message["body"]) for message in thread["messages"]])
        self.assertEqual(0, code)
        self.assertEqual({"submitted": 1, "waiting": ["t1"]}, {key: json.loads(output)[key] for key in ("submitted", "waiting")})

    def test_a_reply_is_added_to_its_thread(self):
        (self.run_dir / "discussion.json").write_text(json.dumps({"threads": [{
            "id": "t1", "path": "app/service.rb", "side": "new", "line": 10, "re": None,
            "messages": [{"author": "you", "body": "Vì sao?"}, {"author": "claude", "body": {"vi": "Vì dễ đọc.", "en": "Readability."}}],
        }]}), encoding="utf-8")
        self.serve()

        reply = self.submit([{"thread": "t1", "body": "Chưa thuyết phục."}, self.on_line(line=2, path="app/new file.rb")])

        self.assertEqual(200, reply.status, reply.text)
        threads = self.discussion()["threads"]
        self.assertEqual(["you", "claude", "you"], [message["author"] for message in threads[0]["messages"]])
        self.assertEqual(("t2", "app/new file.rb", 2), (threads[1]["id"], threads[1]["path"], threads[1]["line"]))

    def refuse(self, expected: int, comments: list, **options) -> Reply:
        self.serve()

        reply = self.submit(comments, **options)

        self.assertEqual(expected, reply.status, reply.text)
        self.assertFalse((self.run_dir / "discussion.json").exists())
        self.assertIsNone(self.server.poll(), "a refused submission must not stop the server")
        return reply

    def test_a_submission_without_the_token_is_refused(self):
        self.refuse(403, [self.on_line()], token="")
        self.assertEqual(403, self.submit([self.on_line()], token="0" * 43).status)

    def test_a_request_addressed_to_another_host_is_refused(self):
        self.refuse(403, [self.on_line()], host="evil.example:%d" % self.port)

    def test_a_comment_on_a_line_outside_the_diff_is_refused(self):
        self.assertIn("line 99", self.refuse(400, [self.on_line(line=99)]).text)

    def test_a_comment_on_an_unknown_file_or_thread_is_refused(self):
        reply = self.refuse(400, [self.on_line(path="nope.rb"), {"thread": "t9", "body": "x"}])

        self.assertIn("nope.rb", reply.text)
        self.assertIn("t9", reply.text)

    def test_an_empty_review_is_refused(self):
        self.refuse(400, [])
        self.assertEqual(400, self.submit([self.on_line(body="   ")]).status)

    def test_one_bad_comment_keeps_the_whole_submission_out(self):
        self.refuse(400, [self.on_line(), self.on_line(line=99)])


class DiscussionOnThePage(ServedCase):
    def thread(self, *messages) -> dict:
        return {"threads": [{"id": "t1", "path": "app/service.rb", "side": "new", "line": 10, "re": None, "messages": list(messages)}]}

    def render_with(self, discussion: dict):
        (self.run_dir / "discussion.json").write_text(json.dumps(discussion), encoding="utf-8")
        return self.render()

    def test_the_discussion_goes_into_the_page(self):
        result = self.render_with(self.thread({"author": "you", "body": "Vì sao?"}, {"author": "claude", "body": {"vi": "Vì dễ đọc.", "en": "Readability."}}))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"author":"claude"', self.html())
        self.assertNotIn("waiting for a reply", result.stdout)

    def test_a_thread_still_waiting_for_claude_is_named(self):
        result = self.render_with(self.thread({"author": "you", "body": "Vì sao?"}))

        self.assertIn("waiting for a reply: t1", result.stdout)

    def test_a_reply_from_claude_needs_both_languages(self):
        result = self.render_with(self.thread({"author": "you", "body": "Vì sao?"}, {"author": "claude", "body": {"vi": "Chỉ tiếng Việt."}}))

        self.assertEqual(1, result.returncode)
        self.assertIn("discussion", result.stderr)
        self.assertIn("en", result.stderr)

    def test_an_unknown_author_is_refused(self):
        result = self.render_with(self.thread({"author": "bot", "body": "hi"}))

        self.assertEqual(1, result.returncode)
        self.assertIn("author", result.stderr)

    def test_what_the_reviewer_wrote_cannot_inject_markup(self):
        self.render_with(self.thread({"author": "you", "body": "</script><script>document.title='PWNED'</script>"}))

        self.assertNotIn("<script>document.title", self.html())


NODE = shutil.which("node")


def end(process: subprocess.Popen):
    if process.poll() is None:
        process.kill()
    process.communicate()


@unittest.skipUnless(CHROME and NODE, "needs Chrome and Node.js 22 or newer")
class WholeLoopInTheBrowser(ServedCase):
    def test_the_reviewer_comments_submits_and_sees_claude_answer(self):
        self.serve()
        debug_port = free_port()
        profile = tempfile.mkdtemp(prefix="lpr-chrome-")
        self.addCleanup(shutil.rmtree, profile, True)
        chrome = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", f"--remote-debugging-port={debug_port}", f"--user-data-dir={profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(end, chrome)
        driver = subprocess.Popen(
            [NODE, str(SKILL_DIR / "tests" / "js" / "live_review.cdp.js"), str(debug_port), f"http://127.0.0.1:{self.port}/#lang=vi"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(end, driver)

        self.assertEqual("SUBMITTED", driver.stdout.readline().strip(), "the page never reached the sent state")
        code, _ = self.finish()
        threads = self.discussion()["threads"]

        self.assertEqual(0, code)
        self.assertEqual([("app/service.rb", "new", 10, None, "Sao không giữ tên cũ?"), ("app/service.rb", "new", 10, "Check callers", "Đồng ý, để tôi kiểm tra.")],
                         [(t["path"], t["side"], t["line"], t["re"], t["messages"][0]["body"]) for t in threads])

        # Claude's turn: answer the first thread, render, serve again.
        discussion = self.discussion()
        discussion["threads"][0]["messages"].append({"author": "claude", "body": {"vi": "Tên mới khớp với spec.", "en": "The new name matches the spec."}})
        (self.run_dir / "discussion.json").write_text(json.dumps(discussion), encoding="utf-8")
        self.assertEqual(0, self.render().returncode)
        self.serve()

        output, errors = driver.communicate(timeout=40)
        self.assertEqual(0, driver.returncode, errors)
        self.assertEqual({"answer": "ClaudeTên mới khớp với spec.", "pendingLeft": 0}, json.loads(output.strip().splitlines()[-1]))


if __name__ == "__main__":
    unittest.main()
