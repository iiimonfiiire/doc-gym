<%*
// Doc Gym: pick intensity and focus, get a workout from the Doc Gym service.
// Pure Obsidian JS, so it works on desktop and mobile. Settings: Doc Gym/.doc-gym.json
// Run it with "Templater: Create Doc Gym/Templates/new-workout.md" (or the Dashboard button):
// it turns the new note into the workout. With "Insert" it opens the workout in a new tab instead.
const creating = tp.config.run_mode === 0;
// In create mode, throwing makes Templater delete its empty new note, so nothing stray is left.
const stop = msg => { new Notice(msg, 12000); if (creating) throw new Error(msg); };

let cfg;
try {
  cfg = JSON.parse(await app.vault.adapter.read("Doc Gym/.doc-gym.json"));
} catch (e) {
  return stop("Doc Gym: missing Doc Gym/.doc-gym.json. Run install.sh on your desktop.");
}

// Find the first service address that answers. requestUrl has no timeout, so race a timer.
async function findService() {
  for (const base of cfg.urls) {
    const health = await Promise.race([
      tp.obsidian.requestUrl({ url: `${base}/health`, throw: false })
        .then(r => (r.status === 200 ? r.json : null), () => null),
      new Promise(r => setTimeout(() => r(null), 4000)),
    ]);
    if (health) return { base, health };
  }
  return null;
}

const service = await findService();
if (!service) return stop("Doc Gym: can't reach the Doc Gym service. Away from home? Turn on your VPN (e.g. Tailscale).");
const ready = service.health.ready ?? [];
const label = f => (service.health.buffer ? `${f} · ${ready.includes(f) ? "ready" : "preparing (a few min)"}` : f);

const intensity = await tp.system.suggester(
  ["Sprint · 15 min", "Routine · 30 min", "Endurance · 60 min"],
  ["Sprint", "Routine", "Endurance"], false, "Intensity");
if (!intensity) return stop("Doc Gym: cancelled.");
const focuses = ["SaaS Docs", "API Specs", "Troubleshooting", "Release Notes",
  "UX Microcopy", "GTM Brief", "Product Walkthrough", "Tutorial"];
const focus = await tp.system.suggester(focuses.map(label), focuses, false, "Focus");
if (!focus) return stop("Doc Gym: cancelled.");

const notice = new Notice(`Doc Gym: getting your ${intensity} · ${focus} workout…`, 0);
const res = await tp.obsidian.requestUrl({
  url: `${service.base}/generate`, method: "POST", contentType: "application/json", throw: false,
  headers: { Authorization: `Bearer ${cfg.token}` }, body: JSON.stringify({ intensity, focus }),
}).catch(e => ({ status: 0, error: e }));
notice.hide();
let workout = {};
try { workout = res.json; } catch (e) {}
if (res.status !== 200) {
  return stop(`Doc Gym: ${workout.error ?? res.error?.message ?? `HTTP ${res.status}`}`);
}

const path = app.vault.getAvailablePath(`Doc Gym/Prompts/${workout.stem}`, "md");
if (creating) {
  await tp.file.move(path.replace(/\.md$/, ""));
  tR += workout.content;
} else {
  const file = await app.vault.create(path, workout.content);
  await app.workspace.getLeaf(true).openFile(file);
}
%>
