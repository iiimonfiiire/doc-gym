"""HTTP-level tests: a real server on an ephemeral localhost port, with the model calls mocked."""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

import service
from conftest import TEST_TOKEN


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(service, "make_workout", lambda *a, **k: pytest.fail("model must not be called"))


def test_health_is_public_and_lists_ready(start, buffer, workout):
    buffer.put("API Specs", workout)
    status, body = start(buffer).request("GET", "/health")
    assert status == 200
    assert body == {"ok": True, "buffer": True, "ready": ["API Specs"]}


def test_health_without_buffer(start):
    assert start().request("GET", "/health") == (200, {"ok": True, "buffer": False, "ready": []})


def test_unknown_get_is_404(start):
    assert start().request("GET", "/nope")[0] == 404


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong-token"},
    {"Authorization": TEST_TOKEN + "x"},
    {"Authorization": "Bearer "},
])
def test_post_requires_valid_token(start, no_model, headers):
    for path in ("/generate", "/evaluate", "/nope"):
        status, body = start().post(path, {"intensity": "Sprint", "focus": "API Specs"}, headers=headers)
        assert status == 401
        assert TEST_TOKEN not in json.dumps(body)


def test_unauthorized_when_server_has_no_token(start, monkeypatch, no_model):
    client = start()
    monkeypatch.delenv("DOC_GYM_TOKEN")
    assert client.post("/generate", {"intensity": "Sprint", "focus": "API Specs"})[0] == 401


def test_generate_fails_fast_with_ready_list(start, buffer, workout, no_model):
    buffer.put("Release Notes", workout)
    status, body = start(buffer).post("/generate", {"intensity": "Sprint", "focus": "API Specs"})
    assert status == 503
    assert body["ready"] == ["Release Notes"]
    assert "No API Specs workout is ready yet" in body["error"]
    assert "Ready now: Release Notes." in body["error"]
    assert buffer.wanted == ["API Specs"]


def test_generate_fails_fast_with_nothing_ready(start, buffer, no_model):
    status, body = start(buffer).post("/generate", {"intensity": "Sprint", "focus": "API Specs"})
    assert status == 503
    assert body["ready"] == []
    assert "none yet" in body["error"]


def test_generate_serves_buffered_workout(start, buffer, workout, no_model):
    buffer.put("API Specs", workout)
    status, body = start(buffer).post("/generate", {"intensity": "Endurance", "focus": "API Specs"})
    assert status == 200
    assert body["buffered"] is True
    assert body["stem"].endswith("-Hive-Sync-Setup")
    assert "intensity: Endurance" in body["content"]
    assert "# Hive Sync Setup" in body["content"]
    assert buffer.ready() == []


def test_generate_wait_generates_live(start, buffer, workout, monkeypatch):
    monkeypatch.setattr(service, "make_workout", lambda focus, attempts=2: workout)
    status, body = start(buffer).post("/generate", {"intensity": "Sprint", "focus": "SaaS Docs", "wait": True})
    assert status == 200
    assert body["buffered"] is False
    assert 'focus: "SaaS Docs"' in body["content"]


def test_generate_without_buffer_generates_live(start, workout, monkeypatch):
    monkeypatch.setattr(service, "make_workout", lambda focus, attempts=2: workout)
    status, body = start().post("/generate", {"intensity": "Sprint", "focus": "Whimsical SaaS Docs"})
    assert status == 200
    assert 'focus: "SaaS Docs"' in body["content"]


def test_generate_rejects_unknown_choice(start, buffer, no_model):
    status, body = start(buffer).post("/generate", {"intensity": "Sprint", "focus": "Poetry"})
    assert status == 422
    assert "Unknown intensity/focus" in body["error"]


def test_dropped_request_keeps_the_workout(start, buffer, workout, monkeypatch):
    client_gone = threading.Event()

    def slow_make_workout(focus, attempts=2):
        client_gone.wait(5)
        return workout
    monkeypatch.setattr(service, "make_workout", slow_make_workout)
    client = start(buffer)
    body = json.dumps({"intensity": "Sprint", "focus": "API Specs", "wait": True}).encode()
    sock = socket.create_connection(("127.0.0.1", client.port))
    sock.sendall(b"POST /generate HTTP/1.1\r\nHost: localhost\r\n"
                 + f"Authorization: Bearer {TEST_TOKEN}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
    time.sleep(0.2)
    sock.close()
    client_gone.set()
    deadline = time.time() + 5
    while time.time() < deadline and not buffer.ready():
        time.sleep(0.05)
    assert buffer.ready() == ["API Specs"]


def test_evaluate_happy_path(start, monkeypatch):
    monkeypatch.setattr(service, "grade_note", lambda text: (text + "\n## Evaluation\n", "B", 24))
    status, body = start().post("/evaluate", {"content": "note"})
    assert status == 200
    assert body == {"content": "note\n## Evaluation\n", "grade": "B", "score": "24/30"}


def test_evaluate_user_error_is_422(start):
    status, body = start().post("/evaluate", {"content": "no draft here"})
    assert status == 422
    assert "## Draft" in body["error"]


def test_bad_json_is_400(start):
    status, body = start().post("/evaluate", raw=b"{not json")
    assert status == 400
    assert body["error"] == "Body must be JSON."


def test_oversized_body_is_413(start, monkeypatch):
    monkeypatch.setattr(service, "MAX_BODY", 10)
    assert start().post("/evaluate", {"content": "x" * 50})[0] == 413


def test_unknown_post_path_is_404(start):
    assert start().post("/nope", {})[0] == 404
