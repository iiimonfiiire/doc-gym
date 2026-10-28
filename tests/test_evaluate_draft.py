from __future__ import annotations

import json

import pytest

import evaluate_draft as ev
from common import DocGymError
from conftest import needs_pakt

DRAFT = " ".join(["Pair the sensor from Settings, then confirm the battery level is above twenty percent."] * 3)
NOTE = f"""---
tags: [doc-gym]
status: pending
focus: "SaaS Docs"
signal_type: kb_task
title: "Pairing"
---
# Pairing

## Scenario
Users cannot pair sensors.

## Signal requirements
- Numbered procedure.

## Draft
<!-- Write your draft here. Delete this comment when you start. -->
## Step one
{DRAFT}
"""
RN_DRAFT = """We've shipped faster sync in this release, and you don't need to do anything.

## New features
- We added offline mode & dark mode for every user of the app today.
"""
RN_NOTE = f"""---
tags: [doc-gym]
status: pending
focus: "Release Notes"
signal_type: release_note
---
# Hive 2.4

## Draft
{RN_DRAFT}"""
# A note from before Signal grading: a random tone and the old fixed requirement.
LEGACY_NOTE = f"""---
tags: [doc-gym]
status: pending
date: 2026-09-20
intensity: Sprint
focus: "Whimsical SaaS Docs"
tone: Playful
title: "Pairing"
---
# Pairing

> **Sprint** · Whimsical SaaS Docs · Playful tone · 120-200 words · 15 min

## Requirements
- Open with one sentence on what the reader will achieve, then give a numbered procedure.
- Tone: whimsy welcome, one light joke or playful phrase per section, but every step and fact stays exact.

## Draft
{DRAFT}
"""
RESULT = {
    "scores": {"technical_clarity": 8, "voice": 9, "scannability": 7, "signal_compliance": 6},
    "summary": "Solid.",
    "highlights": ["Clear steps."],
    "findings": [{"rule": "no_humor", "quote": "Pair the sensor from Settings", "fix": "Keep it plain."}],
    "rewrites": [{"original": "Pair the sensor", "suggestion": "## Pair it\nNow.", "why": "Shorter"}],
}


def groq_response(result: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(result)}}]}


def fake_evaluate(monkeypatch, result=RESULT) -> dict:
    seen = {}

    def evaluate(assignment, draft, signal=""):
        seen.update(assignment=assignment, draft=draft, signal=signal)
        return json.loads(json.dumps(result)), "Test engine"
    monkeypatch.setattr(ev, "evaluate", evaluate)
    return seen


def test_section_finds_content_bounds():
    body = "## A\none\n## B\ntwo\n"
    start, end = ev.section(body, "A")
    assert body[start:end] == "one\n"
    start, end = ev.section(body, "B")
    assert body[start:end] == "two\n"
    assert ev.section(body, "C") is None


def test_four_criteria_out_of_forty():
    assert ev.CRITERIA == ["technical_clarity", "voice", "scannability", "signal_compliance"]
    assert ev.LABELS["voice"] == "Voice and register"
    assert ev.MAX_SCORE == 40


@pytest.mark.parametrize("total,grade", [
    (40, "A"), (36, "A"), (35, "B"), (32, "B"), (31, "C"), (28, "C"), (27, "D"), (24, "D"), (23, "F"), (4, "F")])
def test_grade_for_boundaries(total, grade):
    assert ev.grade_for(total) == grade


def test_normalize_rounds_and_clamps():
    result = ev.normalize({"scores": {"technical_clarity": "7.6", "voice": 42, "scannability": -3, "signal_compliance": 9.4}})
    assert result["scores"] == {"technical_clarity": 8, "voice": 10, "scannability": 1, "signal_compliance": 9}


def test_normalize_missing_scores_become_minimum():
    assert ev.normalize({})["scores"] == {c: 1 for c in ev.CRITERIA}


def test_normalize_accepts_old_tone_balance_key():
    scores = ev.normalize({"scores": {"technical_clarity": 5, "tone_balance": 6, "scannability": 5}})["scores"]
    assert scores["voice"] == 6 and scores["signal_compliance"] == 1


