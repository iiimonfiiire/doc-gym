"""Security-relevant behavior of the service."""
from __future__ import annotations

import json
import logging
import socket

import pytest

import service
from conftest import TEST_TOKEN


def test_default_bind_is_localhost():
    assert service.parse_bind(service.DEFAULT_BIND) == ("127.0.0.1", 8765)


@pytest.mark.parametrize("value,expected", [
    ("8765", ("127.0.0.1", 8765)),
    (":9000", ("127.0.0.1", 9000)),
    ("0.0.0.0:8765", ("0.0.0.0", 8765)),
    ("[::1]:8765", ("[::1]", 8765)),
])
def test_parse_bind(value, expected):
    assert service.parse_bind(value) == expected


def test_parse_bind_rejects_garbage():
    with pytest.raises(SystemExit, match="DOC_GYM_BIND"):
        service.parse_bind("localhost:http")


def test_main_binds_localhost_by_default(monkeypatch):
    monkeypatch.setenv("DOC_GYM_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DOC_GYM_BUFFER", "0")
    seen = {}

    class Stop(Exception):
        pass

    def fake_create_server(host, port, buffer=None):
        seen.update(host=host, port=port)
        raise Stop
    monkeypatch.setattr(service, "create_server", fake_create_server)
    with pytest.raises(Stop):
        service.main()
    assert seen == {"host": "127.0.0.1", "port": 8765}


def test_handler_has_socket_timeout():
    assert 0 < service.Handler.timeout <= 300


@pytest.mark.parametrize("raw", [b"[]", b'"text"', b"42", b"null"])
def test_non_object_json_is_400(start, raw):
    status, body = start().post("/generate", raw=raw)
    assert status == 400
    assert body["error"] == "Body must be a JSON object."


def test_invalid_utf8_is_400(start):
    assert start().post("/evaluate", raw=b"\xff\xfe{")[0] == 400


def test_negative_content_length_is_400(start):
    port = start().port
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"POST /evaluate HTTP/1.0\r\nAuthorization: Bearer " + TEST_TOKEN.encode()
                     + b"\r\nContent-Length: -5\r\n\r\n")
        assert sock.recv(1024).split(b" ")[1] == b"400"


def test_internal_error_hides_details(start, monkeypatch, caplog):
    def boom(text):
        raise ValueError("secret-ish internal detail")
    monkeypatch.setattr(service, "grade_note", boom)
    with caplog.at_level(logging.ERROR, logger="doc-gym"):
        status, body = start().post("/evaluate", {"content": "x"})
    assert status == 500
    assert "secret-ish" not in json.dumps(body)
    assert "secret-ish internal detail" in caplog.text


def test_token_never_logged(start, caplog):
    with caplog.at_level(logging.DEBUG, logger="doc-gym"):
        start().post("/evaluate", {"content": "x"})
        start().post("/evaluate", {"content": "x"}, headers={"Authorization": "Bearer wrong-guess"})
    assert TEST_TOKEN not in caplog.text
    assert "wrong-guess" not in caplog.text


def test_buffer_write_is_atomic(tmp_path, workout, monkeypatch):
    path = tmp_path / "buffer.json"
    buf = service.WorkoutBuffer(path)
    buf.put("API Specs", workout)
    good = path.read_text()

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(service.json, "dump", fail_dump)
    with pytest.raises(OSError):
        buf.put("SaaS Docs", workout)
    assert path.read_text() == good
