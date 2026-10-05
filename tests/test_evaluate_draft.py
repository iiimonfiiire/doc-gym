from __future__ import annotations

import json

import pytest

import evaluate_draft as ev
from common import DocGymError

DRAFT = " ".join(["Pair the sensor from Settings, then confirm the battery level is above twenty percent."] * 3)
NOTE = f"""---
tags: [doc-gym]
status: pending
title: "Pairing"
---
# Pairing

## Scenario
Users cannot pair sensors.

## Requirements
- Numbered procedure.

## Draft
<!-- Write your draft here. Delete this comment when you start. -->
## Step one
{DRAFT}
"""
RESULT = {
    "scores": {"technical_clarity": 8, "tone_balance": 9, "scannability": 7},
    "summary": "Solid.",
    "highlights": ["Clear steps."],
    "rewrites": [{"original": "Pair the sensor", "suggestion": "## Pair it\nNow.", "why": "Shorter"}],
}


def groq_response(result: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(result)}}]}


def test_section_finds_content_bounds():
    body = "## A\none\n## B\ntwo\n"
    start, end = ev.section(body, "A")
    assert body[start:end] == "one\n"
    start, end = ev.section(body, "B")
    assert body[start:end] == "two\n"
    assert ev.section(body, "C") is None


@pytest.mark.parametrize("total,grade", [(30, "A"), (27, "A"), (26, "B"), (24, "B"), (21, "C"), (18, "D"), (17, "F")])
def test_grade_for_boundaries(total, grade):
    assert ev.grade_for(total) == grade


def test_normalize_rounds_and_clamps():
    result = ev.normalize({"scores": {"technical_clarity": "7.6", "tone_balance": 42, "scannability": -3}})
    assert result["scores"] == {"technical_clarity": 8, "tone_balance": 10, "scannability": 1}


def test_normalize_missing_scores_become_minimum():
    assert ev.normalize({})["scores"] == {c: 1 for c in ev.CRITERIA}


def test_normalize_rejects_non_numeric():
    with pytest.raises(DocGymError, match="invalid score for tone_balance"):
        ev.normalize({"scores": {"technical_clarity": 5, "tone_balance": "great", "scannability": 5}})


def test_call_groq_requires_key():
    with pytest.raises(DocGymError, match="GROQ_API_KEY is not set"):
        ev.call_groq("a", "d")


def test_call_groq_parses_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    seen = {}

    def http_json(url, payload, headers=None, timeout=180, context=None):
        seen.update(url=url, payload=payload, headers=headers)
        return groq_response(RESULT)
    monkeypatch.setattr(ev, "http_json", http_json)
    assert ev.call_groq("assignment", "draft") == RESULT
    assert seen["url"] == ev.GROQ_URL
    assert seen["payload"]["model"] == ev.GROQ_MODEL
    assert seen["headers"] == {"Authorization": "Bearer fake-key"}
    assert "DRAFT:\ndraft" in seen["payload"]["messages"][1]["content"]


def test_evaluate_without_fallback_raises_user_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(ev, "http_json", lambda *a, **k: {"choices": []})
    with pytest.raises(DocGymError, match="Groq evaluation failed"):
        ev.evaluate("a", "d")


def test_evaluate_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")

    def http_json(url, payload, headers=None, timeout=180, context=None):
        if url == ev.GROQ_URL:
            raise RuntimeError("HTTP 503 from groq")
        assert headers == {"x-goog-api-key": "fake-gemini"}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(RESULT)}]}}]}
    monkeypatch.setattr(ev, "http_json", http_json)
    result, engine = ev.evaluate("a", "d")
    assert result == RESULT
    assert engine.startswith("Gemini") and "fallback" in engine


def test_evaluate_both_engines_failing_is_user_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setattr(ev, "http_json", lambda *a, **k: {})
    with pytest.raises(DocGymError, match="Gemini fallback failed"):
        ev.evaluate("a", "d")


def test_evaluate_malformed_groq_json_without_fallback(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(ev, "http_json", lambda *a, **k: {"choices": [{"message": {"content": "{nope"}}]})
    with pytest.raises(DocGymError):
        ev.evaluate("a", "d")


def test_grade_note_writes_evaluation_and_frontmatter(monkeypatch):
    seen = {}

    def evaluate(assignment, draft):
        seen.update(assignment=assignment, draft=draft)
        return json.loads(json.dumps(RESULT)), "Test engine"
    monkeypatch.setattr(ev, "evaluate", evaluate)
    content, grade, total = ev.grade_note(NOTE)
    assert (grade, total) == ("B", 24)
    assert "<!--" not in seen["draft"] and seen["draft"].startswith("## Step one")
    assert "## Scenario" in seen["assignment"] and "## Draft" not in seen["assignment"]
    assert "status: completed" in content
    assert "grade: B" in content and "score: 24/30" in content
    assert "> [!abstract] Grade **B** · 24/30" in content
    assert "| Tone Balance | 9/10 |" in content
    assert "##### Pair it" in content
    assert "*Evaluated " in content and "by Test engine.*" in content


def test_grade_note_replaces_previous_evaluation(monkeypatch):
    monkeypatch.setattr(ev, "evaluate", lambda a, d: (json.loads(json.dumps(RESULT)), "Test engine"))
    once, _, _ = ev.grade_note(NOTE)
    twice, _, _ = ev.grade_note(once)
    assert twice.count("## Evaluation") == 1
    assert twice.count("status: completed") == 1


def test_grade_note_dry_run_makes_no_calls(monkeypatch):
    monkeypatch.setattr(ev, "evaluate", lambda a, d: pytest.fail("evaluate called in dry run"))
    content, grade, total = ev.grade_note(NOTE, dry_run=True)
    assert (grade, total) == ("C", 21)
    assert "dry run" in content


def test_grade_note_requires_draft_section():
    with pytest.raises(DocGymError, match="No '## Draft' section"):
        ev.grade_note("# Note\n\n## Scenario\ntext\n")


def test_grade_note_rejects_short_draft():
    with pytest.raises(DocGymError, match="under 20 words"):
        ev.grade_note("## Draft\n<!-- placeholder -->\nToo short.\n")
