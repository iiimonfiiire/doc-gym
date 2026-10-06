#!/usr/bin/env python3
"""Generate a Doc Gym workout with a local Ollama model and write it to the vault.

A workout gives the writer raw inputs (commit messages, engineer notes, support tickets...)
to turn into finished docs, so the exercise is about writing, not inventing a product.
The model only returns structured data; this script builds the note, so the format is fixed.
The Signal requirements come from the active style guide's rules pack, through PAKT.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re

import signal_guide
from common import GYM_FOLDER, DocGymError, die, env, http_json, load_env, ollama_url, vault_dir, write_note

INTENSITY = {
    "Sprint": {"words": "120-200", "minutes": 15},
    "Routine": {"words": "300-500", "minutes": 30},
    "Endurance": {"words": "800-1000", "minutes": 60},
}
FOCUS = {
    "SaaS Docs": "a task-based help-center article for one feature of a fictional SaaS product",
    "API Specs": "reference docs for ONE endpoint of a fictional REST API",
    "Troubleshooting": "a troubleshooting guide for one specific, realistic error users hit in a fictional product",
    "Release Notes": "release notes for one version of a fictional product",
    "UX Microcopy": "the UI strings for one screen of a fictional app: buttons, labels, an error message, "
                    "an empty state, and a success toast",
    "GTM Brief": "a go-to-market brief that hands one feature launch of a fictional product to sales, support, and marketing",
    "Product Walkthrough": "a guided walkthrough of one multi-screen task in a fictional product's UI",
    "Tutorial": "a tutorial that takes a newcomer from zero to a working result with a fictional product",
}
# Old names still sent by templates installed before a rename.
FOCUS_ALIASES = {"Whimsical SaaS Docs": "SaaS Docs"}
# The PAKT content type each focus practices. The active style guide supplies its rules and requirements.
FOCUS_TYPE = {
    "SaaS Docs": "kb_task",
    "API Specs": "api_doc",
    "Troubleshooting": "kb_troubleshooting",
    "Release Notes": "release_note",
    "UX Microcopy": "ui_microcopy",
    "GTM Brief": "gtm_brief",
    "Product Walkthrough": "walkthrough",
    "Tutorial": "tutorial",
}
# A Sprint is too short for a full tutorial, so it practices a quickstart instead.
INTENSITY_TYPE = {("Tutorial", "Sprint"): "quickstart"}
# What the raw inputs look like for each focus, so the writer has real facts to work from.
FOCUS_INPUTS = {
    "SaaS Docs": "spec bullets from the product manager about one feature: the goal a user wants, what they need first "
                 "(plan, role, or setting), the exact menu path, button labels and setting values, how a user can tell "
                 "it worked, plus 1-2 quoted customer questions about it",
    "API Specs": "an engineer's rough notes on the endpoint: method and path, each parameter with type and whether it is "
                 "required, the auth scheme, a sample curl request that pastes a real-looking API token, the success "
                 "response fields, and 2 error codes with their causes",
    "Troubleshooting": "the exact error message, the app versions or platforms where it happens, 2 quoted support tickets "
                       "from users who hit it, the engineering team's note on the real cause and the fix, "
                       "and how to confirm the fix worked",
    "Release Notes": "the version number and release date, then 6-8 raw commit messages in conventional-commit form "
                     "(type(scope): summary) about this product's own features: at least one new feature, one "
                     "improvement, one bug fix, one security fix, one API change, and one deprecation that names its "
                     "end-of-life date and its replacement",
    "UX Microcopy": "a designer's notes on one screen: what each button, label, and field does, the current draft "
                    "strings (some wordy, some in Title Case, one error message that explains the technical reason), "
                    "one error case with what the user can do next, the empty state, and the success event",
    "GTM Brief": "the product manager's launch notes: the internal codename, the official feature name, the target "
                 "customer and their pain, what the feature does and its scope, 2 known limitations or edge cases, "
                 "and a competitor's equivalent with how this one differs",
    "Product Walkthrough": "a designer's flow notes for one task across 3-4 screens: each screen's name, the exact labels "
                           "of its buttons, fields, and toggles, the values to enter, and what changes after each "
                           "action, on desktop",
    "Tutorial": "an engineer's notes for a newcomer: what the learner will build, 2-3 concepts they need first, the "
                "ordered setup and build steps with exact commands or menu paths, and how to check that the result works",
}
# Word targets don't fit every focus. These replace the intensity's word count.
LENGTH_OVERRIDES = {
    "UX Microcopy": {"Sprint": "6 strings", "Routine": "12 strings", "Endurance": "20 strings across 2 screens"},
    "GTM Brief": {"Sprint": "5 sections, 1-2 bullets each", "Routine": "5 sections, 3-4 bullets each",
                  "Endurance": "5 sections, 5-6 bullets each"},
}
# How to lay out a draft so PAKT's checks can read it. {kinds} lists PAKT's UI string kinds.
FOCUS_FORMAT = {
    "UX Microcopy": "Write one string per line as `kind: text`, such as `button: Save changes`. Kinds: {kinds}.",
    "Release Notes": "Use level-2 (`##`) headings for the sections. Put each deprecation in a `> **Warning:**` callout.",
}
# Small models repeat the same domains, so the industry is picked here.
INDUSTRIES = [
    "pet care", "urban beekeeping", "indie game studios", "dental clinics", "food trucks", "climbing gyms",
    "podcast production", "community gardens", "bike repair shops", "wedding planning", "home brewing",
    "school bus fleets", "vinyl record stores", "coworking spaces", "drone surveying", "yoga studios",
    "kayak rentals", "tattoo parlors", "escape rooms", "farmers markets", "cybersecurity", "fintech", "edtech",
]
PLACEHOLDER = "<!-- Write your draft here. Delete this comment when you start. -->"


class ThinOutput(DocGymError):
    """The model answered, but unusably (bad JSON, too little content). Worth retrying."""
# Bump when build_prompt() or clean() changes, so the service discards pre-generated workouts.
PROMPT_VERSION = 3


def build_prompt(focus: str, industry: str) -> str:
    return (
        "You create realistic practice assignments for a senior technical writer.\n"
        f"The writer must produce {FOCUS[focus]}. The fictional product serves the {industry} industry.\n"
        f"Give the writer raw inputs to turn into the document: {FOCUS_INPUTS[focus]}.\n"
        "Raw inputs are unpolished facts as a colleague would hand them over. Do not write the document itself.\n"
        "Requirements say what the document must cover for its audience. Never set structure, headings, tone, "
        "or voice: a style guide sets those.\n"
        "Requirements must be doable in plain Markdown text: no screenshots, images or videos.\n"
        "Reply with JSON only, in exactly this shape:\n"
        '{"title": "<catchy title, max 8 words, names the product>",\n'
        ' "audience": "<who reads the document, one short phrase>",\n'
        ' "context": "<2-3 sentences: what the product does and why this document is needed now>",\n'
        ' "inputs": ["<raw input 1>", "<raw input 2>", "..."],\n'
        ' "requirements": ["<what to cover 1>", "<what to cover 2>"]}'
    )


def call_ollama(prompt: str) -> dict:
    # A workout needs under 1k tokens; a 2k context keeps the KV cache small on low-RAM servers.
    options = {"temperature": 0.7, "num_predict": int(env("OLLAMA_NUM_PREDICT", "700")),
               "num_ctx": int(env("OLLAMA_NUM_CTX", "2048"))}
    if env("OLLAMA_NUM_THREAD"):  # cap threads on hosts that run hot
        options["num_thread"] = int(env("OLLAMA_NUM_THREAD"))
    resp = http_json(f"{ollama_url()}/api/generate", {
        "model": env("OLLAMA_MODEL", "llama3.2:3b"),
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": options,
    }, timeout=int(env("OLLAMA_TIMEOUT", "300")))
    try:
        return json.loads(resp.get("response", ""))
    except json.JSONDecodeError as e:
        raise ThinOutput("The model returned malformed JSON. Try again.") from e


def as_lines(value, limit: int) -> list[str]:
    items = [value] if isinstance(value, str) else (value or [])
    lines = []
    for item in items:
        text = str(item).strip().lstrip("-• ").strip()
        # Models sometimes cram several requirements into one long string; split it into sentences.
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if len(text) > 160 else [text]
        lines += [p.strip() for p in parts if p.strip()]
    return lines[:limit]


def clean(data: dict, focus: str) -> dict:
    title = re.sub(r"^(product|title)\s*:\s*", "", str(data.get("title") or "").strip().strip('"'), flags=re.I)
    workout = {
        "title": (title or focus)[:80],
        "audience": str(data.get("audience") or "").strip(),
        "context": str(data.get("context") or "").strip(),
        "inputs": as_lines(data.get("inputs"), 8),
        "requirements": as_lines(data.get("requirements"), 3),
    }
    if not workout["context"] or len(workout["inputs"]) < 2:
        raise ThinOutput("The model returned too little to work with. Try again.")
    return workout


def slugify(title: str) -> str:
    words = re.sub(r"[^A-Za-z0-9 ]+", "", title).split()
    return "-".join(w.capitalize() for w in words[:8]) or "Workout"


def signal_type(focus: str, intensity: str) -> str:
    return INTENSITY_TYPE.get((focus, intensity), FOCUS_TYPE[focus])


def length_for(focus: str, intensity: str) -> str:
    return LENGTH_OVERRIDES.get(focus, {}).get(intensity) or f"{INTENSITY[intensity]['words']} words"


def _rule_line(tr, rule_id: str, text: str) -> str:
    return f"- {text} (`{rule_id}`{', checked' if signal_guide.is_checked(tr, rule_id) else ''})"


def signal_section(focus: str, type_id: str) -> tuple[list[str], str]:
    """The workout's Signal requirements, built from the rules pack. Returns (lines, guide label).
    Without the guide, the workout still renders, with a warning in place of the requirements."""
    try:
        tr = signal_guide.type_rules(type_id)
    except DocGymError as e:
        return ["## Signal requirements",
                f"> [!warning] The style guide did not load, so this workout lists no Signal requirements. "
                f"Grading needs the guide. {e}", ""], ""
    lines = [
        "## Signal requirements",
        f"Graded against **{tr.guide_label}** (`{tr.guide_source}`) as a **{tr.type_name}** document (`{tr.type_id}`). "
        "Rules marked *checked* run as automatic checks when you grade.",
        "",
        "### Voice",
        *[_rule_line(tr, i, t) for i, t in tr.voice],
        "",
        "### Structure",
        *([_rule_line(tr, r.id, r.summary) for r in tr.requirements] or ["- No structural requirements for this type."]),
        "",
        "### Style",
        *[_rule_line(tr, i, t) for i, t in tr.style],
    ]
    if tr.also_checked:
        lines.append(f"- Also checked: {', '.join(f'`{i}`' for i in tr.also_checked)}.")
    if focus in FOCUS_FORMAT:
        kinds = ", ".join(f"`{k}`" for k in tr.ui_kinds)
        lines += ["", "### Format", f"- {FOCUS_FORMAT[focus].format(kinds=kinds)}"]
    return lines + [""], tr.guide_label


def render(w: dict, intensity: str, focus: str, date: str) -> str:
    spec = INTENSITY[intensity]
    type_id = signal_type(focus, intensity)
    signal_lines, guide = signal_section(focus, type_id)
    lines = [
        "---",
        "tags: [doc-gym]",
        "status: pending",
        f"date: {date}",
        f"intensity: {intensity}",
        f'focus: "{focus}"',
        f"signal_type: {type_id}",
        *([f'guide: "{guide}"'] if guide else []),
        f'title: "{w["title"].replace(chr(34), "")}"',
        "---",
        f"# {w['title']}",
        "",
        f"> **{intensity}** · {focus} · {length_for(focus, intensity)} · {spec['minutes']} min",
    ]
    if w["audience"]:
        lines.append(f"> **Audience:** {w['audience']}")
    lines += [
        "",
        "## Scenario",
        w["context"],
        "",
        "## Raw inputs",
        *[f"- {i}" for i in w["inputs"]],
        "",
    ]
    if w["requirements"]:
        lines += ["## Requirements", *[f"- {r}" for r in w["requirements"]], ""]
    lines += [
        *signal_lines,
        "## Draft",
        PLACEHOLDER,
        "",
    ]
    return "\n".join(lines)


def check_choice(intensity: str, focus: str) -> str:
    """Validate the picker values. Returns the canonical focus name."""
    focus = FOCUS_ALIASES.get(focus, focus)
    if intensity not in INTENSITY or focus not in FOCUS:
        raise DocGymError(f"Unknown intensity/focus: {intensity!r} / {focus!r}")
    return focus


def make_workout(focus: str, attempts: int = 2) -> dict:
    """The slow part: ask the model for a workout. Independent of intensity, so it can be pre-generated.
    Retries unusable answers (ThinOutput); an unreachable Ollama fails at once."""
    for attempt in range(1, attempts + 1):
        try:
            data = call_ollama(build_prompt(focus, random.choice(INDUSTRIES)))
            return clean(data, focus)
        except RuntimeError as e:
            raise DocGymError(f"Ollama failed: {e}") from e
        except ThinOutput:
            if attempt == attempts:
                raise


def render_note(workout: dict, intensity: str, focus: str) -> tuple[str, str]:
    """The fast part: date, intensity, and the Signal requirements. Returns (file stem, note content)."""
    date = dt.date.today().isoformat()
    return f"{date}-{slugify(workout['title'])}", render(workout, intensity, focus, date)


def generate_note(intensity: str, focus: str) -> tuple[str, str]:
    """Generate a workout from scratch. Returns (file stem like 2026-10-01-Title, note content)."""
    focus = check_choice(intensity, focus)
    return render_note(make_workout(focus), intensity, focus)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intensity", required=True, choices=list(INTENSITY))
    ap.add_argument("--focus", required=True, choices=list(FOCUS))
    ap.add_argument("--dry-run", action="store_true", help="print the note instead of writing it")
    args = ap.parse_args()
    load_env()

    try:
        stem, content = generate_note(args.intensity, args.focus)
    except DocGymError as e:
        die(f"{e}\nIs Ollama reachable? Try: curl {ollama_url()}/api/tags")

    if args.dry_run:
        print(content)
        return

    prompts = vault_dir() / GYM_FOLDER / "Prompts"
    path, n = prompts / f"{stem}.md", 2
    while path.exists():
        path, n = prompts / f"{stem}-{n}.md", n + 1
    rel = f"{GYM_FOLDER}/Prompts/{path.name}"
    write_note(path, rel, content)
    print(rel)


if __name__ == "__main__":
    try:
        main()
    except DocGymError as e:
        die(str(e))
