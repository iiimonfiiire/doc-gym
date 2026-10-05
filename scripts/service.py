#!/usr/bin/env python3
"""Doc Gym HTTP service. Runs on the server next to Ollama, so Obsidian on any device
(desktop or mobile) can generate and grade workouts. The service never touches the vault: Obsidian sends and
receives note text, and does all reading and writing itself.

  GET  /health                                   -> {"ok", "buffer", "ready": [focus, ...]}
  POST /generate {"intensity","focus"[,"wait"]}  -> {"stem", "content", "buffered"}
  POST /evaluate {"content"}                     -> {"content", "grade", "score"}

POST requests need `Authorization: Bearer $DOC_GYM_TOKEN`. The service won't start without one.
It listens on 127.0.0.1:8765 unless DOC_GYM_BIND says otherwise.

Generating a workout takes minutes on a small CPU server, so the service keeps one
pre-generated workout per focus (on disk, so it survives restarts) and refills it in the
background after each use. If the requested focus isn't ready, /generate answers 503 at
once and refills that focus next; pass "wait": true to generate live instead (desktop
tools). Mobile apps drop requests that take minutes. Set DOC_GYM_BUFFER=0 to turn this off.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
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
DEFAULT_BIND = "127.0.0.1:8765"
# Per-socket-operation timeout, so a client that stalls mid-request can't hold a thread forever.
SOCKET_TIMEOUT = 60
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
        self.wanted: list[str] = []
        try:
            saved = json.loads(path.read_text())
            if saved.get("version") == PROMPT_VERSION:
                self.items = {f: w for f, w in saved.get("items", {}).items() if f in FOCUS and isinstance(w, dict)}
        except (OSError, ValueError, AttributeError):
            pass  # no buffer yet, or unreadable or wrong shape: start empty

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": PROMPT_VERSION, "items": self.items}, f)
            f.flush()
            os.fsync(f.fileno())
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

    def request(self, focus: str) -> None:
        """Someone asked for a focus that isn't ready: refill it next."""
        with self.lock:
            if focus in self.wanted:
                self.wanted.remove(focus)
            self.wanted.insert(0, focus)
        self.wake.set()

    def _next_missing(self, skip: set[str] | None = None) -> str | None:
        skip = skip or set()
        with self.lock:
            order = self.wanted + [f for f in FOCUS if f not in self.wanted]
            return next((f for f in order if f not in self.items and f not in skip), None)

    def refill_pass(self) -> set[str]:
        """Generate missing focuses one at a time, requested ones first. The model lock is taken
        per attempt, so a live request waits for at most one generation. Returns the focuses that failed."""
        failed = set()
        while (focus := self._next_missing(skip=failed)) is not None:
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
                except Exception as e:  # Ollama down or similar
                    log.warning("Buffer refill for %s failed: %s", focus, e)
                    break
            if workout is not None:
                self.put(focus, workout)
                log.info("Buffer: %s ready", focus)
            with self.lock:
                if focus in self.wanted:
                    self.wanted.remove(focus)
            if focus not in self.ready():
                failed.add(focus)
        return failed

    def run(self) -> None:
        """Refill loop for the background thread. Retries failed focuses after a pause."""
        while True:
            failed = self.refill_pass()
            self.wake.wait(REFILL_RETRY_SECONDS if failed else None)
            self.wake.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "doc-gym/1.0"
    timeout = SOCKET_TIMEOUT

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
            buffer = self.server.buffer
            self._send(200, {"ok": True, "buffer": buffer is not None, "ready": buffer.ready() if buffer else []})
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
        buffer = self.server.buffer
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                return self._send(400, {"error": "Body must be a JSON object."})
            if self.path == "/generate":
                intensity = str(data.get("intensity"))
                focus = check_choice(intensity, str(data.get("focus")))
                workout = buffer.take(focus) if buffer else None
                buffered = workout is not None
                if buffer and not buffered and not data.get("wait"):
                    # Fail fast: mobile apps drop requests that take minutes. Refill this focus next.
                    buffer.request(focus)
                    ready = buffer.ready()
                    return self._send(503, {
                        "error": f"No {focus} workout is ready yet. It's being prepared now (about 3 minutes). "
                                 f"Ready now: {', '.join(ready) if ready else 'none yet'}.",
                        "ready": ready,
                    })
                if not buffered:
                    with GENERATE_LOCK:
                        workout = make_workout(focus)
                stem, content = render_note(workout, intensity, focus)
                sent = self._send(200, {"stem": stem, "content": content, "buffered": buffered})
                if not sent and buffer and buffer.put(focus, workout):
                    log.info("Buffer: kept %s from a dropped request", focus)
            elif self.path == "/evaluate":
                content, grade, total = grade_note(str(data.get("content") or ""))
                self._send(200, {"content": content, "grade": grade, "score": f"{total}/30"})
            else:
                self._send(404, {"error": "Not found"})
        except DocGymError as e:
            self._send(422, {"error": str(e)})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "Body must be JSON."})
        except Exception:  # keep the service up; details go to the journal, not the client
            log.error("Unhandled error on %s\n%s", self.path, traceback.format_exc())
            self._send(500, {"error": "Internal error. Details are in the service log."})


def parse_bind(value: str) -> tuple[str, int]:
    """'host:port' or a bare port. A missing host means localhost, never all interfaces."""
    host, _, port = value.strip().rpartition(":")
    try:
        return host or "127.0.0.1", int(port)
    except ValueError:
        raise SystemExit(f"DOC_GYM_BIND must be host:port, got {value!r}") from None


def create_server(host: str, port: int, buffer: WorkoutBuffer | None = None) -> ThreadingHTTPServer:
    """Build the HTTP server. Starting the refill thread and serving are up to the caller."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.buffer = buffer
    return server


def main() -> None:
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not env("DOC_GYM_TOKEN"):
        raise SystemExit("DOC_GYM_TOKEN is not set in .env")
    buffer = None
    if env("DOC_GYM_BUFFER", "1") != "0":
        cache = Path(env("DOC_GYM_CACHE_DIR", str(PROJECT_DIR / "cache"))).expanduser()
        buffer = WorkoutBuffer(cache / "buffer.json")
        threading.Thread(target=buffer.run, name="buffer-refill", daemon=True).start()
    host, port = parse_bind(env("DOC_GYM_BIND", DEFAULT_BIND))
    server = create_server(host, port, buffer)
    log.info("Doc Gym service on %s:%s, Ollama at %s, buffer %s", host, port, ollama_url(),
             f"ready: {buffer.ready()}" if buffer else "off")
    server.serve_forever()


if __name__ == "__main__":
    main()
