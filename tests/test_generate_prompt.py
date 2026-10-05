from __future__ import annotations

import json

import pytest

import generate_prompt as gp
from common import DocGymError


def fake_ollama(monkeypatch, *responses):
    """Patch the HTTP layer so each Ollama call returns the next item. Returns the call log."""
    calls = []
    queue = list(responses)

    def http_json(url, payload, headers=None, timeout=180, context=None):
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"response": item if isinstance(item, str) else json.dumps(item)}
    monkeypatch.setattr(gp, "http_json", http_json)
    return calls


def test_build_prompt_mentions_focus_and_industry():
    prompt = gp.build_prompt("API Specs", "food trucks")
    assert gp.FOCUS["API Specs"] in prompt
    assert "food trucks industry" in prompt
    assert '"requirements"' in prompt


def test_as_lines_strips_bullets_splits_long_strings_and_limits():
    assert gp.as_lines("- one", 5) == ["one"]
    assert gp.as_lines(None, 5) == []
    assert gp.as_lines(["• a", "  ", "b"], 1) == ["a"]
    long = "First requirement is long enough to matter in this test. " * 2 + "Second one follows it closely. Third."
    long += " Fourth sentence makes it long enough."
    assert len(long) > 160
    assert len(gp.as_lines(long, 10)) == 5
    assert len(gp.as_lines("Short. Not split.", 10)) == 1


def test_clean_normalizes_model_output(model_json):
    w = gp.clean(model_json, "SaaS Docs")
    assert w["title"] == "HiveSync Sensor Pairing"
    assert w["inputs"][0] == "Settings > Sensors > Pair"
    assert len(w["requirements"]) == 2


def test_clean_caps_lists_and_title(model_json):
    model_json["title"] = "x" * 200
    model_json["inputs"] = [f"input {i}" for i in range(20)]
    model_json["requirements"] = [f"req {i}" for i in range(9)]
    w = gp.clean(model_json, "SaaS Docs")
    assert len(w["title"]) == 80
    assert len(w["inputs"]) == 8
    assert len(w["requirements"]) == 3


def test_clean_falls_back_to_focus_for_missing_title(model_json):
    del model_json["title"]
    assert gp.clean(model_json, "API Specs")["title"] == "API Specs"


@pytest.mark.parametrize("change", [
    {"context": ""},
    {"inputs": ["only one"]},
    {"inputs": None},
    {"context": None, "inputs": []},
])
def test_clean_rejects_thin_output(model_json, change):
    model_json.update(change)
    with pytest.raises(gp.ThinOutput):
        gp.clean(model_json, "SaaS Docs")


def test_slugify():
    assert gp.slugify("Fix the API: v2 (beta)!") == "Fix-The-Api-V2-Beta"
    assert gp.slugify("!!!") == "Workout"
    assert gp.slugify("a b c d e f g h i j") == "A-B-C-D-E-F-G-H"


def test_render_produces_fixed_note_format(workout):
    note = gp.render(workout, "Sprint", "Release Notes", "Playful", "2026-01-02")
    assert note.startswith("---\ntags: [doc-gym]\nstatus: pending\ndate: 2026-01-02\nintensity: Sprint\n")
    assert 'focus: "Release Notes"' in note
    assert "# Hive Sync Setup" in note
    assert "120-200 words · 15 min" in note
    assert "> **Audience:** beekeepers new to the app" in note
    assert "- Settings > Sensors > Pair" in note
    assert f"- {gp.FOCUS_MUST['Release Notes']}." in note
    assert f"- Tone: {gp.TONES['Playful']}." in note
    assert note.rstrip().endswith(gp.PLACEHOLDER)


def test_render_strips_quotes_from_frontmatter_title(workout):
    workout["title"] = 'The "Best" Guide'
    note = gp.render(workout, "Routine", "SaaS Docs", "Neutral", "2026-01-02")
    assert 'title: "The Best Guide"' in note


def test_render_omits_empty_audience(workout):
    workout["audience"] = ""
    assert "Audience" not in gp.render(workout, "Routine", "SaaS Docs", "Neutral", "2026-01-02")


def test_check_choice_accepts_aliases_and_rejects_unknowns():
    assert gp.check_choice("Sprint", "Whimsical SaaS Docs") == "SaaS Docs"
    assert gp.check_choice("Endurance", "API Specs") == "API Specs"
    with pytest.raises(DocGymError, match="Unknown intensity/focus"):
        gp.check_choice("Marathon", "API Specs")
    with pytest.raises(DocGymError):
        gp.check_choice("Sprint", "Poetry")


def test_call_ollama_sends_options_from_env(monkeypatch, model_json):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.invalid:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "tiny:1b")
    monkeypatch.setenv("OLLAMA_NUM_THREAD", "2")
    calls = fake_ollama(monkeypatch, model_json)
    assert gp.call_ollama("prompt") == model_json
    call = calls[0]
    assert call["url"] == "http://ollama.invalid:11434/api/generate"
    assert call["payload"]["model"] == "tiny:1b"
    assert call["payload"]["format"] == "json"
    assert call["payload"]["options"] == {"temperature": 0.7, "num_predict": 700, "num_ctx": 2048, "num_thread": 2}
    assert call["timeout"] == 300


def test_call_ollama_malformed_json_is_thin_output(monkeypatch):
    fake_ollama(monkeypatch, "{not json")
    with pytest.raises(gp.ThinOutput, match="malformed JSON"):
        gp.call_ollama("prompt")


def test_make_workout_retries_thin_output(monkeypatch, model_json):
    calls = fake_ollama(monkeypatch, {"context": "", "inputs": []}, model_json)
    w = gp.make_workout("SaaS Docs")
    assert w["title"] == "HiveSync Sensor Pairing"
    assert len(calls) == 2


def test_make_workout_gives_up_after_attempts(monkeypatch):
    calls = fake_ollama(monkeypatch, "{bad", "{bad", "{bad")
    with pytest.raises(gp.ThinOutput):
        gp.make_workout("SaaS Docs", attempts=3)
    assert len(calls) == 3


def test_make_workout_unreachable_ollama_fails_without_retry(monkeypatch, model_json):
    calls = fake_ollama(monkeypatch, RuntimeError("Cannot reach ollama"), model_json)
    with pytest.raises(DocGymError, match="Ollama failed: Cannot reach ollama"):
        gp.make_workout("SaaS Docs")
    assert len(calls) == 1


def test_generate_note_end_to_end(monkeypatch, model_json):
    fake_ollama(monkeypatch, model_json)
    stem, content = gp.generate_note("Sprint", "Whimsical SaaS Docs")
    assert stem.endswith("-Hivesync-Sensor-Pairing")
    assert 'focus: "SaaS Docs"' in content
    assert "intensity: Sprint" in content


def test_generate_note_validates_before_calling_model(monkeypatch):
    calls = fake_ollama(monkeypatch)
    with pytest.raises(DocGymError):
        gp.generate_note("Sprint", "Nope")
    assert calls == []
