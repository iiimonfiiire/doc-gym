---
tags: [doc-gym]
cssclasses: [doc-gym-dashboard]
---
# 🏋️ Doc Gym

New workout: **⌘⌥G** · Grade the open workout: **⌘⌥E** · Setup: [[Doc Gym/README|README]]

```dataviewjs
const pages = dv.pages('"Doc Gym/Prompts" or "Doc Gym/Submissions"')
  .where(p => p.file.tags.includes("#doc-gym") && p.status);
const done = pages.where(p => p.status === "completed");
const toISO = d => (d && d.toISODate) ? d.toISODate() : String(d ?? "").slice(0, 10);
const dayOf = p => toISO(p.evaluated ?? p.date);
const pts = p => parseInt(String(p.score ?? "").split("/")[0], 10);

// Streaks count calendar days with at least one graded workout.
const days = [...new Set(done.map(dayOf).values)].filter(Boolean).sort();
const DAY = 86400000;
const t = s => Date.parse(s + "T00:00:00Z");
let longest = 0, run = 0;
days.forEach((d, i) => {
  run = (i > 0 && t(d) - t(days[i - 1]) === DAY) ? run + 1 : 1;
  longest = Math.max(longest, run);
});
const today = dv.date("today").toISODate();
const yesterday = dv.date("today").minus({ days: 1 }).toISODate();
let current = 0;
if (days.length && (days.at(-1) === today || days.at(-1) === yesterday)) {
  current = 1;
  for (let i = days.length - 1; i > 0 && t(days[i]) - t(days[i - 1]) === DAY; i--) current++;
}
const scores = done.map(pts).values.filter(n => !isNaN(n));
const avg = scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : "–";

dv.paragraph(
  `**Completed:** ${done.length} · **Current streak:** ${current} day${current === 1 ? "" : "s"}` +
  ` · **Longest:** ${longest} · **Avg score:** ${avg}/30 · **Pending:** ${pages.length - done.length}`);

dv.header(3, "Completed workouts");
dv.table(["Date", "Title", "Focus", "Intensity", "Tone", "Grade", "Score", "Status"],
  done.sort(p => dayOf(p), "desc").map(p => [
    dayOf(p), p.file.link, p.focus, p.intensity, p.tone, p.grade, p.score, p.status]));

const pending = pages.where(p => p.status !== "completed");
if (pending.length) {
  dv.header(3, "Pending");
  dv.table(["Date", "Title", "Focus", "Intensity", "Tone"],
    pending.sort(p => toISO(p.date), "desc").map(p => [toISO(p.date), p.file.link, p.focus, p.intensity, p.tone]));
}
```
