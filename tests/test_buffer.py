from __future__ import annotations

import json

import pytest

import service
from generate_prompt import FOCUS, PROMPT_VERSION, ThinOutput


@pytest.fixture
def path(tmp_path):
    return tmp_path / "cache" / "buffer.json"


def test_starts_empty_without_file(path):
    assert service.WorkoutBuffer(path).ready() == []


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    json.dumps({"version": PROMPT_VERSION, "items": ["API Specs"]}),
    json.dumps({"version": PROMPT_VERSION, "items": {"API Specs": "not a workout"}}),
    json.dumps({"version": PROMPT_VERSION - 1, "items": {"API Specs": {}}}),
])
def test_ignores_corrupt_or_stale_file(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert service.WorkoutBuffer(path).ready() == []


def test_drops_unknown_focuses_from_file(path, workout):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": PROMPT_VERSION, "items": {"API Specs": workout, "Retired Focus": workout}}))
    assert service.WorkoutBuffer(path).ready() == ["API Specs"]


def test_put_take_persist_across_restarts(path, workout):
    buf = service.WorkoutBuffer(path)
    assert buf.put("API Specs", workout) is True
    assert buf.put("API Specs", {"other": True}) is False
    saved = json.loads(path.read_text())
    assert saved == {"version": PROMPT_VERSION, "items": {"API Specs": workout}}
    assert not path.with_suffix(".tmp").exists()

    reloaded = service.WorkoutBuffer(path)
    assert reloaded.ready() == ["API Specs"]
    assert reloaded.take("API Specs") == workout
    assert reloaded.take("API Specs") is None
    assert reloaded.wake.is_set()
    assert service.WorkoutBuffer(path).ready() == []


def test_requested_focus_is_refilled_first(path):
    buf = service.WorkoutBuffer(path)
    assert buf._next_missing() == list(FOCUS)[0]
    buf.request("Release Notes")
    buf.request("API Specs")
    assert buf.wanted == ["API Specs", "Release Notes"]
    assert buf._next_missing() == "API Specs"
    buf.request("Release Notes")
    assert buf.wanted == ["Release Notes", "API Specs"]


def test_refill_pass_fills_every_focus_in_order(path, workout, monkeypatch):
    order = []

    def make_workout(focus, attempts=2):
        order.append(focus)
        return {**workout, "title": focus}
    monkeypatch.setattr(service, "make_workout", make_workout)
    buf = service.WorkoutBuffer(path)
    buf.request("Troubleshooting")
    assert buf.refill_pass() == set()
    assert order[0] == "Troubleshooting"
    assert sorted(order) == sorted(FOCUS)
    assert buf.ready() == sorted(FOCUS)
    assert buf.wanted == []


def test_refill_pass_retries_thin_output_then_gives_up(path, monkeypatch):
    calls = []

    def make_workout(focus, attempts=2):
        calls.append(focus)
        raise ThinOutput("thin")
    monkeypatch.setattr(service, "make_workout", make_workout)
    failed = service.WorkoutBuffer(path).refill_pass()
    assert failed == set(FOCUS)
    assert len(calls) == service.REFILL_ATTEMPTS * len(FOCUS)


def test_refill_pass_recovers_after_one_thin_answer(path, workout, monkeypatch):
    answers = {f: [ThinOutput("thin"), workout] for f in FOCUS}

    def make_workout(focus, attempts=2):
        item = answers[focus].pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(service, "make_workout", make_workout)
    buf = service.WorkoutBuffer(path)
    assert buf.refill_pass() == set()
    assert buf.ready() == sorted(FOCUS)


def test_refill_pass_does_not_retry_when_ollama_is_down(path, monkeypatch):
    calls = []

    def make_workout(focus, attempts=2):
        calls.append(focus)
        raise RuntimeError("connection refused")
    monkeypatch.setattr(service, "make_workout", make_workout)
    assert service.WorkoutBuffer(path).refill_pass() == set(FOCUS)
    assert len(calls) == len(FOCUS)


def test_one_failing_focus_does_not_starve_the_others(path, workout, monkeypatch):
    def make_workout(focus, attempts=2):
        if focus == "SaaS Docs":
            raise ThinOutput("thin")
        return workout
    monkeypatch.setattr(service, "make_workout", make_workout)
    buf = service.WorkoutBuffer(path)
    assert buf.refill_pass() == {"SaaS Docs"}
    assert buf.ready() == sorted(set(FOCUS) - {"SaaS Docs"})


def test_refill_pass_skips_focus_filled_meanwhile(path, workout, monkeypatch):
    buf = service.WorkoutBuffer(path)
    for f in FOCUS:
        buf.put(f, workout)
    monkeypatch.setattr(service, "make_workout", lambda *a, **k: pytest.fail("nothing to refill"))
    assert buf.refill_pass() == set()
