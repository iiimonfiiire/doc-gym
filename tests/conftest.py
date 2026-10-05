"""Shared fixtures. Tests run offline: every outbound HTTP call is blocked, and no .env is read."""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

ENV_PREFIXES = ("DOC_GYM_", "OLLAMA_", "GROQ_", "GEMINI_", "OBSIDIAN_", "DEPLOY_")
TEST_TOKEN = "test-token-not-a-secret"


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
