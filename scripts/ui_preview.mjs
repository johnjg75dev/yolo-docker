/* Build a self-contained "what the UI looks like" sheet from a *running* server.
 *
 *   node scripts/ui_preview.mjs --base http://127.0.0.1:8200 --password demo \
 *        --out devdata/ui_preview.html
 *
 * Why: the suite's visuals are generated in the browser (SVG from live state), so
 * a screenshot is the only other way to review them. This renders the same
 * components with the same code and the same data into one static file — useful
 * for design review, for a ticket, and for checking the charts without a browser.
 *
 * Everything is inlined (CSS, SVG, no network) so the file opens anywhere.
 */
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((out, arg, i, all) => {
  if (arg.startsWith("--")) out.push([arg.slice(2), all[i + 1]]);
  return out;
}, []));
const BASE = (args.base || "http://127.0.0.1:8200").replace(/\/$/, "");
const PASSWORD = args.password || process.env.APP_PASSWORD || "demo";
const OUT = args.out || "devdata/ui_preview.html";

let cookie = "";
async function api(p, init = {}) {
  const res = await fetch(BASE + p, {
    ...init,
    headers: { "Content-Type": "application/json", cookie, ...(init.headers || {}) },
  });
  const set = res.headers.get("set-cookie");
  if (set) cookie = set.split(";")[0];
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

await api("/api/login", { method: "POST", body: JSON.stringify({ password: PASSWORD }) });
const progress = await api("/api/suite/progress");
const palette = await api("/api/suite/palette");
const runs = await api("/api/suite/models");
const curveRun = (runs.runs || []).find((r) => r.has_weights);
const curve = curveRun ? await api(`/api/suite/runs/${encodeURIComponent(curveRun.name)}/curve`)
                       : { epochs: [], series: {} };
const shapes = (await api("/api/suite/s2c/options")).finder_shapes.map((s) => s.name);
const finders = {};
for (const s of shapes) {
  finders[s] = await api(`/api/suite/finder/${encodeURIComponent(s)}.svg?size=64`);
}

/* --- real CSS, inlined ---------------------------------------------------- */
const cssFiles = [
  "optical_modem/capture/static/css/app.css",
  "optical_modem/suite/static/css/suite.css",
];
const css = cssFiles.filter((f) => fs.existsSync(f))
  .map((f) => fs.readFileSync(f, "utf8")).join("\n");

/* --- the real viz.js ----------------------------------------------------- */
global.window = {};
global.document = { querySelector: () => null };
eval(fs.readFileSync("optical_modem/suite/static/js/viz.js", "utf8"));
const V = global.window.VIZ;

/* --- the wizard's illustrations, lifted from wizard.js ------------------- */
const wizardSrc = fs.readFileSync("optical_modem/suite/static/js/wizard.js", "utf8");
const start = wizardSrc.indexOf("const ART = {");
let depth = 0, end = -1;
for (let i = wizardSrc.indexOf("{", start); i < wizardSrc.length; i++) {
  if (wizardSrc[i] === "{") depth++;
  else if (wizardSrc[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
const ART = eval("(" + wizardSrc.slice(wizardSrc.indexOf("{", start), end) + ")");

/* --- compose ------------------------------------------------------------- */
const steps = progress.stages;
const section = (title, body, note = "") => `
  <section class="card">
    <div class="row spread"><h2 style="margin:0 0 6px">${title}</h2>
      ${note ? `<span class="small muted">${note}</span>` : ""}</div>
    ${body}
  </section>`;

const rail = steps.map((s, i) => `
  <button class="wrail-item${s.done ? " done" : ""}${s.current ? " cur" : ""}${i === 2 ? " on" : ""}">
    <span class="wi">${s.done ? V.icon("check", 16) : V.icon(s.icon, 16)}</span>
    <span class="wt"><b>${s.title}</b><em>${s.detail}</em></span>
    <span class="wn">${s.done ? "✓" : i + 1}</span>
  </button>`).join("");

const stepCard = (s, idx) => `
  <section class="card wstep">
    <div class="row spread" style="align-items:flex-start">
      <div class="row" style="gap:10px;align-items:center">
        <span class="wicon">${V.icon(s.done ? "check" : s.icon, 22)}</span>
        <div><div class="small muted">step ${idx + 1} of ${steps.length}</div>
          <h2 style="margin:0">${s.title}</h2></div>
      </div>
      <span class="chip ${s.done ? "ok" : s.current ? "run" : ""}">${
        s.done ? "done" : s.current ? "you are here" : "locked"}</span>
    </div>
    <div class="wstep-art">${(ART[s.id] || (() => ""))()}</div>
    <p class="why"><b>Why:</b> ${s.why}</p>
    <ul class="ticks">${s.checklist.map((c) =>
      `<li class="${c.done ? "done" : ""}">${c.done ? "✓" : "○"} ${c.label}</li>`).join("")}</ul>
    ${s.current && s.blocked_by && s.blocked_by.length ? `<div class="callout warn small">
      <b>Later steps are waiting on this one:</b>
      <ul style="margin:6px 0 0 16px">${s.blocked_by.map((b) => `<li>${b}</li>`).join("")}</ul>
    </div>` : ""}
    <div class="row" style="gap:10px;flex-wrap:wrap;margin-top:14px">
      <button>${s.action && s.action.label ? s.action.label : s.cta}</button>
      <a class="ghost" style="text-decoration:none;padding:10px 14px" href="#">open the full page →</a>
    </div>
  </section>`;

const series = {};
["metrics/mAP50-95(B)", "metrics/mAP50-95(P)", "train/box_loss", "val/box_loss"]
  .forEach((k) => { if (curve.series[k]) series[k] = curve.series[k]; });

const bars = curve.epochs.map((e, i) => ({
  label: "epoch " + e,
  value: curve.series["metrics/mAP50-95(B)"] ? curve.series["metrics/mAP50-95(B)"][i] || 0 : 0,
  text: "mAP " + ((curve.series["metrics/mAP50-95(B)"] || [])[i] ?? 0).toFixed(2),
  target: 1,
}));

const modRows = palette.modulations.map((m) => `
  <tr><td>${m.name}</td><td class="num">${m.bits}</td>
    <td>${V.swatchStrip(m.hex)}</td>
    <td class="num">${m.max_user_payload} B</td>
    <td>${m.pair ? "hue pairs" : "direct"}</td></tr>`).join("");

const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suite UI preview</title>
<style>${css}
  body { padding: 0 0 60px; }
  .sheet { max-width: 1120px; margin: 0 auto; padding: 22px 18px; }
  .sheet h1 { margin-bottom: 4px; }
  .shot { border: 1px solid var(--line); border-radius: 14px; padding: 14px;
          background: rgba(255,255,255,.012); margin-bottom: 16px; }
  .shot-label { font-size: .72rem; letter-spacing: .08em; text-transform: uppercase;
                color: var(--muted); margin-bottom: 10px; }
</style></head>
<body class="suite"><div class="sheet">
  <h1>What the guided UI looks like</h1>
  <p class="small muted">Rendered from this server's live state — the same CSS and the
    same <code>viz.js</code> the pages use. Generated ${new Date().toISOString().slice(0, 16)
      .replace("T", " ")} UTC against <code>${BASE}</code>.</p>

  <div class="shot"><div class="shot-label">Dashboard · pipeline + progress ring</div>
    <div class="row" style="gap:18px;align-items:center;flex-wrap:wrap">
      <div>${V.ring(progress.percent, { size: 104, sub: `${progress.done}/${progress.total} steps` })}</div>
      <div style="flex:1;min-width:320px">${V.pipeline(steps)}</div>
    </div>
    <div class="grid3" style="margin-top:14px">
      <div class="card"><h3 style="margin:0">Do this next</h3>
        <p class="small" style="margin:4px 0">${progress.next
          ? progress.next.title + " — " + progress.next.detail
          : "everything is done"}</p>
        <span class="chip ok">Walk me through it →</span></div>
      <div class="card"><h3 style="margin:0">Counts</h3>
        <div class="kpis" style="margin-top:8px">${
          Object.entries(progress.counts).slice(0, 6).map(([k, v]) =>
            `<div class="kpi"><span class="l">${k}</span><span class="v">${v}</span></div>`).join("")}
        </div></div>
      <div class="card"><h3 style="margin:0">Stage rail</h3>
        <div class="steps" style="grid-template-columns:1fr 1fr;margin-top:8px">${
          steps.slice(0, 4).map((s, i) => `<div class="step${s.done ? " done" : i ===
            steps.findIndex((x) => x.current) ? " next" : ""}">
            <div class="n">${s.done ? "✓" : i + 1}</div><div class="t">${s.title}</div>
            <div class="d">${s.detail}</div></div>`).join("")}</div></div>
    </div>
  </div>

  <div class="shot"><div class="shot-label">Guided setup · rail + step card (live state)</div>
    <div class="wizard">
      <nav class="wrail">${rail}</nav>
      <div>${stepCard(steps.find((s) => s.current) || steps[steps.length - 1],
                       Math.max(0, steps.findIndex((s) => s.current)))}</div>
    </div>
  </div>

  <div class="shot"><div class="shot-label">Guided setup · illustrations, one per step</div>
    <div class="grid2">${steps.map((s) => `<div class="wstep-art">${(ART[s.id] || (() => ""))()}</div>`).join("")}</div>
  </div>

  <div class="shot"><div class="shot-label">S2C · finder picker (served as SVG from the same mask the transmitter uses)</div>
    <div class="finder-grid">${shapes.map((s) =>
      `<span class="finder-tile${s === "ring" ? " sel" : ""}">${finders[s]
        .replace('<svg ', '<svg width="56" height="56" ')}<span>${s}</span></span>`).join("")}</div>
  </div>

  <div class="shot"><div class="shot-label">S2C · modulation palettes and what they cost</div>
    <table class="t"><thead><tr><th>modulation</th><th class="num">bit/cell</th>
      <th>palette</th><th class="num">max bytes/frame</th><th>mode</th></tr></thead>
      <tbody>${modRows}</tbody></table>
  </div>

  <div class="shot"><div class="shot-label">Train · training curves from results.csv${
    curveRun ? " (" + curveRun.name + ")" : " (no run yet)"}</div>
    ${curve.epochs.length > 1 ? V.lineChart(curve.epochs, series)
      : '<p class="small muted">no trained run on this box yet — the card stays hidden</p>'}
  </div>

  <div class="shot"><div class="shot-label">Verify · decode rate by channel level</div>
    ${V.bars([{ label: "level 0.2", value: 0.98, text: "98%" },
              { label: "level 0.5", value: 0.86, text: "86%" },
              { label: "level 0.8", value: 0.54, text: "54%" },
              { label: "level 1.0", value: 0.21, text: "21%" }])}
    <p class="hint">Illustrative bars in this sheet; on the page they come from
      <code>GET /api/suite/runs/&lt;run&gt;/eval</code>.</p>
  </div>

  <div class="shot"><div class="shot-label">Empty state (used on Dataset / Train / Models when there is nothing yet)</div>
    <div class="empty"><span style="color:var(--muted)">${V.icon("grid", 34)}</span>
      <div><div class="e-t">No datasets yet</div>
        <div class="e-b">Start with synthetic frames — they are instant, cover the
          geometry, and 120 of them train a usable corner detector on CPU.</div>
        <a class="chip" href="#">Generate one →</a></div></div>
  </div>
</div></body></html>`;

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, html);
console.log(`wrote ${OUT} (${(html.length / 1024).toFixed(0)} kB, ${steps.length} stages, ` +
            `${Object.keys(finders).length} finder shapes, curve ${curve.epochs.length} epochs)`);
