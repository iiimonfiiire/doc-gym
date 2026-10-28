from __future__ import annotations

import json

import pytest

import generate_prompt as gp
import signal_guide
from common import DocGymError
from conftest import ROOT, needs_pakt


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
    assert "Never set structure, headings, tone, or voice" in prompt


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


@needs_pakt
def test_render_produces_fixed_note_format(workout):
    note = gp.render(workout, "Sprint", "Release Notes", "2026-01-02")
    assert note.startswith("---\ntags: [doc-gym]\nstatus: pending\ndate: 2026-01-02\nintensity: Sprint\n")
    assert 'focus: "Release Notes"\nsignal_type: release_note\nguide: "Signal 2026-10-27"\n' in note
    assert "# Hive Sync Setup" in note
    assert "> **Sprint** · Release Notes · 120-200 words · 15 min" in note
    assert "> **Audience:** beekeepers new to the app" in note
    assert "- Settings > Sensors > Pair" in note
    assert "## Requirements\n- Explain pairing\n- Mention battery level\n" in note
    assert "Graded against **Signal 2026-10-27** (`github:iiimonfiiire/signal-style-guide@v2026.10.27`)" in note
    assert note.index("## Requirements") < note.index("## Signal requirements") < note.index("## Draft")
    assert note.rstrip().endswith(gp.PLACEHOLDER)


def test_no_tones_left(workout):
    assert not hasattr(gp, "TONES")
    note = gp.render(workout, "Sprint", "SaaS Docs", "2026-01-02")
    assert "tone:" not in note and "Tone" not in note
    for path in [*(ROOT / "scripts").glob("*.py"), *(ROOT / "vault-scaffold").rglob("*.md")]:
        assert "Playful" not in path.read_text(), path


@needs_pakt
@pytest.mark.parametrize("focus,intensity,type_id,expected", [
    ("SaaS Docs", "Routine", "kb_task", ["`kb_task_sections`, checked", "`kb_numbered_steps`, checked"]),
    ("API Specs", "Routine", "api_doc", ["`api_params_table`, checked", "`api_masked_tokens`, checked"]),
    ("Troubleshooting", "Routine", "kb_troubleshooting", ["`ts_sections`, checked", "`ts_environment`, checked"]),
    ("Release Notes", "Routine", "release_note", ["`rn_sections`, checked", "`rn_deprecation_callout`, checked",
                                                  'write "we" for the company', "`> **Warning:**` callout"]),
    ("UX Microcopy", "Routine", "ui_microcopy", ["`ui_error_two_part`)", "12 words or fewer",
                                                 "Contractions are allowed", "Never use a semicolon anywhere",
                                                 "`button: Save changes`", "`empty_state`"]),
    ("GTM Brief", "Routine", "gtm_brief", ["`gtm_sections`, checked", "`gtm_codename`, checked",
                                           'write "we" for the company']),
    ("Product Walkthrough", "Routine", "walkthrough", ["`walk_ui_verbs`, checked", "`walk_step_density`)"]),
    ("Tutorial", "Routine", "tutorial", ["`learn_tutorial_sections`, checked"]),
    ("Tutorial", "Sprint", "quickstart", ["`learn_quickstart_no_concepts`)"]),
])
def test_signal_requirements_per_focus(workout, focus, intensity, type_id, expected):
    note = gp.render(workout, intensity, focus, "2026-01-02")
    assert f"signal_type: {type_id}\n" in note
    section = note[note.index("## Signal requirements"):note.index("## Draft")]
    assert f"(`{type_id}`)" in section
    for heading in ("### Voice", "### Structure", "### Style"):
        assert heading in section
    assert "- No jokes, asides, or rhetorical flourishes. (`no_humor`)" in section
    assert "(`second_person`, checked)" in section
    assert "- Also checked: " in section and "`oxford_comma`" in section
    for text in expected:
        assert text in section, text
    if type_id not in ("release_note", "gtm_brief"):
        assert "Never use first-person pronouns" in section
    if type_id != "ui_microcopy":
        assert "20 words or fewer" in section and "Never use contractions" in section
        assert "Never use a semicolon in a numbered step" in section


@needs_pakt
def test_requirements_come_from_the_rules_pack(workout, local_guide):
    pack = local_guide.read_text()
    pack = pack.replace("params = { max_words = 20 }", "params = { max_words = 17 }")
    pack = pack.replace('summary = "Write ordered steps as a numbered list."', 'summary = "Number every step."')
    local_guide.write_text(pack)
    note = gp.render(workout, "Routine", "SaaS Docs", "2026-01-02")
    assert "Keep every sentence to 17 words or fewer." in note
    assert "- Number every step. (`kb_numbered_steps`, checked)" in note


@needs_pakt
def test_every_focus_maps_to_a_pakt_type():
    assert set(gp.FOCUS) == set(gp.FOCUS_TYPE) == set(gp.FOCUS_INPUTS)
    types = signal_guide.load().types
    for focus in gp.FOCUS:
        for intensity in gp.INTENSITY:
            assert gp.signal_type(focus, intensity) in types


def test_release_note_inputs_include_a_deprecation_to_practice_the_callout():
    inputs = gp.FOCUS_INPUTS["Release Notes"]
    assert "deprecation" in inputs and "end-of-life date" in inputs and "replacement" in inputs
    assert "breaking change" not in inputs


@pytest.mark.parametrize("focus,intensity,length", [
    ("UX Microcopy", "Sprint", "6 strings"),
    ("UX Microcopy", "Endurance", "20 strings across 2 screens"),
    ("GTM Brief", "Routine", "5 sections, 3-4 bullets each"),
    ("API Specs", "Endurance", "800-1000 words"),
])
def test_length_overrides(focus, intensity, length):
    assert gp.length_for(focus, intensity) == length


def test_render_without_guide_still_renders(workout, no_guide):
    note = gp.render(workout, "Sprint", "UX Microcopy", "2026-01-02")
    assert "signal_type: ui_microcopy\n" in note
    assert "guide:" not in note
    assert "> **Sprint** · UX Microcopy · 6 strings · 15 min" in note
    assert "> [!warning] The style guide did not load" in note
    assert note.rstrip().endswith(gp.PLACEHOLDER)


def test_render_strips_quotes_from_frontmatter_title(workout):
    workout["title"] = 'The "Best" Guide'
    note = gp.render(workout, "Routine", "SaaS Docs", "2026-01-02")
    assert 'title: "The Best Guide"' in note


def test_render_omits_empty_audience_and_requirements(workout):
    workout["audience"] = ""
    workout["requirements"] = []
    note = gp.render(workout, "Routine", "SaaS Docs", "2026-01-02")
    assert "Audience" not in note
    assert "## Requirements" not in note


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