def test_normalize_rejects_non_numeric():
    with pytest.raises(DocGymError, match="invalid score for signal_compliance"):
        ev.normalize({"scores": {"technical_clarity": 5, "voice": 5, "scannability": 5, "signal_compliance": "great"}})


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
    assert ev.call_groq("assignment", "draft", "rules here") == RESULT
    assert seen["url"] == ev.GROQ_URL
    assert seen["payload"]["model"] == ev.GROQ_MODEL
    assert seen["headers"] == {"Authorization": "Bearer fake-key"}
    assert "signal_compliance" in seen["payload"]["messages"][0]["content"]
    assert seen["payload"]["messages"][1]["content"] == "ASSIGNMENT:\nassignment\n\nSIGNAL:\nrules here\n\nDRAFT:\ndraft"


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
        assert "SIGNAL:\nrules" in payload["contents"][0]["parts"][0]["text"]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(RESULT)}]}}]}
    monkeypatch.setattr(ev, "http_json", http_json)
    result, engine = ev.evaluate("a", "d", "rules")
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


@needs_pakt
def test_grade_note_writes_evaluation_and_frontmatter(monkeypatch):
    seen = fake_evaluate(monkeypatch)
    content, grade, total = ev.grade_note(NOTE)
    assert (grade, total) == ("C", 30)
    assert "<!--" not in seen["draft"] and seen["draft"].startswith("## Step one")
    assert "## Scenario" in seen["assignment"] and "## Draft" not in seen["assignment"]
    assert "status: completed" in content
    assert "grade: C" in content and "score: 30/40" in content
    assert "> [!abstract] Grade **C** · 30/40" in content
    assert "| Voice and register | 9/10 |" in content
    assert "| Signal compliance | 6/10 |" in content
    assert "| **Total** | **30/40** |" in content
    assert "##### Pair it" in content
    assert "*Evaluated " in content and "by Test engine.*" in content


@needs_pakt
def test_grader_gets_rules_and_deterministic_findings(monkeypatch):
    seen = fake_evaluate(monkeypatch)
    ev.grade_note(RN_NOTE)
    signal = seen["signal"]
    assert "Content type: Release notes (release_note)." in signal
    assert "- rn_sections [error, check]: Group every update under the six mandatory headings" in signal
    assert "- no_humor [warning, judgment]: No jokes, asides, or rhetorical flourishes." in signal
    assert "- first_person " not in signal  # off for release notes, so "we" is allowed
    assert "- contractions [error] line 1: contraction: We've" in signal
    assert "- ampersand [error] line 4:" in signal
    assert "- rn_sections [error]: missing heading: Improvements" in signal
    assert "legacy" not in signal


@needs_pakt
def test_deterministic_findings_render_first(monkeypatch):
    fake_evaluate(monkeypatch, {**RESULT, "findings": [
        {"rule": "rn_capability_first", "quote": "faster sync in this release", "fix": "Lead with what the reader can do."},
        {"rule": "no_humor", "quote": "a joke the draft never made", "fix": "Hallucinated quote."},
        {"rule": "made_up_rule", "quote": "offline mode", "fix": "Unknown rule."},
        {"rule": "contractions", "quote": "don't", "fix": "Checked rule, PAKT already covers it."},
    ]})
    content, _, _ = ev.grade_note(RN_NOTE)
    evaluation = content[content.index("## Evaluation"):]
    assert "### Signal findings\nChecked against Signal 2026-10-27 as Release notes (`release_note`)." in evaluation
    assert ("- **`contractions`** (error, line 1) – contraction: We've\n  - Quote: `We've`\n"
            "  - Fix: Write 'We have'.") in evaluation
    assert "- **`rn_sections`** (error) – missing heading: Improvements\n  - Fix: Use the six headings" in evaluation
    assert "- **`rn_engineering_framing`** (warning, line 4) – engineering framing: We added" in evaluation
    assert ("**Editor's judgment**\n\n- **`rn_capability_first`** (warning) – `faster sync in this release`\n"
            "  - Fix: Lead with what the reader can do.") in evaluation
    assert "Hallucinated" not in evaluation and "made_up_rule" not in evaluation
    assert "PAKT already covers it" not in evaluation
    order = ["| Criterion |", "**Automatic checks (PAKT)**", "**Editor's judgment**", "### What worked",
             "### Rewrite suggestions"]
    assert [evaluation.index(s) for s in order] == sorted(evaluation.index(s) for s in order)
    assert evaluation.index("`contractions`") < evaluation.index("`rn_engineering_framing`")  # errors first


