#!/usr/bin/env python3
"""Doc Gym HTTP service. Runs on the server next to Ollama, so Obsidian on any device
(desktop or mobile) can generate and grade workouts. The service never touches the vault: Obsidian sends and
receives note text, and does all reading and writing itself.

  GET  /health                          -> {"ok": true, "ready": [focus, ...]}
  POST /generate {"intensity","focus"}  -> {"stem", "content", "buffered"}
  POST /evaluate {"content"}            -> {"content", "grade", "score"}

POST requests need `Authorization: Bearer $DOC_GYM_TOKEN`.

Generating a workout takes minutes on a small CPU server, so the service keeps one
pre-generated workout per focus (on disk, so it survives restarts) and refills it in the
background after each use. Set DOC_GYM_BUFFER=0 to turn this off.
"""
from __future__ import annotations

import hmac
import json
import logging
import select
import socket
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common import PROJECT_DIR, DocGymError, env, load_env, ollama_url
from evaluate_draft import grade_note
from generate_prompt import FOCUS, PROMPT_VERSION, ThinOutput, check_choice, make_workout, render_note

MAX_BODY = 1_000_000
REFILL_ATTEMPTS = 3
REFILL_RETRY_SECONDS = 120
# A CPU-only server has one model slot; queue generations instead of piling them onto Ollama.
GENERATE_LOCK = threading.Lock()
log = logging.getLogger("doc-gym")


class WorkoutBuffer:
    """One ready-made workout per focus, persisted as JSON and refilled by one background thread."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.items: dict[str, dict] = {}
        try:
            saved = json.loads(path.read_text())
            if saved.get("version") == PROMPT_VERSION:
                self.items = {f: w for f, w in saved.get("items", {}).items() if f in FOCUS}
        except (OSError, ValueError):
            pass  # no buffer yet, or unreadable: start empty

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": PROMPT_VERSION, "items": self.items}))
        tmp.replace(self.path)

    def ready(self) -> list[str]:
        with self.lock:
            return sorted(self.items)

    def take(self, focus: str) -> dict | None:
        with self.lock:
            workout = self.items.pop(focus, None)
            if workout is not None:
                self._save()
        self.wake.set()
        return workout

    def put(self, focus: str, workout: dict) -> bool:
        """Store a workout if that focus has no ready one. Returns whether it was stored."""
        with self.lock:
            if focus in self.items:
                return False
            self.items[focus] = workout
            self._save()
        return True

    def run(self) -> None:
        """Refill loop. Generates missing focuses one at a time. The model lock is taken per
        attempt, so a live request waits for at most one generation."""
        while True:
            failed = False
            for focus in [f for f in FOCUS if f not in self.ready()]:
                workout = None
                for attempt in range(1, REFILL_ATTEMPTS + 1):
                    try:
                        with GENERATE_LOCK:
                            if focus in self.ready():  # a live request's leftover filled it meanwhile
                                break
                            workout = make_workout(focus, attempts=1)
                        break
                    except ThinOutput as e:
                        log.warning("Buffer refill for %s, attempt %d: %s", focus, attempt, e)
                    except Exception as e:  # Ollama down or similar: no point trying the rest now
                        log.warning("Buffer refill for %s failed: %s", focus, e)
                        break
                if workout is None:
                    failed = failed or focus not in self.ready()
                    continue
                self.put(focus, workout)
                log.info("Buffer: %s ready", focus)
            self.wake.wait(REFILL_RETRY_SECONDS if failed else None)
            self.wake.clear()


BUFFER: WorkoutBuffer | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "doc-gym/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_address[0], fmt % args)

    def _client_gone(self) -> bool:
        """True if the client already hung up. A clean close only shows up as end-of-stream,
        so peek instead of relying on the write to fail."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _send(self, status: int, payload: dict) -> bool:
        """Send a JSON response. Returns False if the client had already gone away."""
        body = json.dumps(payload).encode()
        if self._client_gone():
            log.warning("%s disconnected before the %s response", self.client_address[0], self.path)
            return False
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            # Mobile apps drop connections when backgrounded or the screen locks.
            log.warning("%s disconnected before the %s response", self.client_address[0], self.path)
            return False

    def _authorized(self) -> bool:
        token = env("DOC_GYM_TOKEN", "")
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(token) and hmac.compare_digest(given, token)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "ready": BUFFER.ready() if BUFFER else []})
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
                intensity = str(data.get("intensity"))
                focus = check_choice(intensity, str(data.get("focus")))
                workout = BUFFER.take(focus) if BUFFER else None
                buffered = workout is not None
                if not buffered:
                    with GENERATE_LOCK:
                        workout = make_workout(focus)
                stem, content = render_note(workout, intensity, focus)
                sent = self._send(200, {"stem": stem, "content": content, "buffered": buffered})
                if not sent and BUFFER and BUFFER.put(focus, workout):
                    log.info("Buffer: kept %s from a dropped request", focus)
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
    global BUFFER
    if env("DOC_GYM_BUFFER", "1") != "0":
        cache = Path(env("DOC_GYM_CACHE_DIR", str(PROJECT_DIR / "cache"))).expanduser()
        BUFFER = WorkoutBuffer(cache / "buffer.json")
        threading.Thread(target=BUFFER.run, name="buffer-refill", daemon=True).start()
    host, _, port = env("DOC_GYM_BIND", "0.0.0.0:8765").rpartition(":")
    server = ThreadingHTTPServer((host, int(port)), Handler)
    log.info("Doc Gym service on %s:%s, Ollama at %s, buffer %s", host, port, ollama_url(),
             f"ready: {BUFFER.ready()}" if BUFFER else "off")
    server.serve_forever()


if __name__ == "__main__":
    main()
