"""Shared fixtures. Tests run offline: every outbound HTTP call is blocked, and no .env is read."""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

ENV_PREFIXES = ("DOC_GYM_", "OLLAMA_", "GROQ_", "GEMINI_", "OBSIDIAN_", "DEPLOY_")
TEST_TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


class Client:
    """Minimal JSON client for a test server."""

    def __init__(self, port: int):
        self.port = port

    def request(self, method: str, path: str, body=None, headers=None, raw: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        resp = conn.getresponse()
        payload = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, payload

    def post(self, path, body=None, headers=AUTH, raw=None):
        return self.request("POST", path, body, headers, raw)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(ENV_PREFIXES):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DOC_GYM_ENV", str(tmp_path / "no-such.env"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not make real HTTP calls. Mock http_json instead.")
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


@pytest.fixture
def buffer(tmp_path):
    import service
    return service.WorkoutBuffer(tmp_path / "buffer.json")


@pytest.fixture
def start(monkeypatch):
    """Start the real service on an ephemeral localhost port. Returns a Client."""
    import service
    monkeypatch.setenv("DOC_GYM_TOKEN", TEST_TOKEN)
    servers = []

    def _start(buffer=None):
        server = service.create_server("127.0.0.1", 0, buffer)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return Client(server.server_address[1])
    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def workout():
    return {
        "title": "Hive Sync Setup",
        "audience": "beekeepers new to the app",
        "context": "HiveSync tracks hive health. Users keep asking how to pair a sensor.",
        "inputs": ["Settings > Sensors > Pair", "Customer: 'Where is the pair button?'"],
        "requirements": ["Explain pairing", "Mention battery level"],
    }


@pytest.fixture
def model_json():
    """A well-formed answer from the workout model, before clean()."""
    return {
        "title": "Title: HiveSync Sensor Pairing",
        "audience": "new users",
        "context": "HiveSync tracks hive health. Pairing confuses people.",
        "inputs": ["- Settings > Sensors > Pair", "Ticket: 'pair button greyed out'", "Battery must be > 20%"],
        "requirements": ["Explain pairing step by step", "Cover the greyed-out button"],
    }
