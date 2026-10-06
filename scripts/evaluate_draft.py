#!/usr/bin/env python3
"""Grade the ## Draft section of a Doc Gym note against Signal and write the evaluation back.

PAKT runs Signal's deterministic checks on the draft. Groq (Gemini fallback) scores four criteria,
with those findings as evidence, and adds judgment findings and rewrites.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re

import signal_guide
from common import (DocGymError, die, env, http_json, join_frontmatter, load_env, resolve_note,
                    set_frontmatter, split_frontmatter, write_note)
from generate_prompt import FOCUS_ALIASES, FOCUS_TYPE, INTENSITY, signal_type

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile; gpt-oss-120b is its strongest general model as of 2026-10.
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CRITERIA = ["technical_clarity", "voice", "scannability", "signal_compliance"]
LABELS = {
    "technical_clarity": "Technical clarity",
    "voice": "Voice and register",
    "scannability": "Scannability and formatting",
    "signal_compliance": "Signal compliance",
}
MAX_SCORE = 10 * len(CRITERIA)
GRADE_FLOORS = (("A", 36), ("B", 32), ("C", 28), ("D", 24))
MAX_FINDINGS = 25

RUBRIC = """You are a strict senior documentation editor grading a practice draft against the Signal style guide.
Score each criterion from 1 to 10. Be demanding: 7 is solid professional work, 9-10 is rare.

technical_clarity: Is every step, term and behavior accurate, specific, unambiguous, and true to the raw inputs?
  1-3 vague or wrong; 4-6 understandable but hand-wavy or missing key details; 7-8 precise and complete; 9-10 nothing to fix.
voice: Voice and register as Signal sets them for this content type: the person rules (you, we, I) listed under SIGNAL,
  imperative verbs for instructions, directness, no humor, a professional register.
  1-3 wrong person or register; 4-6 inconsistent; 7-8 fitting and consistent; 9-10 effortless.
scannability: Signal formatting: a clear heading hierarchy, numbered lists only for ordered steps and bullets otherwise,
  short sentences, key information first, tables only for tabular data, correct Markdown.
  1-3 wall of text; 4-6 some structure, hard to skim; 7-8 easy to skim; 9-10 a reader finds any answer in seconds.
signal_compliance: How fully the draft follows the Signal rules and structural requirements listed under SIGNAL.
  The PAKT findings come from deterministic checks and are verified: count each one as a real violation, errors more
  than warnings, warnings more than suggestions. Then judge the rules marked judgment yourself.
  1-3 ignores the guide; 4-6 many violations; 7-8 a few minor ones; 9-10 only with no error findings at all.

Also check the draft against the assignment's constraints and length target; missed constraints must lower scores.
If SIGNAL says the assignment is a legacy one, it predates Signal grading: score voice against the tone the assignment
asks for, and score signal_compliance against Signal anyway.

Reply with JSON only:
{"scores": {"technical_clarity": int, "voice": int, "scannability": int, "signal_compliance": int},
 "summary": "one sentence verdict",
 "highlights": ["2-3 specific things done well, quoting the draft"],
 "findings": [{"rule": "a judgment rule ID from SIGNAL", "quote": "exact quote from the draft", "fix": "how to fix it"}],
 "rewrites": [{"original": "exact quote from the draft", "suggestion": "rewritten version", "why": "short reason"}]}
Give 0-5 findings, for judgment rules only: the PAKT findings already cover the checked rules.
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
    for letter, floor in GRADE_FLOORS:
        if total >= floor:
            return letter
    return "F"


def user_message(assignment: str, draft: str, signal: str) -> str:
    return f"ASSIGNMENT:\n{assignment}\n\nSIGNAL:\n{signal}\n\nDRAFT:\n{draft}"


def call_groq(assignment: str, draft: str, signal: str = "") -> dict:
    key = env("GROQ_API_KEY", required=True)
    resp = http_json(GROQ_URL, {
        "model": env("GROQ_MODEL", GROQ_MODEL),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": user_message(assignment, draft, signal)},
        ],
    }, headers={"Authorization": f"Bearer {key}"}, timeout=90)
    return json.loads(resp["choices"][0]["message"]["content"])


