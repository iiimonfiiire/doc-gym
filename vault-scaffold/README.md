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

Templater lists two commands per template, Create and Insert. You only need two of the four:

| Action | How | Command it runs |
|---|---|---|
| New workout | Tap **🏋️ New workout** on the [[Doc Gym/Dashboard\|Dashboard]] | `Create …/new-workout` |
| Grade | Open the workout, tap into the text, then the toolbar button | `Insert …/evaluation-template` |

- **Set up the grade button once:** Settings → Toolbar → add `Templater: Insert Doc Gym/Templates/evaluation-template.md`. The toolbar appears above the keyboard while you edit, which is when you finish a draft.
- **Ignore** `Insert …/new-workout` and `Create …/evaluation-template`.
- The focus picker shows which workouts are **ready** (instant) and which are **preparing** (about 3 minutes). Picking one that's still preparing gives you a message right away instead of a long wait, and moves it to the front of the queue.
- Away from home, connect your VPN first if the server is only reachable through it.

## Settings

The templates read `Doc Gym/.doc-gym.json` (hidden, synced with the vault). It holds the service addresses and the service token. `install.sh` writes it. The Groq key is never stored in the vault.

## Troubleshooting

- **"Can't reach the Doc Gym service"**
  - Away from home: connect your VPN.
  - Check the service: `curl http://<server>:8765/health` should print `{"ok": true}`.
  - Restart it on the server: `systemctl --user restart doc-gym`. Logs: `journalctl --user -u doc-gym -n 50`.
- **"Missing or wrong Doc Gym token".** Rerun `./install.sh`, then `./ops/deploy-server.sh`. The token in `.env` on both machines must match `.doc-gym.json`.
- **"The model returned too little to work with".** Small models sometimes produce thin output. Run it again.
- **"No … workout is ready yet".** The server keeps one ready workout per focus and makes a new one in the background after each use (about 3 minutes each). Pick a focus marked ready, or try again in a few minutes. When the buffer starts empty (first install, or after a prompt update), all four take about 12 minutes to fill.
- **"The draft is empty or under 20 words".** Write under `## Draft` and delete the placeholder comment.
- **"The note changed while grading".** You typed during grading. Run it again.
