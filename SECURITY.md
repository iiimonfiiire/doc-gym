# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab of this repository, then **Report a vulnerability**. Please don't open a public issue for security problems.

## Threat model

Doc Gym is a single-user, self-hosted tool. One person runs the service on their own server and calls it from their own Obsidian clients.

**In scope**

- Anyone on the networks that can reach port 8765 (LAN or VPN) who doesn't hold the token.
- Malformed or oversized requests sent to the service.
- Secrets leaking through the repo, logs, or error responses.

**Out of scope**

- Someone who already holds the service token. They are the user.
- Someone with access to the vault. The vault holds the token in `Doc Gym/.doc-gym.json` by design, so the mobile apps can call the service.
- Content returned by the models. Workouts and grades are text written into your own notes.

## Defenses

- **Token auth.** Every POST needs `Authorization: Bearer <DOC_GYM_TOKEN>`. Comparison is constant-time. The service refuses to start without a token. `GET /health` is open and returns only which workout focuses are ready.
- **Network exposure.** The service binds to `127.0.0.1` by default. The systemd unit opts into `0.0.0.0` for remote clients, and `ops/server-service-setup.sh` limits port 8765 with ufw to the networks you name. Use a VPN such as Tailscale away from home. The service speaks plain HTTP, so don't expose it to the internet.
- **Ollama** has no auth. Keep it on localhost (the default in `ops/server-ollama-setup.sh`).
- **Request limits.** 1 MB body limit, JSON object bodies only, and a 60 s socket timeout.
- **Error responses** carry user-facing messages only. Stack traces go to the service log.
- **Secrets** (Groq and Gemini keys, the service token) live only in `.env`, which is gitignored and `chmod 600`. The Groq and Gemini keys never reach the vault.
- **Least privilege.** The service runs as a systemd user service, not as root.

## Rotating the token

Delete `DOC_GYM_TOKEN` from `.env`, rerun `./install.sh` (it generates a new one and rewrites `.doc-gym.json`), then delete `.env` on the server and rerun `./ops/deploy-server.sh` so the new file is copied over.
