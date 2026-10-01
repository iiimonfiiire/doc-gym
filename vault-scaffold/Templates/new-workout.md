<%*
// Doc Gym: pick intensity and focus, get a workout from the Doc Gym service.
// Pure Obsidian JS, so it works on desktop and mobile. Settings: Doc Gym/.doc-gym.json
// Works from both Templater commands:
//   "Create new note from template" (recommended): turns the new note into the workout.
//   "Insert template": leaves the open note alone and opens the workout in a new tab.
const creating = tp.config.run_mode === 0;
// In create mode, throwing makes Templater delete its empty new note, so nothing stray is left.
const stop = msg => { new Notice(msg, 10000); if (creating) throw new Error(msg); };

let cfg;
try {
  cfg = JSON.parse(await app.vault.adapter.read("Doc Gym/.doc-gym.json"));
} catch (e) {
  return stop("Doc Gym: missing Doc Gym/.doc-gym.json. Run install.sh on your desktop.");
}

async function call(path, body) {
  for (const base of cfg.urls) {
    // requestUrl has no timeout, so probe each address quickly before the real call.
    const alive = await Promise.race([
      tp.obsidian.requestUrl({ url: `${base}/health`, throw: false }).then(r => r.status === 200, () => false),
      new Promise(r => setTimeout(() => r(false), 4000)),
    ]);
    if (!alive) continue;
    const res = await tp.obsidian.requestUrl({
      url: base + path, method: "POST", contentType: "application/json", throw: false,
      headers: { Authorization: `Bearer ${cfg.token}` }, body: JSON.stringify(body),
    });
    let data = {};
    try { data = res.json; } catch (e) {}
    if (res.status === 200) return data;
    throw new Error(data.error ?? `HTTP ${res.status}`);
  }
  throw new Error("Can't reach the Doc Gym service. Away from home? Turn on your VPN (e.g. Tailscale).");
}

const intensity = await tp.system.suggester(
  ["Sprint · ~150 words · 15 min", "Routine · ~400 words · 30 min", "Endurance · ~900 words · 60 min"],
  ["Sprint", "Routine", "Endurance"], false, "Intensity");
if (!intensity) return stop("Doc Gym: cancelled.");
const focus = await tp.system.suggester(
  ["SaaS Docs", "API Specs", "Troubleshooting", "Release Notes"],
  ["SaaS Docs", "API Specs", "Troubleshooting", "Release Notes"], false, "Focus");
if (!focus) return stop("Doc Gym: cancelled.");

const notice = new Notice(`Doc Gym: generating ${intensity} · ${focus}… (usually instant; up to 3 minutes if none is ready)`, 0);
let workout;
try {
  workout = await call("/generate", { intensity, focus });
} catch (e) {
  notice.hide();
  return stop(`Doc Gym: prompt generation failed.\n${e.message ?? e}`);
}
notice.hide();

const path = app.vault.getAvailablePath(`Doc Gym/Prompts/${workout.stem}`, "md");
if (creating) {
  await tp.file.move(path.replace(/\.md$/, ""));
  tR += workout.content;
} else {
  const file = await app.vault.create(path, workout.content);
  await app.workspace.getLeaf(true).openFile(file);
}
%>
