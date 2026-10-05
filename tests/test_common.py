from __future__ import annotations

import os

import pytest

import common
from common import DocGymError


def write_env(tmp_path, monkeypatch, text: str):
    path = tmp_path / "test.env"
    path.write_text(text)
    monkeypatch.setenv("DOC_GYM_ENV", str(path))
    return path


def test_load_env_parses_values_comments_and_quotes(tmp_path, monkeypatch):
    write_env(tmp_path, monkeypatch, "\n".join([
        "# comment",
        "",
        "DOC_GYM_VAULT = /tmp/vault ",
        'GROQ_MODEL="quoted-model"',
        "OLLAMA_MODEL='single'",
        "not a pair",
        "DOC_GYM_SERVICE_URLS=http://a:1=x",
    ]))
    common.load_env()
    assert os.environ["DOC_GYM_VAULT"] == "/tmp/vault"
    assert os.environ["GROQ_MODEL"] == "quoted-model"
    assert os.environ["OLLAMA_MODEL"] == "single"
    assert os.environ["DOC_GYM_SERVICE_URLS"] == "http://a:1=x"


def test_load_env_does_not_override_real_env(tmp_path, monkeypatch):
    write_env(tmp_path, monkeypatch, "OLLAMA_MODEL=from-file\n")
    monkeypatch.setenv("OLLAMA_MODEL", "from-env")
    common.load_env()
    assert os.environ["OLLAMA_MODEL"] == "from-env"


def test_load_env_missing_file_is_fine():
    common.load_env()


def test_env_default_and_empty_value(monkeypatch):
    assert common.env("DOC_GYM_X", "fallback") == "fallback"
    monkeypatch.setenv("DOC_GYM_X", "")
    assert common.env("DOC_GYM_X", "fallback") == "fallback"


def test_env_required_raises_clear_error():
    with pytest.raises(DocGymError, match=r"GROQ_API_KEY is not set\. Add it to .*\.env"):
        common.env("GROQ_API_KEY", required=True)


def test_vault_dir_requires_setting():
    with pytest.raises(DocGymError, match="DOC_GYM_VAULT"):
        common.vault_dir()


def test_ollama_url_default_and_trailing_slash(monkeypatch):
    assert common.ollama_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://example.invalid:11434/")
    assert common.ollama_url() == "http://example.invalid:11434"


def test_frontmatter_roundtrip():
    text = "---\ntags: [doc-gym]\nstatus: pending\n---\n# Title\nbody\n"
    fm, body = common.split_frontmatter(text)
    assert fm == ["tags: [doc-gym]", "status: pending"]
    assert body == "# Title\nbody\n"
    assert common.join_frontmatter(fm, body) == text


def test_split_frontmatter_without_frontmatter():
    assert common.split_frontmatter("# Just a note\n") == ([], "# Just a note\n")


def test_set_frontmatter_updates_and_appends():
    lines = ["status: pending", "tags:", "  - status: nested", "title: x"]
    out = common.set_frontmatter(lines, {"status": "completed", "grade": "B"})
    assert out == ["status: completed", "tags:", "  - status: nested", "title: x", "grade: B"]


def test_resolve_note_relative_and_outside(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("DOC_GYM_VAULT", str(vault))
    abs_path, rel = common.resolve_note("Doc Gym/Prompts/a.md")
    assert abs_path == vault / "Doc Gym/Prompts/a.md"
    assert rel == "Doc Gym/Prompts/a.md"
    _, rel = common.resolve_note(str(tmp_path / "elsewhere.md"))
    assert rel == ""


def test_write_note_falls_back_to_filesystem(tmp_path):
    path = tmp_path / "sub" / "note.md"
    assert common.write_note(path, "sub/note.md", "hello") == "filesystem"
    assert path.read_text() == "hello"


def test_http_json_wraps_connection_errors(monkeypatch):
    import urllib.error

    def refuse(*args, **kwargs):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(common.urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="Cannot reach http://example.invalid"):
        common.http_json("http://example.invalid/x", {})
