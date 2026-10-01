<%*
// Doc Gym: grade the open note's ## Draft via the Doc Gym service, then write the
// grade and ## Evaluation back into the note. Works on desktop and mobile. Inserts nothing.
// Run it with "Templater: Insert Doc Gym/Templates/evaluation-template.md" (not "Create").
if (tp.config.run_mode === 0) {
  // Throwing makes Templater delete the empty note it just created.
  const msg = "Doc Gym: grading must run on the open note. Use \"Templater: Insert …evaluation-template.md\".";
  new Notice(msg, 12000);
  throw new Error(msg);
}
const file = tp.config.target_file;
if (!file || !/^Doc Gym\/(Prompts|Submissions)\//.test(file.path)) {
  new Notice("Doc Gym: open a workout note in Doc Gym/Prompts first.");
  return;
}

let cfg;
try {
  cfg = JSON.parse(await app.vault.adapter.read("Doc Gym/.doc-gym.json"));
} catch (e) {
  new Notice("Doc Gym: missing Doc Gym/.doc-gym.json. Run install.sh on your desktop.", 10000);
  return;
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

// Flush unsaved typing so the service grades what's on screen.
await app.commands.executeCommandById("editor:save-file");
await new Promise(r => setTimeout(r, 300));
const original = await app.vault.read(file);

const notice = new Notice("Doc Gym: grading your draft…", 0);
try {
  const result = await call("/evaluate", { content: original });
  await app.vault.process(file, current => {
    if (current !== original) throw new Error("The note changed while grading. Run it again.");
    return result.content;
  });
  notice.hide();
  new Notice(`Doc Gym: ${result.grade} ${result.score}`, 8000);
  // Let the open editor pick up the new text before Templater saves it.
  await new Promise(r => setTimeout(r, 300));
} catch (e) {
  notice.hide();
  new Notice(`Doc Gym: evaluation failed.\n${e.message ?? e}`, 15000);
}
%>