def call_gemini(assignment: str, draft: str, signal: str = "") -> dict:
    key = env("GEMINI_API_KEY", required=True)
    model = env("GEMINI_MODEL", "gemini-2.5-flash")
    resp = http_json(GEMINI_URL.format(model=model), {
        "systemInstruction": {"parts": [{"text": RUBRIC}]},
        "contents": [{"parts": [{"text": user_message(assignment, draft, signal)}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }, headers={"x-goog-api-key": key}, timeout=90)
    return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])


def evaluate(assignment: str, draft: str, signal: str = "") -> tuple[dict, str]:
    # Network failures, or a response that isn't the JSON shape we asked for.
    failures = (RuntimeError, KeyError, IndexError, TypeError, json.JSONDecodeError)
    try:
        return call_groq(assignment, draft, signal), f"Groq {env('GROQ_MODEL', GROQ_MODEL)}"
    except failures as e:
        if not env("GEMINI_API_KEY"):
            raise DocGymError(f"Groq evaluation failed: {e}") from e
        try:
            return call_gemini(assignment, draft, signal), f"Gemini {env('GEMINI_MODEL', 'gemini-2.5-flash')} (fallback)"
        except failures as e2:
            raise DocGymError(f"Groq evaluation failed: {e}. Gemini fallback failed: {e2}") from e2


def normalize(result: dict) -> dict:
    scores = result.get("scores") or {}
    if "voice" not in scores and "tone_balance" in scores:  # a model answering the old rubric
        scores["voice"] = scores["tone_balance"]
    clean = {}
    for c in CRITERIA:
        try:
            clean[c] = max(1, min(10, int(round(float(scores.get(c, 0))))))
        except (TypeError, ValueError):
            raise DocGymError(f"Evaluator returned an invalid score for {c}: {scores.get(c)!r}") from None
    result["scores"] = clean
    return result


def judgment_findings(result: dict, rules: dict, draft: str) -> list[dict]:
    """Keep the model's findings that name a judgment rule and quote the draft exactly. Drop the rest."""
    text = signal_guide.normalize_space(draft)
    out = []
    for f in result.get("findings") or []:
        if not isinstance(f, dict):
            continue
        rule, quote = str(f.get("rule", "")).strip().strip("`"), str(f.get("quote", "")).strip()
        if rule in rules and quote and signal_guide.normalize_space(quote) in text:
            out.append({"rule": rule, "severity": rules[rule].severity, "quote": quote, "fix": str(f.get("fix", "")).strip()})
    return out[:5]


def note_meta(fm: list[str]) -> dict:
    meta = {}
    for line in fm:
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def note_type(meta: dict, draft: str) -> str:
    """The content type to grade against: the note's signal_type, then its focus, then PAKT's detection."""
    types = signal_guide.load().types
    if meta.get("signal_type") in types:
        return meta["signal_type"]
    focus = FOCUS_ALIASES.get(meta.get("focus", ""), meta.get("focus", ""))
    if focus in FOCUS_TYPE:
        return signal_type(focus, meta["intensity"]) if meta.get("intensity") in INTENSITY else FOCUS_TYPE[focus]
    return signal_guide.detect_type(draft)


def signal_context(tr, findings: list[dict], legacy: bool) -> str:
    lines = [signal_guide.digest(tr), "", f"PAKT findings ({len(findings)}, deterministic and verified):"]
    lines += [f"- {f['rule']} [{f['severity']}]" + (f" line {f['line']}" if f["line"] else "") + f": {f['message']}"
              for f in findings] or ["- none"]
    if legacy:
        lines += ["", "This is a legacy assignment: it predates Signal grading, and its tone or structure rules may conflict "
                      "with Signal."]
    return "\n".join(lines)


def _code(text: str) -> str:
    return "`" + " ".join(str(text).split()).replace("`", "'") + "`"


def render_findings(tr, checks: list[dict], judged: list[dict], legacy: bool) -> list[str]:
    lines = ["### Signal findings",
             f"Checked against {tr.guide_label} as {tr.type_name} (`{tr.type_id}`). "
             "Line numbers count from the start of the draft."]
    if legacy:
        lines += ["", "> [!note] This workout predates Signal grading. Its tone and structure requirements can conflict "
                      "with Signal. Voice is scored against the workout's tone. The findings show what Signal expects."]
    lines += ["", "**Automatic checks (PAKT)**", ""]
    if not checks:
        lines.append("- None. Every automatic check passed.")
    for f in checks[:MAX_FINDINGS]:
        where = f", line {f['line']}" if f["line"] else ""
        lines.append(f"- **`{f['rule']}`** ({f['severity']}{where}) – {' '.join(f['message'].split())}")
        if f["quote"] and f["line"]:
            lines.append(f"  - Quote: {_code(f['quote'])}")
        if f["fix"]:
            lines.append(f"  - Fix: {f['fix']}")
    if len(checks) > MAX_FINDINGS:
        lines.append(f"- {len(checks) - MAX_FINDINGS} more findings not shown. Fix these first, then grade again.")
    lines += ["", "**Editor's judgment**", ""]
    if not judged:
        lines.append("- No further findings.")
    for f in judged:
        lines.append(f"- **`{f['rule']}`** ({f['severity']}) – {_code(f['quote'])}")
        if f["fix"]:
            lines.append(f"  - Fix: {f['fix']}")
    return lines


def render_evaluation(result: dict, total: int, grade: str, engine: str, date: str, findings: list[str]) -> str:
    s = result["scores"]
    lines = [
        "## Evaluation",
        f"> [!abstract] Grade **{grade}** · {total}/{MAX_SCORE}",
        f"> {result.get('summary', '').strip()}",
        "",
        "| Criterion | Score |",
        "|---|---|",
        *[f"| {LABELS[c]} | {s[c]}/10 |" for c in CRITERIA],
        f"| **Total** | **{total}/{MAX_SCORE}** |",
        "",
        *findings,
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

    meta = note_meta(fm)
    legacy = "tone" in meta and "signal_type" not in meta
    tr = signal_guide.type_rules(note_type(meta, draft))
    checks = signal_guide.check_draft(draft, tr.type_id)
    if dry_run:
        result, engine = {
            "scores": {"technical_clarity": 7, "voice": 8, "scannability": 6, "signal_compliance": 7},
            "summary": "Dry run: canned evaluation.",
            "highlights": ["Dry run highlight."],
            "rewrites": [{"original": draft.split(".")[0], "suggestion": "Dry run rewrite.", "why": "Dry run."}],
        }, "dry run"
    else:
        result, engine = evaluate(assignment, draft, signal_context(tr, checks, legacy))
    result = normalize(result)
    judged = judgment_findings(result, {r.id: r for r in tr.rules + tr.requirements if not r.check}, draft)
    total = sum(result["scores"].values())
    grade = grade_for(total)
    today = dt.date.today().isoformat()

    # Replace any previous evaluation instead of stacking a second one.
    eval_span = section(body, "Evaluation")
    if eval_span:
        body = body[:eval_span[0]].rsplit("## Evaluation", 1)[0] + body[eval_span[1]:]
    findings = render_findings(tr, checks, judged, legacy)
    body = body.rstrip() + "\n\n" + render_evaluation(result, total, grade, engine, today, findings)
    fm = set_frontmatter(fm, {"status": "completed", "grade": grade, "score": f"{total}/{MAX_SCORE}", "evaluated": today})
    return join_frontmatter(fm, body), grade, total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="note path, absolute or vault-relative")
    ap.add_argument("--dry-run", action="store_true",
                    help="use a canned evaluation, print the result, write nothing (no model call)")
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
    print(f"{grade} {total}/{MAX_SCORE}")


if __name__ == "__main__":
    try:
        main()
    except DocGymError as e:
        die(str(e))
