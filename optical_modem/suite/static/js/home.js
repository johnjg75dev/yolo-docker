/* Dashboard: pipeline state, what to do next, recent jobs. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt, statusChip } = window.SUITE;
  const $ = UI.$;

  const NEXT = {
    env: {
      title: "Check the box can train",
      body: "torch and ultralytics must be importable. On a CPU-only VPS the " +
            "small model at 448 px is the right choice; a GPU changes the presets " +
            "for you on the Train page.",
      cta: ["Open the guide", "/guide"],
    },
    dataset: {
      title: "Build a dataset",
      body: "Generate a few hundred synthetic frames first (minutes), then add " +
            "real captures from the phone. Merge them into one dataset before " +
            "training.",
      cta: ["Create a dataset", "/dataset"],
    },
    train: {
      title: "Train the detector",
      body: "Fine-tune yolo26n-pose on the merged dataset. 40 epochs at 448 px " +
            "is ~10–25 min on 4 CPU cores for a few hundred images.",
      cta: ["Start training", "/train"],
    },
    evaluate: {
      title: "Verify and promote",
      body: "Evaluate the run (corner error + decode rate), run the loopback " +
            "check, then promote the run to the active model.",
      cta: ["Verify a run", "/evaluate"],
    },
    s2c: {
      title: "Send a file over the screen",
      body: "Open S2C on the laptop, start a transfer, and read it back with the " +
            "phone camera. Every frame is decoded with the active model.",
      cta: ["Open the S2C app", "/s2c"],
    },
  };

  function renderSteps(stages) {
    const host = $("#steps");
    host.innerHTML = "";
    const nextIdx = stages.findIndex((s) => !s.done);
    stages.forEach((s, i) => {
      const a = document.createElement("a");
      a.className = "step" + (s.done ? " done" : i === nextIdx ? " next" : "");
      a.href = s.href;
      a.title = s.why || "";
      const ic = window.VIZ ? window.VIZ.icon(s.done ? "check" : s.icon, 15) : "";
      a.innerHTML = `<div class="n">${s.done ? "✓" : i + 1}</div>` +
                    `<div class="t">${s.title}</div>` +
                    `<div class="d">${s.detail}</div>` +
                    (s.done ? "" : `<div class="d" style="color:var(--muted);opacity:.8;
                       display:flex;gap:6px;align-items:center;margin-top:6px">${ic}
                       ${s.current ? "you are here" : "after the step before"}</div>`);
      host.appendChild(a);
    });
    return nextIdx === -1 ? stages.length - 1 : nextIdx;
  }

  /* ---------------------------------------------------------- pipeline art */
  function renderPipeline(progress) {
    if (!window.VIZ) return;
    $("#pipeline").innerHTML = window.VIZ.pipeline(progress.stages, {
      active: (progress.next || {}).icon === "signal" ? "file"
             : (progress.next || {}).id === "dataset" ? "camera" : null });
    $("#ring").innerHTML = window.VIZ.ring(progress.percent, {
      size: 96, sub: `${progress.done}/${progress.total}` });
    $("#ring-copy").innerHTML = progress.complete
      ? "Everything is wired up — go send a file over the screen."
      : `${progress.total - progress.done} step(s) to go. The guided setup takes you
         through them one at a time, with the buttons in place.`;
    $("#progress-chip").className = "chip " + (progress.complete ? "ok" : "run");
    $("#progress-chip").textContent = `${progress.percent}% ready`;
  }

  function renderNext(stages, nextIdx, progress) {
    const stage = stages[Math.min(nextIdx, stages.length - 1)];
    const conf = NEXT[stage.id] || NEXT.dataset;
    const done = stages.every((s) => s.done);
    const tips = (stage.checklist || []).filter((c) => !c.done)
      .map((c) => `<li>${c.label}</li>`).join("");
    $("#next").innerHTML =
      `<p style="color:var(--fg);font-weight:600;margin:0 0 4px">${
        done ? "Everything is wired up — go send something" : conf.title}</p>` +
      `<p class="small" style="margin-top:0">${esc(conf.body)}</p>` +
      (tips ? `<ul class="small muted" style="margin:6px 0 10px 18px">${tips}</ul>` : "") +
      `<div class="row" style="gap:8px;flex-wrap:wrap">` +
      `<a class="btn" style="padding:10px 16px;text-decoration:none"
          href="/wizard">Walk me through it →</a>` +
      `<a class="ghost" style="padding:10px 16px;text-decoration:none"
          href="${conf.cta[1]}">${conf.cta[0]}</a></div>`;
    $("#next-why").textContent = done ? ""
      : `step ${nextIdx + 1} of ${stages.length} · ${progress.percent}% ready`;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function renderEnv(env, disk, active) {
    const torch = env.torch
      ? `<span class="chip ok">torch ${env.torch}</span>`
      : `<span class="chip bad">no torch — training unavailable</span>`;
    const ult = env.ultralytics
      ? `<span class="chip ok">ultralytics ${env.ultralytics}</span>`
      : `<span class="chip bad">no ultralytics</span>`;
    const mem = env.mem_total_bytes
      ? `<span class="chip">${fmt.bytes(env.mem_total_bytes)} RAM</span>` : "";
    const free = disk && disk.total_bytes
      ? `<span class="chip">${fmt.bytes(disk.free_bytes)} free</span>` : "";
    $("#env").innerHTML =
      `<div class="row wrap" style="gap:6px;margin-bottom:8px">${torch}${ult}` +
      `<span class="chip">${env.cpus_available || env.cpu_count || "?"} cpus</span>` +
      `${env.cuda ? `<span class="chip ok">CUDA</span>` : `<span class="chip">CPU only</span>`}` +
      `${mem}${free}</div>` +
      `<div>Active model: <b>${active ? active.name : "none"}</b>` +
      `${active && active.metrics && active.metrics.box_map50_95
        ? ` · box mAP50-95 ${fmt.num(active.metrics.box_map50_95, 3)}` : ""}</div>` +
      `<div style="margin-top:4px">Python ${env.python} · OpenCV ${env.opencv || "?"}</div>`;
  }

  function renderJobs(jobs) {
    const host = $("#jobs");
    if (!jobs || !jobs.length) {
      host.innerHTML = `<span class="muted">no jobs yet — dataset generation and ` +
                       `training appear here</span>`;
      return;
    }
    host.innerHTML = "";
    const table = document.createElement("table");
    table.className = "t";
    table.innerHTML = `<thead><tr><th>job</th><th>status</th><th>progress</th>` +
                      `<th class="num">elapsed</th><th></th></tr></thead><tbody></tbody>`;
    const body = table.querySelector("tbody");
    jobs.slice(0, 8).forEach((j) => {
      const tr = document.createElement("tr");
      const pct = Math.round(100 * (j.progress || 0));
      tr.innerHTML =
        `<td><b>${j.title}</b><div class="muted small mono">${j.kind} · ${j.id}</div></td>` +
        `<td></td>` +
        `<td style="min-width:120px"><div class="bar"><i style="width:${pct}%"></i></div>` +
        `<div class="muted small">${pct}% ${j.message || ""}</div></td>` +
        `<td class="num">${fmt.dur(j.elapsed)}</td>` +
        `<td class="num"><a class="chip" href="${jobHref(j)}">open</a></td>`;
      tr.children[1].appendChild(statusChip(j.status));
      body.appendChild(tr);
    });
    host.appendChild(table);
  }

  function jobHref(job) {
    const run = job.meta && job.meta.run;
    const dataset = job.meta && job.meta.dataset;
    if (job.kind === "train" && run) return `/train?job=${job.id}&run=${encodeURIComponent(run)}`;
    if (job.kind === "evaluate" && run) return `/evaluate?job=${job.id}&run=${encodeURIComponent(run)}`;
    if (dataset) return `/dataset?job=${job.id}`;
    return `/train?job=${job.id}`;
  }

  async function load() {
    let state, progress = null;
    try {
      state = await API.get("/api/suite/status");
      try { progress = await API.get("/api/suite/progress"); } catch (e) { progress = null; }
    } catch (e) {
      if (e.status === 401 || /login required|not signed in/.test(e.message || "")) {
        $("#steps").innerHTML = "";
        $("#next").innerHTML =
          `<p style="color:var(--fg)">This suite is password protected.</p>` +
          `<a class="btn" style="display:inline-block;padding:10px 16px;` +
          `text-decoration:none" href="/login?next=%2F">Sign in</a>`;
        $("#env").textContent = "sign in to see this container's capabilities";
        $("#jobs").textContent = "";
        return;
      }
      $("#next").innerHTML = `<div class="callout bad">could not reach the server: ` +
        `${e.message}</div>`;
      return;
    }
    const stages = (progress && progress.stages) || state.stages;
    const nextIdx = renderSteps(stages);
    if (progress) renderPipeline(progress);
    renderNext(stages, nextIdx, progress || { percent: 0 });
    renderEnv(state.env, state.disk, state.active);
    renderJobs(state.jobs);
    $("#health").textContent = "healthy · " + (state.env.cpu_count || "?") + " cpus";
    $("#health").className = "chip ok";

    const images = state.datasets.reduce((a, d) => a + d.images, 0);
    $("#kpi-dataset .v").textContent = `${images} images`;
    $("#kpi-train .v").textContent = `${state.runs.length} runs`;
    $("#kpi-s2c .v").textContent = state.active ? state.active.name : "none yet";

    // keep the dashboard alive while a job runs
    if (state.jobs.some((j) => j.live)) {
      setTimeout(load, 4000);
    }
  }

  chrome("/");
  load();
})();
