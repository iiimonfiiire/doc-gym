#!/usr/bin/env python3
"""Generate a Doc Gym workout with a local Ollama model and write it to the vault.

A workout gives the writer raw inputs (commit messages, engineer notes, support tickets...)
to turn into finished docs, so the exercise is about writing, not inventing a product.
The model only returns structured data; this script builds the note, so the format is fixed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re

from common import GYM_FOLDER, DocGymError, die, env, http_json, load_env, ollama_url, vault_dir, write_note

INTENSITY = {
    "Sprint": {"words": "120-200", "minutes": 15},
    "Routine": {"words": "300-500", "minutes": 30},
    "Endurance": {"words": "800-1000", "minutes": 60},
}
FOCUS = {
    "SaaS Docs": "a help-center article for a fictional SaaS product",
    "API Specs": "reference docs for ONE endpoint of a fictional REST API",
    "Troubleshooting": "a troubleshooting guide for one specific, realistic error users hit in a fictional product",
    "Release Notes": "release notes for one version of a fictional product",
}
# Old names still sent by templates installed before a rename.
FOCUS_ALIASES = {"Whimsical SaaS Docs": "SaaS Docs"}
# What the raw inputs look like for each focus, so the writer has real facts to work from.
FOCUS_INPUTS = {
    "SaaS Docs": "spec bullets from the product manager about one feature, including the exact menu path, "
                 "button labels and setting values a user needs, plus 1-2 quoted customer questions about it",
    "API Specs": "an engineer's rough notes on the endpoint: method and path, each parameter with type and whether "
                 "it is required, the success response fields, and 2 error codes with their causes",
    "Troubleshooting": "the exact error message, 2 quoted support tickets from users who hit it, "
                       "and the engineering team's note on the real cause and the fix",
    "Release Notes": "the version number, then 5-7 raw commit messages in conventional-commit form "
                     "(type(scope): summary) about this product's own features, including at least one breaking change",
}
# One fixed structural requirement per focus, checked by the grader.
FOCUS_MUST = {
    "SaaS Docs": "Open with one sentence on what the reader will achieve, then give a numbered procedure",
    "API Specs": "Include a parameters table (name, type, required, description) and one error response",
    "Troubleshooting": "Structure it as symptom, cause, fix, and how to confirm the fix worked",
    "Release Notes": "Group changes under New, Improved, Fixed, and Breaking, with a migration note",
}
# Tone is picked at random so tone control gets practiced too. The grader scores against it.
TONES = {
    "Neutral": "plain and professional, no jokes",
    "Friendly": "warm and conversational, still concise",
    "Playful": "whimsy welcome, one light joke or playful phrase per section, but every step and fact stays exact",
}
# Small models repeat the same domains, so the industry is picked here.
INDUSTRIES = [
    "pet care", "urban beekeeping", "indie game studios", "dental clinics", "food trucks", "climbing gyms",
    "podcast production", "community gardens", "bike repair shops", "wedding planning", "home brewing",
    "school bus fleets", "vinyl record stores", "coworking spaces", "drone surveying", "yoga studios",
    "kayak rentals", "tattoo parlors", "escape rooms", "farmers markets", "cybersecurity", "fintech", "edtech",
]
PLACEHOLDER = "<!-- Write your draft here. Delete this comment when you start. -->"
# Bump when build_prompt() or clean() changes, so the service discards pre-generated workouts.
PROMPT_VERSION = 2


def build_prompt(focus: str, industry: str) -> str:
    return (
        "You create realistic practice assignments for a senior technical writer.\n"
        f"The writer must produce {FOCUS[focus]}. The fictional product serves the {industry} industry.\n"
        f"Give the writer raw inputs to turn into the document: {FOCUS_INPUTS[focus]}.\n"
        "Raw inputs are unpolished facts as a colleague would hand them over. Do not write the document itself.\n"
        "Requirements are about the writing (structure, audience, what to include), not product features.\n"
        "Requirements must be doable in plain Markdown text: no screenshots, images or videos.\n"
        "Do not set any tone or voice requirement; tone is assigned separately.\n"
        "Reply with JSON only, in exactly this shape:\n"
        '{"title": "<catchy title, max 8 words, names the product>",\n'
        ' "audience": "<who reads the document, one short phrase>",\n'
        ' "context": "<2-3 sentences: what the product does and why this document is needed now>",\n'
        ' "inputs": ["<raw input 1>", "<raw input 2>", "..."],\n'
        ' "requirements": ["<writing requirement 1>", "<writing requirement 2>"]}'
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
        raise DocGymError("The model returned malformed JSON. Try again.") from e


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
        raise DocGymError("The model returned too little to work with. Try again.")
    return workout


def slugify(title: str) -> str:
    words = re.sub(r"[^A-Za-z0-9 ]+", "", title).split()
    return "-".join(w.capitalize() for w in words[:8]) or "Workout"


def render(w: dict, intensity: str, focus: str, tone: str, date: str) -> str:
    spec = INTENSITY[intensity]
    lines = [
        "---",
        "tags: [doc-gym]",
        "status: pending",
        f"date: {date}",
        f"intensity: {intensity}",
        f'focus: "{focus}"',
        f"tone: {tone}",
        f'title: "{w["title"].replace(chr(34), "")}"',
        "---",
        f"# {w['title']}",
        "",
        f"> **{intensity}** · {focus} · {tone} tone · {spec['words']} words · {spec['minutes']} min",
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
        "## Requirements",
        *[f"- {r}" for r in w["requirements"]],
        f"- {FOCUS_MUST[focus]}.",
        f"- Tone: {TONES[tone]}.",
        "",
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


def make_workout(focus: str) -> dict:
    """The slow part: ask the model for a workout. Independent of intensity, so it can be pre-generated."""
    try:
        data = call_ollama(build_prompt(focus, random.choice(INDUSTRIES)))
    except RuntimeError as e:
        raise DocGymError(f"Ollama failed: {e}") from e
    return clean(data, focus)


def render_note(workout: dict, intensity: str, focus: str) -> tuple[str, str]:
    """The fast part: date, random tone, and intensity. Returns (file stem, note content)."""
    date = dt.date.today().isoformat()
    tone = random.choice(list(TONES))
    return f"{date}-{slugify(workout['title'])}", render(workout, intensity, focus, tone, date)


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
