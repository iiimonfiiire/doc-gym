#!/usr/bin/env python3
"""Grade the ## Draft section of a Doc Gym note with Groq (Gemini fallback) and write the evaluation back."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re

from common import (DocGymError, die, env, http_json, join_frontmatter, load_env, resolve_note,
                    set_frontmatter, split_frontmatter, write_note)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile; gpt-oss-120b is its strongest general model as of 2026-10.
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CRITERIA = ["technical_clarity", "tone_balance", "scannability"]
LABELS = {
    "technical_clarity": "Technical Clarity",
    "tone_balance": "Tone Balance",
    "scannability": "Scannability & Formatting",
}

RUBRIC = """You are a strict senior documentation editor grading a practice draft.
Score each criterion from 1 to 10. Be demanding: 7 is solid professional work, 9-10 is rare.

technical_clarity: Is every step, term and behavior accurate, specific and unambiguous?
  1-3 vague or wrong; 4-6 understandable but hand-wavy or missing key details; 7-8 precise and complete; 9-10 nothing to fix.
tone_balance: Does the voice match the tone the assignment asks for (Neutral, Friendly or Playful) and suit its audience?
  Playful means whimsy that never costs clarity; Neutral means no jokes at all.
  1-3 wrong register or grating; 4-6 inconsistent; 7-8 fitting and consistent; 9-10 effortless and on-brand.
scannability: Headings, lists, short sentences, front-loaded key info, correct Markdown.
  1-3 wall of text; 4-6 some structure, hard to skim; 7-8 easy to skim; 9-10 a reader finds any answer in seconds.

Also check the draft against the assignment's constraints and length target; missed constraints must lower scores.

Reply with JSON only:
{"scores": {"technical_clarity": int, "tone_balance": int, "scannability": int},
 "summary": "one sentence verdict",
 "highlights": ["2-3 specific things done well, quoting the draft"],
 "rewrites": [{"original": "exact quote from the draft", "suggestion": "rewritten version", "why": "short reason"}]}
Give 3-5 rewrites, most impactful first."""

PLACEHOLDER_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def section(body: str, heading: str) -> tuple[int, int] | None:
    """Return (start, end) of a `## heading` section's content, or None."""
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n", body, re.MULTILINE)
    if not m:
        return None
    nxt = re.search(r"^## ", body[m.end():], re.MULTILINE)
    return m.end(), (m.end() + nxt.start()) if nxt else len(body)


def grade_for(total: int) -> str:
    for letter, floor in (("A", 27), ("B", 24), ("C", 21), ("D", 18)):
        if total >= floor:
            return letter
    return "F"


def call_groq(assignment: str, draft: str) -> dict:
    key = env("GROQ_API_KEY", required=True)
    resp = http_json(GROQ_URL, {
        "model": env("GROQ_MODEL", GROQ_MODEL),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": f"ASSIGNMENT:\n{assignment}\n\nDRAFT:\n{draft}"},
        ],
    }, headers={"Authorization": f"Bearer {key}"}, timeout=90)
    return json.loads(resp["choices"][0]["message"]["content"])


