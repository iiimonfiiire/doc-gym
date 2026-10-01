---
tags: [doc-gym]
---
# Doc Gym

A practice loop for technical writing. Your server writes the exercise and grades your draft. Works on desktop and mobile.

- **Dashboard:** [[Doc Gym/Dashboard|Dashboard]]
- **Code and setup:** the `doc-gym` project folder on your desktop (see its README).

## How it works

1. **New workout.** Pick an intensity and a focus. The server's local model writes a scenario with **raw inputs**: commit messages, engineer notes, or support tickets. It also picks a random **tone**. The note lands in `Prompts/YYYY-MM-DD-Title.md` with `status: pending`.
2. **Write** under `## Draft`, turning the raw inputs into the finished document. Delete the placeholder comment. Use any headings you like.
3. **Grade.** With the workout open, run the grader. The note gets `status: completed`, `grade`, and `score`, plus a `## Evaluation` section. Grading again replaces the old evaluation.

```
Obsidian (desktop or mobile) ──HTTP──▶ Doc Gym service on your server :8765
                                         ├─ /generate → Ollama (local on the server)
                                         └─ /evaluate → Groq
```

| Intensity | Length | Time |
|---|---|---|
| Sprint | 120–200 words | 15 min |
| Routine | 300–500 words | 30 min |
| Endurance | 800–1000 words | 60 min |

- **Focus:** SaaS Docs, API Specs, Troubleshooting, Release Notes.
- **Tone** (random each time, shown in the header): Neutral, Friendly, Playful. The grader scores Tone Balance against it.
- **Grades:** A ≥ 27, B ≥ 24, C ≥ 21, D ≥ 18, F below. Three criteria, 1–10 each: Technical Clarity, Tone Balance, Scannability & Formatting.

## Desktop: hotkeys

Settings → Hotkeys → search `Doc Gym`:

| Action | Command | Suggested key |
|---|---|---|
| New workout | `Templater: Create Doc Gym/Templates/new-workout.md` | ⌘⌥G |
| Grade | `Templater: Insert Doc Gym/Templates/evaluation-template.md` | ⌘⌥E |

- **New workout uses Create.** It turns a new note into the workout. Works with no note open. Cancelling leaves nothing behind.
- **Grade uses Insert.** It acts on the note you have open and inserts nothing. If it's bound to Create by mistake, it says so and cleans up.

## Mobile

Same two commands, no keyboard:

- **Toolbar (best):** Settings → Toolbar → add the two commands above. They appear above the keyboard while you edit.
- **Command palette:** swipe down in a note, type `Doc Gym`.

Away from home, connect your VPN first if the server is only reachable through it.

## Settings

The templates read `Doc Gym/.doc-gym.json` (hidden, synced with the vault). It holds the service addresses and the service token. `install.sh` writes it. The Groq key is never stored in the vault.

## Troubleshooting

- **"Can't reach the Doc Gym service"**
  - Away from home: connect your VPN.
  - Check the service: `curl http://<server>:8765/health` should print `{"ok": true}`.
  - Restart it on the server: `systemctl --user restart doc-gym`. Logs: `journalctl --user -u doc-gym -n 50`.
- **"Missing or wrong Doc Gym token".** Rerun `./install.sh`, then `./ops/deploy-server.sh`. The token in `.env` on both machines must match `.doc-gym.json`.
- **"The model returned too little to work with".** Small models sometimes produce thin output. Run it again.
- **A workout took minutes instead of seconds.** The server keeps one ready workout per focus and makes a new one in the background after each use (about 2–3 minutes). Two workouts of the same focus in a row means the second one is generated live.
- **"The draft is empty or under 20 words".** Write under `## Draft` and delete the placeholder comment.
- **"The note changed while grading".** You typed during grading. Run it again.
