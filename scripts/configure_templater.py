#!/usr/bin/env python3
"""Register the Doc Gym templates as Templater hotkey targets, and undo the old shell-command setup.

The templates now call the Doc Gym service over HTTP, so Templater no longer needs to run
shell commands. System commands are switched off again unless something else still uses them.
"""
import json
import shutil
import sys
import time
from pathlib import Path

vault = Path(sys.argv[1])
cfg = vault / ".obsidian/plugins/templater-obsidian/data.json"
backup = cfg.with_name(f"data.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
shutil.copy2(cfg, backup)
d = json.loads(cfg.read_text())

pairs = [p for p in d.get("templates_pairs", []) if p[0] and p[0] not in ("gym_new", "gym_eval")]
d["templates_pairs"] = pairs or [["", ""]]
if not pairs:
    d["enable_system_commands"] = False

hot = [h for h in d.get("enabled_templates_hotkeys", []) if h]
for h in ("Doc Gym/Templates/new-workout.md", "Doc Gym/Templates/evaluation-template.md"):
    if h not in hot:
        hot.append(h)
d["enabled_templates_hotkeys"] = hot

cfg.write_text(json.dumps(d, indent=2))
print(f"   updated (backup: {backup.name}); system commands: {'on' if d['enable_system_commands'] else 'off'}")
