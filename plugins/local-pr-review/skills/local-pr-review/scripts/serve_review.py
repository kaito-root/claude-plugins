#!/usr/bin/env python3
"""Serves one rendered review on this machine so the reviewer can comment on it.

It runs until the reviewer presses Submit: the comments go into discussion.json, the server stops,
and whoever started it (Claude, as a background command) is woken up to answer. Start it again
after answering and the open page reloads itself.

Exit codes: 0 a review was submitted, 3 nobody submitted before the timeout, 2 it could not start.
"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import discussion as talk

HOST = "127.0.0.1"
CONFIG_NAME = "server.json"
BODY_BYTES_LIMIT = 1_000_000
TOKEN_META = '<meta name="review-token" content="{token}">'


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def server_config(folder: Path, new_port: bool = False) -> dict:
    """The address and the secret of this review's server. Kept, so the open page finds it again."""
    path = folder / CONFIG_NAME
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        usable = isinstance(config.get("port"), int) and isinstance(config.get("token"), str)
    except (OSError, ValueError, AttributeError):
        config, usable = {}, False
    if not usable:
        config = {"host": HOST, "port": free_port(), "token": secrets.token_urlsafe(32)}
    elif new_port:
        config["port"] = free_port()
    config["host"] = HOST
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


def page_version(folder: Path) -> str:
    stat = (folder / "review.html").stat()
    return f"{stat.st_mtime_ns}-{stat.st_size}"


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalPRReview"

    def log_message(self, *args):
        pass

    def answer(self, status: int, body, content_type: str = "application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def addressed_to_us(self) -> bool:
        # A page on another site can make the browser call localhost. It can't fake this header's port
        # match and our token together, and a rebound DNS name shows up here as a foreign host.
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"{HOST}:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.addressed_to_us():
            return self.answer(403, {"error": "wrong host"})
        folder = self.server.folder
        if self.path in ("/", "/index.html"):
            page = (folder / "review.html").read_text(encoding="utf-8")
            meta = TOKEN_META.format(token=self.server.token)
            return self.answer(200, page.replace("</head>", meta + "\n</head>", 1).encode("utf-8"), "text/html; charset=utf-8")
        if self.path == "/state":
            raw, _ = talk.read(folder)
            threads = raw["threads"] if isinstance(raw, dict) and isinstance(raw.get("threads"), list) else []
            return self.answer(200, {"version": page_version(folder), "waiting": _waiting(threads)})
        return self.answer(404, {"error": "not found"})

    def do_POST(self):
        if not self.addressed_to_us():
            return self.answer(403, {"error": "wrong host"})
        if not hmac.compare_digest(self.headers.get("X-Review-Token", ""), self.server.token):
            return self.answer(403, {"error": "missing or wrong token"})
        if self.path != "/submit":
            return self.answer(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > BODY_BYTES_LIMIT:
                return self.answer(400, {"errors": ["the review is too large"]})
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            comments = payload.get("comments") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            return self.answer(400, {"errors": ["the review is not valid JSON"]})
        with self.server.lock:
            raw, problem = talk.read(self.server.folder)
            if problem:
                return self.answer(500, {"errors": [problem]})
            updated, errors, touched = talk.add_comments(raw, comments, self.server.changes)
            if errors:
                return self.answer(400, {"errors": errors})
            talk.save(self.server.folder, updated)
            self.server.submitted += len(touched)
        self.answer(200, {"ok": True, "threads": touched})
        # The answer is out. Stopping from another thread lets this request finish first.
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def _waiting(threads: list) -> list:
    usable = [thread for thread in threads if isinstance(thread, dict) and isinstance(thread.get("messages"), list)]
    return talk.waiting([thread for thread in usable if all(isinstance(m, dict) for m in thread["messages"])])


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="folder with a rendered review")
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    parser.add_argument("--timeout", type=float, default=4 * 3600, help="seconds to wait for a submission (default 4 hours)")
    parser.add_argument("--new-port", action="store_true", help="pick another port, when the saved one is taken")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    options = parse_args(argv)
    folder = Path(options.run_dir).resolve()
    try:
        changes = json.loads((folder / "changes.json").read_text(encoding="utf-8"))
        page_version(folder)
    except (OSError, ValueError):
        print(f"local-pr-review: {folder} holds no rendered review. Run render_review.py first.", file=sys.stderr)
        return 2
    config = server_config(folder, options.new_port)
    try:
        server = ThreadingHTTPServer((HOST, config["port"]), Handler)
    except OSError as error:
        print(f"local-pr-review: port {config['port']} can't be used ({error}). Run again with --new-port.", file=sys.stderr)
        return 2
    server.folder, server.changes, server.token = folder, changes, config["token"]
    server.lock, server.submitted = threading.Lock(), 0
    url = f"http://{HOST}:{config['port']}/"
    timer = threading.Timer(options.timeout, server.shutdown)
    timer.daemon = True
    timer.start()
    if options.open:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
    raw, _ = talk.read(folder)
    threads = raw["threads"] if isinstance(raw, dict) and isinstance(raw.get("threads"), list) else []
    print(json.dumps({"submitted": server.submitted, "waiting": _waiting(threads), "discussion": str(folder / talk.FILE_NAME), "url": url}, indent=2))
    return 0 if server.submitted else 3


if __name__ == "__main__":
    sys.exit(main())