@needs_pakt
def test_clean_draft_has_no_check_findings(monkeypatch):
    fake_evaluate(monkeypatch, {**RESULT, "findings": []})
    clean = """Pair a sensor to track hive health from your phone.

## Prerequisites
- A charged sensor.

## Procedure
1. Open **Settings**.
2. Select **Sensors**, then select **Pair**.
3. Hold the sensor next to the phone until the light turns green.

## Verification
The sensor appears in the **Sensors** list with its battery level.
"""
    content, _, _ = ev.grade_note(NOTE.replace(f"## Step one\n{DRAFT}\n", clean))
    assert "- None. Every automatic check passed." in content
    assert "- No further findings." in content


@needs_pakt
def test_grade_note_replaces_previous_evaluation(monkeypatch):
    fake_evaluate(monkeypatch)
    once, _, _ = ev.grade_note(NOTE)
    twice, _, _ = ev.grade_note(once)
    assert twice.count("## Evaluation") == 1
    assert twice.count("### Signal findings") == 1
    assert twice.count("status: completed") == 1


@needs_pakt
def test_legacy_note_is_graded_with_a_fallback(monkeypatch):
    seen = fake_evaluate(monkeypatch)
    content, grade, total = ev.grade_note(LEGACY_NOTE)
    assert (grade, total) == ("C", 30)
    assert "Tone: whimsy welcome" in seen["assignment"]
    assert "Content type: KB task-based guide (kb_task)." in seen["signal"]
    assert "This is a legacy assignment" in seen["signal"]
    assert "> [!note] This workout predates Signal grading." in content
    assert "score: 30/40" in content
    assert "tone: Playful" in content


@needs_pakt
def test_note_without_focus_uses_pakt_detection(monkeypatch):
    seen = fake_evaluate(monkeypatch)
    note = "# API notes\n\n## Draft\n" + "Call GET /v1/hives/{id} to read one hive. " * 4 + "\n"
    ev.grade_note(note)
    assert "Content type: API documentation (api_doc)." in seen["signal"]
    assert "This is a legacy assignment" not in seen["signal"]


@needs_pakt
def test_microcopy_findings_point_at_strings(monkeypatch):
    fake_evaluate(monkeypatch, {**RESULT, "findings": []})
    note = """---
focus: "UX Microcopy"
signal_type: ui_microcopy
---
## Draft
button: Save Changes
error: Upload failed because the file is too big; try a smaller file.
empty_state: No hives yet. Add your first hive to start tracking health.
toast: Hive saved.
"""
    content, _, _ = ev.grade_note(note)
    assert "- **`semicolons`** (error, line 2)" in content
    assert "- **`ui_case`** (warning, line 1) – title case button: Save Changes" in content
    assert "- **`ui_error_no_why`** (warning, line 2)" in content
    assert "`contractions`" not in content.split("### Signal findings")[1].split("### What worked")[0]


def test_grade_note_without_guide_is_a_clear_error(monkeypatch, no_guide):
    monkeypatch.setattr(ev, "evaluate", lambda *a: pytest.fail("no model call without the guide"))
    with pytest.raises(DocGymError, match="style guide|PAKT"):
        ev.grade_note(NOTE)


@needs_pakt
def test_grade_note_dry_run_makes_no_calls(monkeypatch):
    monkeypatch.setattr(ev, "evaluate", lambda *a: pytest.fail("evaluate called in dry run"))
    content, grade, total = ev.grade_note(NOTE, dry_run=True)
    assert (grade, total) == ("C", 28)
    assert "dry run" in content
    assert "### Signal findings" in content


def test_grade_note_requires_draft_section():
    with pytest.raises(DocGymError, match="No '## Draft' section"):
        ev.grade_note("# Note\n\n## Scenario\ntext\n")


def test_grade_note_rejects_short_draft():
    with pytest.raises(DocGymError, match="under 20 words"):
        ev.grade_note("## Draft\n<!-- placeholder -->\nToo short.\n")
