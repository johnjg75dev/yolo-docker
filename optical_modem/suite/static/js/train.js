/* Train page: start a run, follow its log, list past runs. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt, watchJob, renderLog, busy,
          fillProjects, project, emptyState } = window.SUITE;
  const $ = UI.$;
  const params = new URLSearchParams(location.search);
  let currentJob = null;
  let defaults = null;

  /* --------------------------------------------------- estimates */
  function estimate() {
    const epochs = parseInt($("#epochs").value, 10) || 40;
    const imgsz = parseInt($("#imgsz").value, 10) || 448;
    const ds = defaults && defaults.datasets.find((d) => d.name === $("#dataset").value);
    const n = ds ? ((ds.splits.train || {}).images || 0) : 0;
    if (!n) { $("#estimate").textContent = "pick a dataset"; return; }
    const perImage = 0.35 * (imgsz / 448) ** 2;          // s/image-epoch, CPU yolo26n
    const secs = n * epochs * perImage;
    $("#estimate").textContent =
      `${n} training images × ${epochs} epochs @ ${imgsz}px ≈ ${fmt.dur(secs)} ` +
      `on this box (CPU estimate; a GPU is ~20× faster).`;
  }

  /* --------------------------------------------------- start */
  $("#go").addEventListener("click", async () => {
    const dataset = $("#dataset").value;
    if (!dataset) { UI.toast("choose a dataset", true); return; }
    const btn = $("#go");
    busy(btn, true, "starting…");
    try {
      const res = await API.post("/api/suite/train", {
        dataset,
        project: project(),
        name: $("#name").value.trim() || undefined,
        model: $("#model").value,
        epochs: parseInt($("#epochs").value, 10),
        imgsz: parseInt($("#imgsz").value, 10),
        batch: parseInt($("#batch").value, 10),
        device: $("#device").value.trim() || "cpu",
        workers: parseInt($("#workers").value, 10),
        seed: parseInt($("#seed").value, 10),
      });
      $("#name").value = res.run;
      follow(res.job.id, res.job.title);
      history.replaceState(null, "", `/train?job=${res.job.id}&run=${encodeURIComponent(res.run)}`);
      showCurve(res.run);
    } catch (e) {
      UI.toast(e.message, true);
    } finally { busy(btn, false); }
  });

  /* --------------------------------------------------- follow */
  function follow(jid, title) {
    if (currentJob) currentJob.stop();
    $("#log").textContent = "";
    $("#status").className = "chip run";
    $("#status").textContent = "running";
    $("#cancel").style.display = "";
    currentJob = watchJob(jid, (snap) => {
      const pct = Math.round(100 * (snap.progress || 0));
      $("#bar").style.width = pct + "%";
      renderLog($("#log"), snap.tail);
      const m = snap.metrics || {};
      const bits = [snap.message || "", m.map50_95 != null
        ? `pose mAP50-95 ${fmt.num(m.map50_95, 3)}` : ""].filter(Boolean);
      $("#status").textContent = snap.live ? (bits.join(" · ") || "running") : snap.status;
      $("#status").className = "chip " + (snap.live ? "run"
        : snap.status === "ok" ? "ok" : "bad");
      if (!snap.live) $("#cancel").style.display = "none";
    }, async (snap) => {
      if (snap.status === "ok") {
        UI.toast("training finished — promote it on the Models page");
        await loadRuns();
      } else if (snap.status !== "cancelled") {
        UI.toast("training " + snap.status + " — see the log", true);
      }
    });
  }

  $("#cancel").addEventListener("click", async () => {
    const jid = new URLSearchParams(location.search).get("job");
    if (!jid) return;
    try { await API.post(`/api/jobs/${jid}/cancel`, {}); } catch (e) { UI.toast(e.message, true); }
  });

  /* --------------------------------------------------- listing */
  async function loadRuns() {
    let data;
    try {
      data = await API.get("/api/suite/models");
    } catch (e) {
      $("#runs").textContent = "cannot load runs: " + e.message;
      return;
    }
    $("#model-chip").textContent = data.active
      ? "active: " + data.active.name : "no active model";
    $("#model-chip").className = "chip " + (data.active ? "ok" : "");
    const runs = data.runs || [];
    const host = $("#runs");
    if (!runs.length) {
      host.innerHTML = emptyState({
        icon: "brain", title: "No training runs yet",
        body: "A run is one fine-tune of yolo26n-pose on a dataset. " +
              "40 epochs at 448 px takes ~10–25 min on 4 CPU cores for a few " +
              "hundred images.",
        cta: "Build a dataset first", href: "/dataset" });
      return;
    }
    const table = document.createElement("table");
    table.className = "t";
    table.innerHTML = `<thead><tr><th>run</th><th class="num">pose mAP50-95</th>` +
      `<th class="num">box mAP50-95</th><th class="num">weights</th>` +
      `<th class="num">actions</th></tr></thead><tbody></tbody>`;
    const body = table.querySelector("tbody");
    runs.forEach((r) => {
      const m = r.metrics || {};
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><b>${r.name}</b><div class="muted small">${fmt.ago(r.modified)}</div></td>` +
        `<td class="num">${fmt.num(m.pose_map50_95, 3)}</td>` +
        `<td class="num">${fmt.num(m.box_map50_95, 3)}</td>` +
        `<td class="num">${r.has_weights ? fmt.bytes(r.weights_bytes) : "—"}</td>` +
        `<td class="num" style="white-space:nowrap"></td>`;
      const acts = tr.querySelector("td:last-child");
      const ev = document.createElement("button");
      ev.className = "ghost";
      ev.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
      ev.textContent = "verify";
      ev.addEventListener("click", () => {
        location.href = `/evaluate?run=${encodeURIComponent(r.name)}`;
      });
      const pm = document.createElement("button");
      pm.className = "ghost";
      pm.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
      pm.textContent = r.promoted ? "re-promote" : "promote";
      pm.disabled = !r.has_weights;
      pm.addEventListener("click", async () => {
        busy(pm, true, "…");
        try {
          await API.post("/api/suite/models/promote", { run: r.name });
          UI.toast("promoted " + r.name);
          await loadRuns();
        } catch (e) { UI.toast(e.message, true); busy(pm, false); }
      });
      const open = document.createElement("button");
      open.className = "ghost";
      open.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
      open.textContent = "log";
      open.addEventListener("click", () => {
        const job = (data.runs || []).find(() => false);
        openRunLog(r.name);
      });
      acts.append(ev, pm, open);
      body.appendChild(tr);
    });
    host.innerHTML = "";
    host.appendChild(table);
  }

  async function openRunLog(runName) {
    showCurve(runName);
    // find the newest train job for this run and replay its log
    try {
      const data = await API.get("/api/jobs?kind=train&limit=50");
      const job = (data.jobs || []).find((j) => (j.meta || {}).run === runName);
      if (!job) { UI.toast("no job log for " + runName); return; }
      follow(job.id, job.title);
      history.replaceState(null, "", `/train?job=${job.id}&run=${encodeURIComponent(runName)}`);
    } catch (e) { UI.toast(e.message, true); }
  }

  /* ------------------------------------------------- training curves */
  /* One glance at the shape of a run beats six numbers: is it still learning,
     did it diverge, did it overfit? Drawn from the run's own results.csv. */
  async function showCurve(runName) {
    const card = $("#curve-card");
    if (!card || !runName || !window.VIZ) return;
    let data;
    try {
      data = await API.get(`/api/suite/runs/${encodeURIComponent(runName)}/curve`);
    } catch (e) { card.style.display = "none"; return; }
    if (!data.epochs || data.epochs.length < 2) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";
    $("#curve-run").textContent = "· " + runName;
    const prefs = ["metrics/mAP50-95(B)", "metrics/mAP50(B)", "train/box_loss",
                   "train/pose_loss", "val/box_loss"];
    const ordered = {};
    prefs.forEach((k) => { if (data.series[k]) ordered[k] = data.series[k]; });
    Object.keys(data.series).forEach((k) => {
      if (!ordered[k] && Object.keys(ordered).length < 5) ordered[k] = data.series[k];
    });
    $("#curve").innerHTML = window.VIZ.lineChart(data.epochs, ordered);
    $("#curve-chip").textContent = `${data.epochs.length} epochs`;
    $("#curve-chip").className = "chip";
  }

  /* --------------------------------------------------- boot */
  async function boot() {
    chrome("/train");
    fillProjects($("#project"));
    const bootRun = params.get("run");
    if (bootRun) showCurve(bootRun);
    try {
      defaults = await API.get("/api/suite/train/defaults");
    } catch (e) {
      UI.toast(e.message, true);
      return;
    }
    const sel = $("#dataset");
    sel.innerHTML = "";
    const usable = defaults.datasets.filter((d) => d.yaml && d.images);
    if (!usable.length) {
      sel.innerHTML = `<option value="">no dataset with data.yaml — build one first</option>`;
    }
    usable.forEach((d) => {
      const o = document.createElement("option");
      o.value = d.name;
      const tr = (d.splits.train || {}).images || 0;
      const va = (d.splits.val || {}).images || 0;
      o.textContent = `${d.name}  (${tr} train / ${va} val)`;
      sel.appendChild(o);
    });
    const want = params.get("dataset");
    if (want && usable.some((d) => d.name === want)) sel.value = want;

    // base weights: a bundled frame detector (no download, converges fast) or
    // a stock ultralytics checkpoint (auto-downloaded on first use)
    const msel = $("#model");
    msel.innerHTML = "";
    (defaults.bundled_bases || []).forEach((b) => {
      msel.appendChild(new Option(b.name + " — recommended", b.path));
    });
    (defaults.base_models || []).forEach((m) => msel.appendChild(new Option(m, m)));
    if (!msel.options.length) msel.appendChild(new Option("yolo26n-pose.pt", "yolo26n-pose.pt"));

    const s = defaults.suggested;
    $("#epochs").value = s.epochs;
    $("#imgsz").value = s.imgsz;
    $("#batch").value = s.batch;
    $("#device").value = s.device;
    $("#workers").value = s.workers;
    if (Array.from(msel.options).some((o) => o.value === s.model)) msel.value = s.model;
    ["#epochs", "#imgsz", "#batch", "#dataset"].forEach((k) =>
      $(k).addEventListener("change", estimate));
    estimate();

    if (!defaults.env.torch) {
      $("#log").innerHTML =
        `<span class="err">torch is not installed in this container — training ` +
        `will fail. Use the full image (requirements.txt), not the capture-only one.</span>`;
    }
    await loadRuns();
    if (params.get("job")) follow(params.get("job"));
  }

  boot();
})();
