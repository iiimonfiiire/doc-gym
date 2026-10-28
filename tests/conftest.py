"""Shared fixtures. Tests run offline: every outbound HTTP call is blocked, and no .env is read.

PAKT serves the pinned Signal guide from a fixture copy in a private cache. Without PAKT, the tests
that need it skip, unless DOC_GYM_REQUIRE_PAKT=1 (set in CI when PAKT is available) makes that an error.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

ENV_PREFIXES = ("DOC_GYM_", "OLLAMA_", "GROQ_", "GEMINI_", "OBSIDIAN_", "DEPLOY_", "PAKT_ROOT", "PAKT_OFFLINE")
TEST_TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

os.environ["PAKT_CACHE_DIR"] = PAKT_CACHE = tempfile.mkdtemp(prefix="doc-gym-pakt-cache-")
import signal_guide  # noqa: E402
from common import DocGymError  # noqa: E402

try:
    signal_guide.load_pakt()
    PAKT_ERROR = ""
except DocGymError as e:
    PAKT_ERROR = str(e)
if PAKT_ERROR and os.environ.get("DOC_GYM_REQUIRE_PAKT") == "1":
    raise RuntimeError(f"DOC_GYM_REQUIRE_PAKT=1, but {PAKT_ERROR}")


def _no_network(url: str) -> bytes:
    raise AssertionError(f"Tests must not fetch {url}. The Signal fixture is in the PAKT cache.")


if not PAKT_ERROR:
    from pakt import sources
    shutil.copytree(FIXTURES / "signal", sources._cache_dir(sources.parse_spec(sources.DEFAULT_SOURCE)) / "rules")
    sources._fetch = _no_network

needs_pakt = pytest.mark.skipif(bool(PAKT_ERROR), reason=f"PAKT unavailable: {PAKT_ERROR}")


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
def no_guide(monkeypatch, tmp_path):
    """No cached guide and no network: PAKT can't load Signal."""
    monkeypatch.setattr(signal_guide, "_loaded", {})
    monkeypatch.setenv("PAKT_CACHE_DIR", str(tmp_path / "empty-cache"))
    if not PAKT_ERROR:
        from pakt import styleguide

        def offline(url):
            raise urllib.error.URLError("network is unreachable")
        monkeypatch.setattr(styleguide, "_CACHE", {})
        monkeypatch.setattr(sources, "_fetch", offline)


@pytest.fixture
def local_guide(monkeypatch, tmp_path):
    """A local copy of the Signal fixture as the active guide. Returns its rules pack path to edit."""
    folder = tmp_path / "guide"
    shutil.copytree(FIXTURES / "signal", folder)
    monkeypatch.setenv("DOC_GYM_GUIDE", str(folder))
    return folder / "signal.rules.toml"


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
