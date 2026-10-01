#!/usr/bin/env python3
"""Make sure .env has a DOC_GYM_TOKEN, then write the vault's Doc Gym/.doc-gym.json.

That file is what the Templater templates read (on desktop and mobile) to find and call the
Doc Gym service. It holds the service token, never the Groq key.
"""
import json
import os
import secrets
import sys
from pathlib import Path

vault, project = Path(sys.argv[1]), Path(sys.argv[2])
env_file = project / ".env"
text = env_file.read_text() if env_file.exists() else ""
values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#"))

token = values.get("DOC_GYM_TOKEN", "").strip()
if not token:
    token = secrets.token_urlsafe(24)
    env_file.write_text(text.rstrip("\n") + f"\nDOC_GYM_TOKEN={token}\n")
    os.chmod(env_file, 0o600)
    print("   added DOC_GYM_TOKEN to .env")

# Comma-separated, tried in order. Put the address that works everywhere (e.g. a VPN one) first.
urls = values.get("DOC_GYM_SERVICE_URLS", "").strip()
if not urls:
    sys.exit("DOC_GYM_SERVICE_URLS is not set in .env, e.g. DOC_GYM_SERVICE_URLS=http://my-server:8765")
config = {"urls": [u.strip().rstrip("/") for u in urls.split(",") if u.strip()], "token": token}
dest = vault / "Doc Gym" / ".doc-gym.json"
dest.write_text(json.dumps(config, indent=2) + "\n")
print(f"   wrote {dest.relative_to(vault)} ({len(config['urls'])} service URLs)")