def call_gemini(assignment: str, draft: str) -> dict:
    key = env("GEMINI_API_KEY", required=True)
    model = env("GEMINI_MODEL", "gemini-2.5-flash")
    resp = http_json(GEMINI_URL.format(model=model), {
        "systemInstruction": {"parts": [{"text": RUBRIC}]},
        "contents": [{"parts": [{"text": f"ASSIGNMENT:\n{assignment}\n\nDRAFT:\n{draft}"}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }, headers={"x-goog-api-key": key}, timeout=90)
    return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])


def evaluate(assignment: str, draft: str) -> tuple[dict, str]:
    try:
        return call_groq(assignment, draft), f"Groq {env('GROQ_MODEL', GROQ_MODEL)}"
    except (RuntimeError, KeyError, json.JSONDecodeError) as e:
        if not env("GEMINI_API_KEY"):
            raise DocGymError(f"Groq evaluation failed: {e}") from e
        return call_gemini(assignment, draft), f"Gemini {env('GEMINI_MODEL', 'gemini-2.5-flash')} (fallback)"


def normalize(result: dict) -> dict:
    scores = result.get("scores") or {}
    clean = {}
    for c in CRITERIA:
        try:
            clean[c] = max(1, min(10, int(round(float(scores.get(c, 0))))))
        except (TypeError, ValueError):
            raise DocGymError(f"Evaluator returned an invalid score for {c}: {scores.get(c)!r}")
    result["scores"] = clean
    return result


def render_evaluation(result: dict, total: int, grade: str, engine: str, date: str) -> str:
    s = result["scores"]
    lines = [
        "## Evaluation",
        f"> [!abstract] Grade **{grade}** · {total}/30",
        f"> {result.get('summary', '').strip()}",
        "",
        "| Criterion | Score |",
        "|---|---|",
        *[f"| {LABELS[c]} | {s[c]}/10 |" for c in CRITERIA],
        f"| **Total** | **{total}/30** |",
        "",
        "### What worked",
        *[f"- {h}" for h in result.get("highlights") or []],
        "",
        "### Rewrite suggestions",
    ]
    for i, r in enumerate(result.get("rewrites") or [], 1):
        original = str(r.get("original", "")).strip()
        # Suggestions are often multi-line Markdown. Demote their headings so they
        # can't be mistaken for this note's own ## sections on the next run.
        suggestion = re.sub(r"^#{1,4} ", "##### ", str(r.get("suggestion", "")).strip(), flags=re.M)
        lines += [
            "",
            f"#### {i}. {str(r.get('why', '')).strip()}",
            "> [!quote] Original",
            *[f"> {ln}" for ln in original.splitlines()],
            "",
            "**Rewrite:**",
            "",
            suggestion,
        ]
    lines += ["", f"*Evaluated {date} by {engine}.*", ""]
    return "\n".join(lines)


def grade_note(text: str, dry_run: bool = False) -> tuple[str, str, int]:
    """Grade a workout note's draft. Returns (updated note text, letter grade, total score)."""
    fm, body = split_frontmatter(text)

    draft_span = section(body, "Draft")
    if not draft_span:
        raise DocGymError("No '## Draft' section in this note.")
    # Drafts often contain their own ## headings, so the draft runs to ## Evaluation (or the end).
    eval_heading = re.search(r"^## Evaluation[ \t]*$", body[draft_span[0]:], re.MULTILINE)
    draft_end = draft_span[0] + eval_heading.start() if eval_heading else len(body)
    draft = PLACEHOLDER_RE.sub("", body[draft_span[0]:draft_end]).strip()
    if len(draft.split()) < 20:
        raise DocGymError("The draft is empty or under 20 words. Write it under '## Draft' first.")

    # Everything above the draft (scenario, constraints) is the assignment.
    assignment = body[:draft_span[0]].rsplit("## Draft", 1)[0].strip()

    if dry_run:
        result, engine = {
            "scores": {"technical_clarity": 7, "tone_balance": 8, "scannability": 6},
            "summary": "Dry run: canned evaluation.",
            "highlights": ["Dry run highlight."],
            "rewrites": [{"original": draft.split(".")[0], "suggestion": "Dry run rewrite.", "why": "Dry run."}],
        }, "dry run"
    else:
        result, engine = evaluate(assignment, draft)
    result = normalize(result)
    total = sum(result["scores"].values())
    grade = grade_for(total)
    today = dt.date.today().isoformat()

    # Replace any previous evaluation instead of stacking a second one.
    eval_span = section(body, "Evaluation")
    if eval_span:
        body = body[:eval_span[0]].rsplit("## Evaluation", 1)[0] + body[eval_span[1]:]
    body = body.rstrip() + "\n\n" + render_evaluation(result, total, grade, engine, today)
    fm = set_frontmatter(fm, {"status": "completed", "grade": grade, "score": f"{total}/30", "evaluated": today})
    return join_frontmatter(fm, body), grade, total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="note path, absolute or vault-relative")
    ap.add_argument("--dry-run", action="store_true",
                    help="use a canned evaluation, print the result, write nothing (no API call)")
    args = ap.parse_args()
    load_env()

    abs_path, rel = resolve_note(args.path)
    if not abs_path.exists():
        die(f"Note not found: {abs_path}")
    try:
        content, grade, total = grade_note(abs_path.read_text(encoding="utf-8"), args.dry_run)
    except DocGymError as e:
        die(str(e))

    if args.dry_run:
        print(content)
        return
    write_note(abs_path, rel, content)
    print(f"{grade} {total}/30")


if __name__ == "__main__":
    try:
        main()
    except DocGymError as e:
        die(str(e))
