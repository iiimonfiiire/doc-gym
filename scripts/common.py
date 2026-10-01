"""Shared helpers for Doc Gym: config, HTTP, note I/O. Stdlib only, Python 3.9+."""
from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
GYM_FOLDER = "Doc Gym"


def load_env() -> None:
    """Load PROJECT_DIR/.env (or $DOC_GYM_ENV) into os.environ without overriding real env vars."""
    env_file = Path(os.environ.get("DOC_GYM_ENV") or PROJECT_DIR / ".env").expanduser()
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class DocGymError(Exception):
    """A user-facing failure. The CLI prints it; the service returns it as JSON."""


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name) or default
    if required and not value:
        raise DocGymError(f"{name} is not set. Add it to {PROJECT_DIR / '.env'}")
    return value


def vault_dir() -> Path:
    return Path(env("DOC_GYM_VAULT", required=True)).expanduser()


def ollama_url() -> str:
    # PHOENIX_OLLAMA_URL is the pre-anonymization name, still honored for existing .env files.
    return (env("OLLAMA_URL") or env("PHOENIX_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


def die(msg: str, code: int = 1) -> None:
    print(f"doc-gym: {msg}", file=sys.stderr)
    sys.exit(code)


def http_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 180,
              context: ssl.SSLContext | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "doc-gym/1.0", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach {url}: {e.reason}") from e
    except (http.client.HTTPException, OSError) as e:
        raise RuntimeError(f"Connection to {url} failed: {e}") from e


def resolve_note(path_arg: str) -> tuple[Path, str]:
    """Accept an absolute path or a vault-relative one. Return (absolute, vault-relative)."""
    vault = vault_dir()
    p = Path(path_arg).expanduser()
    abs_path = p if p.is_absolute() else vault / p
    try:
        rel = abs_path.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        rel = ""
    return abs_path, rel


def write_note(abs_path: Path, rel_path: str, content: str) -> str:
    """Write through Obsidian's Local REST API when available, so an open editor
    picks up the change cleanly. Fall back to the filesystem. Returns the method used."""
    token = env("OBSIDIAN_REST_TOKEN")
    if token and rel_path:
        base = env("OBSIDIAN_REST_URL", "https://127.0.0.1:27124").rstrip("/")
        url = f"{base}/vault/{urllib.parse.quote(rel_path)}"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # localhost, self-signed cert
        req = urllib.request.Request(
            url, data=content.encode(), method="PUT",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "text/markdown"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5, context=ctx):
                return "rest-api"
        except (urllib.error.URLError, OSError):
            pass  # Obsidian closed or plugin off
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    return "filesystem"


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def split_frontmatter(text: str) -> tuple[list[str], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return [], text
    return m.group(1).splitlines(), text[m.end():]


def set_frontmatter(lines: list[str], updates: dict[str, str]) -> list[str]:
    """Update or append flat `key: value` lines. Leaves other keys untouched."""
    out, seen = [], set()
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if key in updates and not line.startswith((" ", "\t", "-")):
            out.append(f"{key}: {updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}: {v}" for k, v in updates.items() if k not in seen]
    return out


def join_frontmatter(lines: list[str], body: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\n" + body
