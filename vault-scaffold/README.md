---
tags: [doc-gym]
---
# Doc Gym

Doc Gym is a practice loop for technical writing in Obsidian. Your server writes each exercise and grades your draft against the Signal style guide. It works on desktop and mobile.

- **Dashboard** – [[Doc Gym/Dashboard|Dashboard]]
- **Code and setup** – the `doc-gym` project folder on your desktop, described in its README

## How it works

1. Run **New workout**, then pick an intensity and a focus. The server's local model writes a scenario with raw inputs, such as commit messages, engineer notes, or support tickets. The note lands in `Prompts/YYYY-MM-DD-Title.md` with `status: pending`.
2. Read the **Signal requirements** section of the workout. It lists the voice, structure, and style rules for this type of document.
3. Write the finished document under `## Draft`, and delete the placeholder comment.
4. Run **Grade** with the workout open. The note gets `status: completed`, `grade`, `score`, and a `## Evaluation` section. Grading again replaces the old evaluation.

```
Obsidian (desktop or mobile) ──HTTP──▶ Doc Gym service on your server :8765
                                         ├─ /generate → Ollama (local on the server)
                                         └─ /evaluate → PAKT checks, then Groq
```

## Focuses

Each focus practices one Signal content type. The workout shows the type, and the grader checks the draft against it.

| Focus | Signal content type |
|---|---|
| SaaS Docs | KB task-based guide |
| API Specs | API documentation |
| Troubleshooting | Troubleshooting guide |
| Release Notes | Release notes |
| UX Microcopy | UX microcopy |
| GTM Brief | GTM and product handoff brief |
| Product Walkthrough | Product walkthrough |
| Tutorial | Tutorial, or a quickstart on Sprint |

## Intensity

| Intensity | Length | Time |
|---|---|---|
| Sprint | 120–200 words | 15 min |
| Routine | 300–500 words | 30 min |
| Endurance | 800–1000 words | 60 min |

UX Microcopy counts strings instead of words, from 6 strings on Sprint to 20 on Endurance. GTM Brief counts bullets in its five sections.

## Voice

Signal sets the voice for each content type. The workout states it under **Voice**.

- **Instructional types** – Address the reader as `you` and use imperative verbs. Do not write `we` or `I`.
- **Release notes and GTM briefs** – You can write `we` for the company. Describe the new capability, not what the team built.
- **Every type** – No jokes, no asides, and no slang.

## Grades

The grader scores four criteria from 1 to 10, for a total out of 40.

| Criterion | What it measures |
|---|---|
| Technical clarity | Accurate, specific steps and facts, true to the raw inputs |
| Voice and register | The Signal voice for the content type |
| Scannability and formatting | Signal formatting: headings, lists, short sentences, and key information first |
| Signal compliance | The Signal rules and structural requirements for the content type |

- **Letter grades** – A from 36, B from 32, C from 28, and D from 24. Anything lower is an F.
- **Signal findings** – The evaluation lists automatic check results first. Each one names the rule ID, quotes the draft, and gives the fix. The grader's own findings and rewrites follow.
- **Older workouts** – Workouts from before Signal grading still grade. The grader scores voice against their old tone, and the findings show what Signal expects.

## Desktop hotkeys

Open **Settings** > **Hotkeys**, and search for `Doc Gym`.

| Action | Command | Suggested key |
|---|---|---|
| New workout | `Templater: Create Doc Gym/Templates/new-workout.md` | ⌘⌥G |
| Grade | `Templater: Insert Doc Gym/Templates/evaluation-template.md` | ⌘⌥E |

- **New workout uses Create** – It turns a new note into the workout, so it works with no note open. Cancelling leaves nothing behind.
- **Grade uses Insert** – It acts on the open note and inserts nothing. If you bind it to Create by mistake, it tells you and cleans up.

## Mobile

Templater lists a Create command and an Insert command for each template. You need two of the four.

| Action | How | Command it runs |
|---|---|---|
| New workout | Tap **🏋️ New workout** on the [[Doc Gym/Dashboard\|Dashboard]] | `Create …/new-workout` |
| Grade | Open the workout, tap into the text, and then tap the toolbar button | `Insert …/evaluation-template` |

- **Grade button** – Open **Settings** > **Toolbar**, and add `Templater: Insert Doc Gym/Templates/evaluation-template.md`. The toolbar appears above the keyboard while you edit.
- **Unused commands** – Ignore `Insert …/new-workout` and `Create …/evaluation-template`.
- **Ready focuses** – The focus picker marks each focus as ready or preparing. A ready workout opens at once. A focus that is still preparing shows a message right away. It also moves to the front of the queue.
- **Away from home** – Connect your VPN first if the server is only reachable through it.

## Settings

The templates read `Doc Gym/.doc-gym.json`, a hidden file that syncs with the vault. It holds the service addresses and the service token, and `install.sh` writes it. The vault never stores the Groq key.

## Troubleshooting

- **`Can't reach the Doc Gym service`**
  - Away from home, connect your VPN.
  - Check the service with `curl http://<server>:8765/health`. The reply starts with `{"ok": true`.
  - Restart the service on the server with `systemctl --user restart doc-gym`. Read the logs with `journalctl --user -u doc-gym -n 50`.
- **`Missing or wrong Doc Gym token`** – Run `./install.sh`, and then run `./ops/deploy-server.sh`. The token in `.env` on both machines must match `.doc-gym.json`.
- **`The model returned too little to work with`** – Small models sometimes produce thin output. Run it again.
- **`No … workout is ready yet`** – The server keeps one ready workout per focus. It makes a new one in the background after each use, which takes about three minutes. Pick a focus marked ready, or try again in a few minutes. After a prompt update, all eight focuses take about 20 minutes to fill.
- **`The style guide did not load`** – The server has no cached copy of Signal and cannot reach GitHub. Run `./ops/deploy-server.sh` again when the server is online. Grading works again once the server caches the guide.
- **`The draft is empty or under 20 words`** – Write under `## Draft`, and delete the placeholder comment.
- **`The note changed while grading`** – You typed during grading. Run it again.
