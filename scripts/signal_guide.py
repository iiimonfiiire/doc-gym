#!/usr/bin/env python3
"""Signal through PAKT: load the pinned style guide, describe a content type's rules, and run PAKT's checks.

Doc Gym reads Signal only through PAKT's deterministic modules, which import nothing outside the stdlib.
PAKT comes from vendor/ (ops/vendor-pakt.sh) or an installed package. PAKT fetches the guide once and caches it.
Run this file to fetch and cache the guide ahead of time.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from common import PROJECT_DIR, DocGymError, die, env, load_env

MIN_PYTHON = (3, 11)
SEVERITY_ORDER = {"error": 0, "warning": 1, "suggestion": 2}
# Style rules every workout spells out with their per-type values. The rest are listed by ID.
VOICE_RULES = ("second_person", "first_person", "no_humor")
KEY_STYLE_RULES = ("sentence_length", "contractions", "semicolons")
_lock = threading.Lock()
_loaded: dict[str, "Signal"] = {}


def vendor_dir() -> Path:
    return Path(env("DOC_GYM_VENDOR") or PROJECT_DIR / "vendor").expanduser()


def load_pakt():
    """Import PAKT, from vendor/ first. Raises DocGymError with a fix when it can't."""
    if sys.version_info < MIN_PYTHON:
        raise DocGymError("Signal grading needs Python 3.11 or later, because PAKT reads TOML with tomllib.")
    vendor = vendor_dir()
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    try:
        import pakt
        from pakt import document, review, sources, structure, styleguide  # noqa: F401
    except ImportError as e:
        raise DocGymError(f"PAKT is not available ({e}). Run ops/vendor-pakt.sh, or install PAKT.") from e
    return pakt


def pakt_root() -> Path:
    """The folder that holds PAKT's content-types.toml: PAKT_ROOT, else the package's parent folder."""
    from pakt.config import package_root
    return Path(os.environ.get("PAKT_ROOT") or package_root())


def guide_source() -> str:
    """A PAKT guide source. The default, signal, is the Signal release that the pinned PAKT ref pins."""
    return env("DOC_GYM_GUIDE", "signal")


@dataclass(frozen=True)
class Signal:
    guide: object
    types: dict

    @property
    def label(self) -> str:
        return self.guide.label

    @property
    def source(self) -> str:
        return self.guide.source


def load() -> Signal:
    """Load the active guide and PAKT's content types, once per process."""
    source = guide_source()
    with _lock:
        if source in _loaded:
            return _loaded[source]
        load_pakt()
        from pakt.config import ConfigError
        from pakt.structure import load_content_types
        from pakt.styleguide import get_guide
        try:
            root = pakt_root()
            guide = get_guide(source, root, PROJECT_DIR, origin="doc-gym")
            types = load_content_types(guide, root)
        except (ConfigError, OSError, ValueError) as e:
            raise DocGymError(f"Cannot load the style guide {source!r}: {e}") from e
        _loaded[source] = Signal(guide, types)
        return _loaded[source]


@dataclass
class TypeRules:
    """What a content type must follow, with every by_type override applied."""
    type_id: str
    type_name: str
    guide_label: str
    guide_source: str
    rules: list = field(default_factory=list)
    requirements: list = field(default_factory=list)
    voice: list[tuple[str, str]] = field(default_factory=list)
    style: list[tuple[str, str]] = field(default_factory=list)
    also_checked: list[str] = field(default_factory=list)
    ui_kinds: list[str] = field(default_factory=list)


def content_type(sig: Signal, type_id: str):
    ctype = sig.types.get(type_id)
    if ctype is None:
        raise DocGymError(f"PAKT has no content type {type_id!r}.")
    return ctype


def type_rules(type_id: str) -> TypeRules:
    sig = load()
    ctype = content_type(sig, type_id)
    effective = {r.id: r for r in sig.guide.rules_for(ctype)}
    out = TypeRules(type_id, ctype.name, sig.label, sig.source, list(effective.values()), list(ctype.requirements))

    if "second_person" in effective:
        out.voice.append(("second_person", effective["second_person"].summary))
    if "first_person" in effective:
        out.voice.append(("first_person", effective["first_person"].summary))
    elif sig.guide.rule("first_person"):
        out.voice.append(("first_person", 'First person is allowed for this content type: write "we" for the company.'))
    if "no_humor" in effective:
        out.voice.append(("no_humor", effective["no_humor"].summary))

    rule = effective.get("sentence_length")
    if rule and rule.params.get("max_words"):
        out.style.append((rule.id, f"Keep every sentence to {rule.params['max_words']} words or fewer."))
    if "contractions" in effective:
        out.style.append(("contractions", effective["contractions"].summary))
    elif sig.guide.rule("contractions"):
        out.style.append(("contractions", "Contractions are allowed for this content type."))
    rule = effective.get("semicolons")
    if rule:
        where = "in a numbered step" if rule.params.get("scope") == "steps" else "anywhere"
        out.style.append((rule.id, f"Never use a semicolon {where}."))

    listed = set(VOICE_RULES) | set(KEY_STYLE_RULES)
    out.also_checked = [r.id for r in effective.values() if r.check and r.id not in listed]
    if type_id == "ui_microcopy":
        from pakt.document import UI_KINDS
        out.ui_kinds = list(UI_KINDS)
    return out


def is_checked(tr: TypeRules, rule_id: str) -> bool:
    return any(r.id == rule_id and r.check for r in tr.rules + tr.requirements)


def digest(tr: TypeRules) -> str:
    """The rules and requirements for the grader: IDs, severities, and summaries only."""
    def line(r) -> str:
        return f"- {r.id} [{r.severity}, {'check' if r.check else 'judgment'}]: {r.summary}"
    return "\n".join([
        f"Style guide: {tr.guide_label} ({tr.guide_source}). Content type: {tr.type_name} ({tr.type_id}).",
        "Rules:", *[line(r) for r in tr.rules],
        "Structural requirements:", *([line(r) for r in tr.requirements] or ["- none"]),
    ])


def check_draft(draft: str, type_id: str) -> list[dict]:
    """PAKT's deterministic findings for a draft, most severe first. Each has rule, severity, message, quote, line, fix."""
    sig = load()
    ctype = content_type(sig, type_id)
    from pakt.document import parse_document
    from pakt.review import deterministic_findings
    findings, _ = deterministic_findings(parse_document(draft), sig.guide, ctype)
    out = [f.to_dict() for f in findings]
    return sorted(out, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 3), f["line"] or 0))


def detect_type(draft: str) -> str:
    """PAKT's content detection, for notes that name no type."""
    sig = load()
    from pakt.document import parse_document
    from pakt.structure import detect_type as pakt_detect
    return pakt_detect(parse_document(draft), sig.types)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--type", help="also print the rules digest for a PAKT content type, such as release_note")
    args = ap.parse_args()
    load_env()
    try:
        sig = load()
        print(f"{sig.label} from {sig.source}, {len(sig.guide.rules)} rules, {len(sig.types)} content types.")
        if args.type:
            print(digest(type_rules(args.type)))
    except DocGymError as e:
        die(str(e))


if __name__ == "__main__":
    main()
