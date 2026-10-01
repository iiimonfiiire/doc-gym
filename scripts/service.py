#!/usr/bin/env python3
"""Doc Gym HTTP service. Runs on the server next to Ollama, so Obsidian on any device
(desktop or mobile) can generate and grade workouts. The service never touches the vault: Obsidian sends and
receives note text, and does all reading and writing itself.

  GET  /health                          -> {"ok": true}
  POST /generate {"intensity","focus"}  -> {"stem", "content"}
  POST /evaluate {"content"}            -> {"content", "grade", "score"}

POST requests need `Authorization: Bearer $DOC_GYM_TOKEN`.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import DocGymError, env, load_env, ollama_url
from evaluate_draft import grade_note
from generate_prompt import generate_note

MAX_BODY = 1_000_000
# A CPU-only server has one model slot; queue generations instead of piling them onto Ollama.
GENERATE_LOCK = threading.Lock()
log = logging.getLogger("doc-gym")


class Handler(BaseHTTPRequestHandler):
    server_version = "doc-gym/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_address[0], fmt % args)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = env("DOC_GYM_TOKEN", "")
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(token) and hmac.compare_digest(given, token)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "Missing or wrong Doc Gym token."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            return self._send(400, {"error": "Bad Content-Length."})
        if length > MAX_BODY:
            return self._send(413, {"error": "Note too large."})
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/generate":
                with GENERATE_LOCK:
                    stem, content = generate_note(str(data.get("intensity")), str(data.get("focus")))
                self._send(200, {"stem": stem, "content": content})
            elif self.path == "/evaluate":
                content, grade, total = grade_note(str(data.get("content") or ""))
                self._send(200, {"content": content, "grade": grade, "score": f"{total}/30"})
            else:
                self._send(404, {"error": "Not found"})
        except DocGymError as e:
            self._send(422, {"error": str(e)})
        except json.JSONDecodeError:
            self._send(400, {"error": "Body must be JSON."})
        except Exception as e:  # keep the service up; details go to the journal
            log.error("Unhandled error on %s\n%s", self.path, traceback.format_exc())
            self._send(500, {"error": f"Internal error: {e}"})


def main() -> None:
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not env("DOC_GYM_TOKEN"):
        raise SystemExit("DOC_GYM_TOKEN is not set in .env")
    host, _, port = env("DOC_GYM_BIND", "0.0.0.0:8765").rpartition(":")
    server = ThreadingHTTPServer((host, int(port)), Handler)
    log.info("Doc Gym service on %s:%s, Ollama at %s", host, port, ollama_url())
    server.serve_forever()


if __name__ == "__main__":
    main()
