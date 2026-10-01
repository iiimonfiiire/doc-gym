# doc-gym

A technical-writing practice loop inside Obsidian. A small self-hosted service generates writing workouts with a local Ollama model and grades drafts with Groq. Obsidian templates call it, so it works on desktop and mobile.

The in-vault user guide is `vault-scaffold/README.md`.

```
Obsidian (desktop/mobile) ─ Templater JS ─ requestUrl ─▶ scripts/service.py on your server :8765
  POST /generate → generate_prompt.generate_note → Ollama (localhost on the server)
  POST /evaluate → evaluate_draft.grade_note     → Groq (Gemini fallback)
```

The service never touches the vault. Obsidian sends note text and writes the result back.

Generation takes minutes on a small CPU server, so the service keeps **one pre-generated workout per focus** in `cache/buffer.json`. A request takes one, applies the chosen intensity, a random tone, and today's date, and returns at once. A background thread refills it. `GET /health` lists which focuses are ready. Set `DOC_GYM_BUFFER=0` to disable. Bump `PROMPT_VERSION` in `generate_prompt.py` whenever the prompt changes, so stale buffered workouts are discarded.

## Requirements

- **Server:** Linux with systemd, Python 3.9+, [Ollama](https://ollama.com), and ufw. Tested on Ubuntu 24.04 with 4 GB RAM.
- **Desktop:** macOS or Linux with Obsidian and the Templater and Dataview plugins.
- **Accounts:** a Groq API key. Optionally a Gemini key as a fallback.
- **Mobile access away from home:** a VPN to the server, such as Tailscale.

## Setup

1. **Configure.** `cp .env.example .env` and fill in the required values. All deployment-specific settings live in `.env`, never in code.
2. **Ollama on the server** (one time): `sudo bash ops/server-ollama-setup.sh`, then `ollama pull llama3.2:3b`. With no arguments, Ollama listens on localhost only, which is all the service needs.
3. **Service on the server** (one time, root): `sudo bash ops/server-service-setup.sh <cidr> [...]`. List only the networks that should reach port 8765, e.g. your VPN range.
4. **Desktop** (Obsidian closed): `./install.sh`. It creates the vault folders, the service token, and `Doc Gym/.doc-gym.json`, and registers the Templater hotkey targets.
5. **Deploy:** `./ops/deploy-server.sh`. It copies the code and `.env` to the server and starts the systemd user service. Rerun it after code changes. No sudo needed.

## Layout

| Path | What |
|---|---|
| `scripts/service.py` | HTTP service (stdlib). Bearer-token auth on POST, 1 MB body limit. |
| `scripts/generate_prompt.py`, `evaluate_draft.py` | Core logic, also usable as CLIs against the vault |
| `scripts/common.py` | Env loading, HTTP, frontmatter, note I/O |
| `scripts/configure_templater.py`, `write_client_config.py` | Used by `install.sh` |
| `vault-scaffold/` | Templates, Dashboard, and guide copied into `<vault>/Doc Gym/` |
| `ops/` | Server setup, systemd user unit, deploy script, firewall helper, NVIDIA driver cleanup |

## Develop

```bash
DOC_GYM_BIND=127.0.0.1:18765 python3 scripts/service.py          # service locally
python3 scripts/generate_prompt.py --intensity Sprint --focus "API Specs" --dry-run
python3 scripts/evaluate_draft.py "Doc Gym/Prompts/<note>.md" --dry-run
```

- Stdlib only.
- `DOC_GYM_ENV` points the scripts at a different `.env`.
- After changing `vault-scaffold/`, run `./install.sh --force`. That overwrites the vault copies.

## Security notes

- The Groq key lives only in `.env` (desktop and server, `chmod 600`, gitignored).
- The vault holds only the service token, in `.doc-gym.json`. It syncs wherever your vault syncs. Rotate it by deleting `DOC_GYM_TOKEN` from `.env`, rerunning `install.sh`, and redeploying.
- The service speaks plain HTTP. Reach it over a VPN (WireGuard/Tailscale encrypts the traffic), or keep it on a trusted LAN. Restrict port 8765 with ufw to those networks only.
- Ollama has no auth. Keep it on localhost unless desktop CLIs need it directly.
- To change who can reach a port later: `sudo bash ops/server-firewall.sh <port> [<cidr> ...]`. It replaces every ufw rule for that port, and with no networks the port is closed.

## Known issue: Ollama installer and old NVIDIA GPUs

Ollama's `install.sh` installs the NVIDIA CUDA driver whenever it sees an NVIDIA GPU. On GPUs too old for that driver, udev retries loading it endlessly, and the CPU overheats and throttles. `ops/server-remove-nvidia.sh` removes the driver and pins it out. Later Ollama upgrades through `install.sh` then error at the CUDA step, which is harmless.
