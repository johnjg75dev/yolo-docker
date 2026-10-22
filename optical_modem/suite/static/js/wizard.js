/* The guided flow: one stage at a time, with the work done inline.
 *
 * State comes from GET /api/suite/progress (server-computed, derived from disk
 * and the database). The page never marks a step done itself — it re-reads after
 * every action and after every job event, so the ticks match reality even when
 * the job outlives the tab.
 */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt, watchJob } = window.SUITE;
  const V = window.VIZ;
  const $ = UI.$;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  let progress = null;
  let index = 0;            // which stage the user is looking at
  let followedJob = null;   // job we are streaming into the log panel
  let autoAdvanced = "";    // remember the last auto-advance so we do it once

  /* --------------------------------------------------------- illustrations */
  /* Each step gets a small purpose-built SVG: a picture of what it produces is
     what turns a form into a guide. */
  const ART = {
    env: () => `<svg viewBox="0 0 320 120" class="art">
      <rect x="14" y="18" width="120" height="80" rx="8" fill="#121820" stroke="#2a3540"/>
      <text x="74" y="46" text-anchor="middle" fill="#8fa3b3" font-size="9">your server</text>
      <g stroke="#35d0a5" fill="none" stroke-width="1.4">
        <rect x="30" y="56" width="26" height="26" rx="4"/><path d="M56 69h8M34 56v-6M52 56v-6"/>
        <rect x="78" y="56" width="40" height="26" rx="4"/><path d="M84 62h28M84 68h20M84 74h24"/>
      </g>
      <text x="74" y="94" text-anchor="middle" fill="#35d0a5" font-size="8">CPU · RAM · disk</text>
      <path d="M140 58h44" stroke="#3ea6ff" stroke-width="1.6" stroke-dasharray="4 4"/>
      <path d="M184 58l-6-4v8z" fill="#3ea6ff"/>
      <g fill="#e9f2f7" font-size="9">
        <text x="196" y="46">torch</text><text x="196" y="62">ultralytics</text>
        <text x="196" y="78">opencv</text>
      </g>
      <g stroke="#35d0a5" stroke-width="1.6"><path d="M268 42l4 5 7-9"/><path d="M268 62l4 5 7-9"/>
        <path d="M268 82l4 5 7-9"/></g></svg>`,

    project: () => `<svg viewBox="0 0 320 120" class="art">
      <g fill="none" stroke="#2a3540"><rect x="20" y="24" width="92" height="66" rx="8"/>
        <rect x="122" y="24" width="92" height="66" rx="8"/></g>
      <g><rect x="20" y="24" width="92" height="14" rx="7" fill="#3ea6ff" opacity=".25"/>
        <text x="66" y="56" text-anchor="middle" fill="#e9f2f7" font-size="10">v0.3-hd</text>
        <text x="66" y="72" text-anchor="middle" fill="#ffb020" font-size="8">development</text></g>
      <g><rect x="122" y="24" width="92" height="14" rx="7" fill="#35d0a5" opacity=".25"/>
        <text x="168" y="56" text-anchor="middle" fill="#e9f2f7" font-size="10">v0.2-baseline</text>
        <text x="168" y="72" text-anchor="middle" fill="#35d0a5" font-size="8">finished ✓</text></g>
      <g fill="#8fa3b3" font-size="8">
        <text x="232" y="40">datasets</text><text x="232" y="56">runs</text>
        <text x="232" y="72">transfers</text></g>
      <g stroke="#8fa3b3" stroke-width="1.2" fill="none">
        <path d="M230 37h-10M230 53h-10M230 69h-10"/></g>
      <text x="232" y="100" fill="#8fa3b3" font-size="8">everything is filed</text></svg>`,

    dataset: () => `<svg viewBox="0 0 320 120" class="art">
      <g fill="#121820" stroke="#2a3540"><rect x="16" y="22" width="70" height="70" rx="8"/>
        <rect x="96" y="22" width="70" height="70" rx="8"/></g>
      <g stroke="#3ea6ff" fill="#3ea6ff" fill-opacity=".18">
        ${[[28, 34], [50, 34], [28, 56], [50, 56], [28, 74], [50, 74]]
          .map(([x, y]) => `<rect x="${x}" y="${y}" width="18" height="18" rx="3"/>`).join("")}
      </g>
      <text x="51" y="108" text-anchor="middle" fill="#8fa3b3" font-size="8">synthetic</text>
      <g stroke="#35d0a5" fill="none" stroke-width="1.3">
        <rect x="108" y="34" width="46" height="46" rx="6" transform="rotate(-6 131 57)"/>
        <path d="M114 48h34M114 58h34M114 68h34" stroke-dasharray="2 3"/>
      </g>
      <g fill="#35d0a5">
        <circle cx="114" cy="40" r="2.4"/><circle cx="148" cy="40" r="2.4"/>
        <circle cx="148" cy="76" r="2.4"/><circle cx="114" cy="76" r="2.4"/></g>
      <text x="131" y="108" text-anchor="middle" fill="#8fa3b3" font-size="8">phone captures</text>
      <path d="M176 57h30" stroke="#8fa3b3" stroke-dasharray="4 4"/>
      <path d="M206 57l-6-4v8z" fill="#8fa3b3"/>
      <g><rect x="214" y="26" width="90" height="62" rx="8" fill="#121820" stroke="#3ea6ff"/>
        <text x="259" y="44" text-anchor="middle" fill="#e9f2f7" font-size="9">dataset</text>
        <g fill="#8fa3b3" font-size="8" text-anchor="middle">
          <text x="259" y="60">images/ train · val</text>
          <text x="259" y="72">labels/ 4 keypoints</text>
          <text x="259" y="84" fill="#35d0a5">data.yaml ✓</text></g></g></svg>`,

    train: () => `<svg viewBox="0 0 320 120" class="art">
      <g stroke="#2a3540" fill="#121820">
        <rect x="16" y="26" width="86" height="66" rx="8"/></g>
      <g fill="none" stroke="#35d0a5" stroke-width="1.4">
        <rect x="30" y="40" width="34" height="34" rx="5"/><rect x="48" y="58" width="22" height="22" rx="4"/>
      </g>
      <text x="59" y="108" text-anchor="middle" fill="#8fa3b3" font-size="8">yolo26n-pose</text>
      <path d="M110 58h34" stroke="#3ea6ff" stroke-dasharray="4 4"/><path d="M144 58l-6-4v8z" fill="#3ea6ff"/>
      <g stroke="#2a3540" fill="#05070a"><rect x="152" y="22" width="152" height="74" rx="8"/></g>
      <g stroke="#8fa3b3" stroke-width=".8" opacity=".5">
        <path d="M162 40h132M162 56h132M162 72h132M162 88h132"/></g>
      <path d="M162 84 L182 74 L202 68 L222 58 L242 52 L262 48 L282 44"
            fill="none" stroke="#35d0a5" stroke-width="1.8"/>
      <text x="228" y="34" text-anchor="middle" fill="#8fa3b3" font-size="8">box mAP50-95</text>
      <text x="300" y="90" text-anchor="end" fill="#8fa3b3" font-size="8">epochs →</text></svg>`,

    verify: () => `<svg viewBox="0 0 320 120" class="art">
      <g stroke="#2a3540" fill="#121820"><rect x="14" y="24" width="120" height="72" rx="8"/></g>
      <g stroke="#8fa3b3" fill="none" stroke-width=".9" stroke-dasharray="2 3">
        <path d="M30 40h88M30 58h88M30 76h88"/></g>
      <g stroke="#35d0a5" fill="none" stroke-width="1.6">
        <rect x="44" y="38" width="60" height="42" rx="4" transform="rotate(4 74 59)"/>
        <circle cx="44" cy="38" r="2.6" fill="#35d0a5"/>
        <circle cx="104" cy="41" r="2.6" fill="#35d0a5"/>
        <circle cx="103" cy="79" r="2.6" fill="#35d0a5"/>
        <circle cx="43" cy="76" r="2.6" fill="#35d0a5"/></g>
      <text x="74" y="108" text-anchor="middle" fill="#8fa3b3" font-size="8">4 corners, 0.04 err</text>
      <path d="M144 60h34" stroke="#3ea6ff" stroke-dasharray="4 4"/><path d="M178 60l-6-4v8z" fill="#3ea6ff"/>
      <g><rect x="188" y="30" width="116" height="60" rx="8" fill="rgba(53,208,165,.1)" stroke="#35d0a5"/>
        <text x="246" y="52" text-anchor="middle" fill="#e9f2f7" font-size="9">promote → active</text>
        <text x="246" y="70" text-anchor="middle" fill="#35d0a5" font-size="8">the S2C receiver uses it</text></g></svg>`,

    send: () => `<svg viewBox="0 0 320 120" class="art">
      <g><rect x="12" y="26" width="132" height="76" rx="6" fill="#05070a" stroke="#2a3540"/>
        ${Array.from({ length: 6 }).map((_, r) => Array.from({ length: 6 }).map((__, c) => {
          const on = (r * 7 + c * 3) % 5 < 2;
          return `<rect x="${26 + c * 18}" y="${38 + r * 10}" width="15" height="7" rx="1"
                   fill="${on ? "#3ea6ff" : "#14304a"}" opacity="${on ? 0.95 : 0.6}"/>`;
        }).join("")).join("")}</g>
      <g stroke="#3ea6ff" stroke-width="1" fill="none" opacity=".8">
        <path d="M150 64q22-18 42 0"/><path d="M150 64q22 18 42 0" stroke-dasharray="3 3"/></g>
      <text x="171" y="46" text-anchor="middle" fill="#8fa3b3" font-size="8">photons</text>
      <g><rect x="196" y="34" width="54" height="60" rx="8" fill="#121820" stroke="#35d0a5"/>
        <rect x="204" y="42" width="38" height="38" rx="3" fill="none" stroke="#35d0a5"/>
        <circle cx="223" cy="61" r="8" fill="none" stroke="#35d0a5"/>
        <rect x="216" y="86" width="14" height="4" rx="2" fill="#35d0a5"/></g>
      <text x="223" y="108" text-anchor="middle" fill="#8fa3b3" font-size="8">phone camera</text>
      <path d="M258 64h18" stroke="#35d0a5"/><path d="M276 64l-6-4v8z" fill="#35d0a5"/>
      <g fill="#8fa3b3" font-size="8"><text x="284" y="56">file</text>
        <text x="284" y="70" fill="#35d0a5">sha1 ✓</text></g></svg>`,
  };

  /* --------------------------------------------------------------- render */
  function rail() {
    const host = $("#rail");
    host.innerHTML = progress.stages.map((s, i) => `
      <button class="wrail-item${i === index ? " on" : ""}${s.done ? " done" : ""}${s.current ? " cur" : ""}"
              data-i="${i}">
        <span class="wi">${s.done ? V.icon("check", 16) : V.icon(s.icon, 16)}</span>
        <span class="wt"><b>${esc(s.title)}</b><em>${esc(s.detail)}</em></span>
        <span class="wn">${s.done ? "✓" : i + 1}</span>
      </button>`).join("");
    host.querySelectorAll(".wrail-item").forEach((btn) => {
      btn.addEventListener("click", () => { index = Number(btn.dataset.i); pump(); });
    });
  }

  function kpis() {
    const c = progress.counts || {};
    $("#kpis").innerHTML = [
      ["datasets", c.datasets], ["labelled images", c.images],
      ["runs", c.runs], ["models", c.models], ["projects", c.projects],
      ["transfers", c.transfers], ["phone shots", c.shots],
    ].map(([l, v]) => `<div class="kpi"><span class="l">${esc(l)}</span>
      <span class="v">${v == null ? "–" : v}</span></div>`).join("");
    $("#state-line").textContent =
      `${progress.done}/${progress.total} steps · ${progress.percent}% complete`;
    const lj = progress.last_job;
    $("#describe").innerHTML = lj
      ? `last job: <b>${esc(lj.title)}</b> — ${esc(lj.status)}` +
        (lj.live ? " (running now)" : "")
      : "no jobs have run on this box yet";
  }

  function stepView() {
    const stages = progress.stages;
    const s = stages[Math.min(index, stages.length - 1)];
    $("#step-kicker").textContent = `step ${index + 1} of ${stages.length}`;
    $("#step-title").textContent = s.title;
    $("#step-icon").innerHTML = V.icon(s.done ? "check" : s.icon, 22);
    $("#step-chip").className = "chip " + (s.done ? "ok" : s.current ? "run" : "");
    $("#step-chip").textContent = s.done ? "done" : s.current ? "you are here" : "locked";
    $("#step-art").innerHTML = (ART[s.id] || (() => ""))();
    $("#step-why").innerHTML = `<b>Why:</b> ${esc(s.why)}`;
    $("#step-ticks").innerHTML = (s.checklist || []).map((c) =>
      `<li class="${c.done ? "done" : ""}">${c.done ? "✓" : "○"} ${esc(c.label)}</li>`).join("");

    const blockers = s.current ? (s.blocked_by || []) : [];
    $("#step-blockers").innerHTML = blockers.length
      ? `<div class="callout warn small" style="margin-top:10px">
           <b>Later steps are waiting on this one:</b>
           <ul style="margin:6px 0 0 16px">${blockers.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
         </div>`
      : "";

    const actions = [];
    if (s.action && s.action.kind === "post" && !s.done) {
      actions.push(`<button class="act-run" data-act="run">${esc(s.action.label)}</button>`);
      actions.push(`<a class="ghost" style="text-decoration:none;padding:10px 14px"
        href="${s.action.then || s.href}">open the full page →</a>`);
    } else {
      actions.push(`<a class="act-open" style="text-decoration:none;padding:10px 14px"
        href="${s.href}">${esc(s.cta)} →</a>`);
    }
    if (s.id === "project" && !s.done) {
      actions.push(`<button class="ghost" data-act="quick-project">create “${esc(
        "first-run") }” for me</button>`);
    }
    if (s.id === "verify" && !s.done) {
      actions.push(`<button class="ghost" data-act="promote">promote the newest run</button>`);
    }
    $("#step-actions").innerHTML = actions.join("");
    wireActions(s);

    $("#prev").disabled = index === 0;
    $("#next").disabled = index >= stages.length - 1;
    $("#next").textContent = index >= stages.length - 1 ? "finish" : "next →";
  }

  /* -------------------------------------------------------------- actions */
  function showJobPanel(job) {
    $("#step-job").style.display = "";
    $("#job-title").textContent = job.title || job.kind || "job";
    followedJob = job.id;
    watchJob(job.id, (snap) => {
      const pct = Math.round(100 * (snap.progress || 0));
      $("#job-pct").textContent = pct + "%";
      $("#job-bar").style.width = pct + "%";
      $("#job-log").textContent = (snap.tail || []).slice(-20).join("\n");
      if (!snap.live) {
        followedJob = null;
        UI.toast(snap.status === "ok" ? "job finished ✓" : "job failed", snap.status !== "ok");
        refresh();
      }
    }, () => refresh());
  }

  async function wireActions(s) {
    const run = $("#step-actions").querySelector('[data-act="run"]');
    if (run) {
      run.addEventListener("click", async () => {
        const payload = Object.assign({}, s.action.payload || {});
        if (s.id === "dataset") {
          payload.name = payload.name || `guided-${new Date().toISOString().slice(4, 10).replace(/-/g, "")}`;
          payload.project = window.SUITE.project();
        }
        run.disabled = true;
        run.textContent = "starting…";
        try {
          const res = await API.post(s.action.endpoint, payload);
          UI.toast("started — watch the log");
          if (res.job) showJobPanel(res.job);
          refresh();
        } catch (e) {
          UI.toast(e.message, true);
          run.disabled = false;
          run.textContent = s.action.label;
        }
      });
    }
    const proj = $("#step-actions").querySelector('[data-act="quick-project"]');
    if (proj) {
      proj.addEventListener("click", async () => {
        try {
          await API.post("/api/suite/projects", {
            slug: "first-run", title: "First run",
            description: "created by the guided setup",
            status: "development", tags: ["guided"],
          });
          UI.toast("project “first-run” created");
          refresh();
        } catch (e) { UI.toast(e.message, true); }
      });
    }
    const promote = $("#step-actions").querySelector('[data-act="promote"]');
    if (promote) {
      promote.addEventListener("click", async () => {
        try {
          const runs = await API.get("/api/suite/train/defaults");
          const trained = (runs.runs || []).filter((r) => r.has_weights);
          if (!trained.length) { UI.toast("no trained run yet — do step 4 first", true); return; }
          const newest = trained[0];
          await API.post("/api/suite/models/promote", { run: newest.name });
          UI.toast(`${newest.name} is now the active model`);
          refresh();
        } catch (e) { UI.toast(e.message, true); }
      });
    }
  }

  /* ---------------------------------------------------------------- shell */
  function shell() {
    $("#ring").innerHTML = V.ring(progress.percent, {
      size: 104, sub: `${progress.done}/${progress.total} steps` });
    const activeId = ["screen", "optic", "camera", "detector", "fec", "file"]
      .find((id, i) => i === Math.min(index, 5));
    $("#pipeline").innerHTML = V.pipeline(progress.stages, { active: activeId });
    $("#live-chip").className = "chip " + (progress.complete ? "ok" : "run");
    $("#live-chip").textContent = progress.complete
      ? "all steps done — go send something"
      : `${progress.total - progress.done} step(s) left`;
  }

  function pump() {
    rail(); stepView(); shell(); kpis();
  }

  async function refresh({ keepIndex = true } = {}) {
    const previous = progress;
    try {
      progress = await API.get("/api/suite/progress");
    } catch (e) {
      UI.toast(e.message, true);
      return;
    }
    if (!keepIndex || previous == null) {
      index = progress.next ? progress.stages.findIndex((s) => s.id === progress.next.id)
                            : progress.stages.length - 1;
    } else {
      index = Math.min(index, progress.stages.length - 1);
      // when the step you are looking at just completed, move the view forward
      const cur = progress.stages[index];
      if (cur && cur.done && progress.next && autoAdvanced !== progress.next.id) {
        const nxt = progress.stages.findIndex((s) => s.id === progress.next.id);
        if (nxt > index) {
          autoAdvanced = progress.next.id;
          index = nxt;
          UI.toast(`“${cur.title}” done — on to “${progress.stages[nxt].title}”`);
          UI.vibrate && UI.vibrate(18);
        }
      }
    }
    pump();
  }

  /* ---------------------------------------------------------------- boot */
  chrome("/wizard");

  $("#prev").addEventListener("click", () => {
    if (index > 0) { index -= 1; autoAdvanced = ""; pump(); }
  });
  $("#next").addEventListener("click", () => {
    if (index < progress.stages.length - 1) { index += 1; autoAdvanced = ""; pump(); }
    else location.href = "/s2c";
  });
  $("#skip").addEventListener("click", () => {
    if (index < progress.stages.length - 1) { index += 1; autoAdvanced = ""; pump(); }
  });
  document.addEventListener("keydown", (ev) => {
    if (!progress) return;                       // still loading
    if (ev.target.matches && ev.target.matches("input,textarea,select")) return;
    if (ev.key === "ArrowRight" && index < progress.stages.length - 1) {
      index += 1; autoAdvanced = ""; pump();
    }
    if (ev.key === "ArrowLeft" && index > 0) { index -= 1; autoAdvanced = ""; pump(); }
  });

  refresh({ keepIndex: false });
  // a running training job changes the state as it goes: poll (cheap, server-side
  // derived) and let the SSE stream trigger an immediate re-read as well
  setInterval(() => { if (!followedJob) refresh(); }, 6000);
  if (window.EventSource) {
    const es = new EventSource("/api/events");
    ["job.finished", "job.failed", "dataset.upserted", "model.promoted",
     "project.created", "s2c.complete"].forEach((name) =>
      es.addEventListener(name, () => refresh()));
  }
})();
