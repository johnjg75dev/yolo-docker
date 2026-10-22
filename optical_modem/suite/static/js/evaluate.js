/* Verify page: run scripts/evaluate.py as a job, show the report, loopback. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt, watchJob, renderLog, busy } = window.SUITE;
  const $ = UI.$;
  const params = new URLSearchParams(location.search);
  let activeRun = params.get("run") || "";
  let lastTransfer = null;

  function pct(x) { return x == null ? "–" : (100 * x).toFixed(0) + "%"; }

  async function loadRuns() {
    let data;
    try {
      data = await API.get("/api/suite/models");
    } catch (e) { UI.toast(e.message, true); return; }
    const runs = (data.runs || []).filter((r) => r.has_weights);
    const sel = $("#run");
    sel.innerHTML = "";
    if (!runs.length) {
      sel.innerHTML = `<option value="">no trained run yet — go to Train</option>`;
      return;
    }
    runs.forEach((r) => {
      const o = document.createElement("option");
      o.value = r.name;
      o.textContent = `${r.name}  (pose mAP50-95 ${
        r.metrics && r.metrics.pose_map50_95 != null
          ? fmt.num(r.metrics.pose_map50_95, 3) : "–"})`;
      sel.appendChild(o);
    });
    if (activeRun && runs.some((r) => r.name === activeRun)) sel.value = activeRun;
    else activeRun = sel.value;
    await loadReport(activeRun);
  }

  async function loadReport(run) {
    if (!run) return;
    try {
      const rep = await API.get(`/api/suite/runs/${encodeURIComponent(run)}/eval`);
      renderReport(run, rep);
    } catch (e) {
      $("#report-card").style.display = "none";
    }
  }

  function renderReport(run, rep) {
    $("#report-card").style.display = "";
    $("#report-run").textContent = run;
    const levels = Object.keys(rep.levels || {});
    if (!levels.length) {
      $("#report").innerHTML = `<div class="callout warn">report has no levels — rerun the evaluation</div>`;
      return;
    }
    const modes = ["classical", "yolo", "auto"];
    let html = `<table class="t"><thead><tr><th>level</th><th>mode</th>` +
               `<th class="num">corner err</th><th class="num">p90</th>` +
               `<th class="num">detected</th><th class="num">decoded</th>` +
               `<th class="num">ms/frame</th></tr></thead><tbody>`;
    levels.forEach((lv) => {
      const entry = rep.levels[lv];
      modes.forEach((m) => {
        const e = entry[m];
        if (!e) return;
        const cls = e.decode_rate >= 0.9 ? "ok" : e.decode_rate >= 0.5 ? "" : "bad";
        html += `<tr><td>${lv}</td><td>${m}</td>` +
          `<td class="num">${fmt.num(e.corner_err_median, 4)}</td>` +
          `<td class="num">${fmt.num(e.corner_err_p90, 4)}</td>` +
          `<td class="num">${pct(e.detected)}</td>` +
          `<td class="num"><span class="chip ${cls}">${pct(e.decode_rate)}</span></td>` +
          `<td class="num">${Math.round(e.ms_per_frame || 0)}</td></tr>`;
      });
    });
    html += `</tbody></table>`;

    // the same numbers as bars: decode rate by channel level is the one shape
    // that tells you whether the model is usable on a real phone
    if (window.VIZ) {
      const bars = levels.map((lv) => {
        const e = (rep.levels[lv] || {}).auto || (rep.levels[lv] || {}).yolo;
        if (!e) return null;
        return { label: "level " + lv, value: e.decode_rate || 0,
                 text: pct(e.decode_rate), target: 0.9 };
      }).filter(Boolean);
      if (bars.length > 1) {
        html += `<h3 style="margin:16px 0 6px">Decode rate by channel level</h3>` +
                window.VIZ.bars(bars);
      }
    }
    if (rep.weights) {
      html += `<p class="hint mono">${rep.weights}</p>`;
    }
    $("#report").innerHTML = html;
  }

  /* ------------------------------------------------------------ evaluate */
  $("#go").addEventListener("click", async () => {
    const run = $("#run").value;
    if (!run) { UI.toast("no run selected", true); return; }
    const btn = $("#go");
    busy(btn, true, "starting…");
    try {
      const res = await API.post(`/api/suite/runs/${encodeURIComponent(run)}/evaluate`, {
        n: parseInt($("#n").value, 10) || 12,
        level: parseFloat($("#level").value) || 0.6,
      });
      $("#job-card").style.display = "";
      watchJob(res.job.id, (snap) => {
        $("#job-status").className = "chip " + (snap.live ? "run" : snap.status === "ok" ? "ok" : "bad");
        $("#job-status").textContent = snap.live
          ? `${Math.round(100 * snap.progress)}% ${snap.message || ""}` : snap.status;
        $("#job-bar").style.width = Math.round(100 * (snap.progress || 0)) + "%";
        renderLog($("#job-log"), snap.tail);
      }, async (snap) => {
        renderLog($("#job-log"), snap.tail);
        if (snap.status === "ok") {
          UI.toast("evaluation done");
          await loadReport(run);
        } else UI.toast("evaluation " + snap.status, true);
      });
    } catch (e) {
      UI.toast(e.message, true);
    } finally { busy(btn, false); }
  });

  $("#promote").addEventListener("click", async () => {
    const btn = $("#promote");
    busy(btn, true, "promoting…");
    try {
      await API.post("/api/suite/models/promote", { run: $("#run").value });
      UI.toast("promoted — S2C will use it now");
    } catch (e) { UI.toast(e.message, true); }
    finally { busy(btn, false); }
  });

  /* ------------------------------------------------------------ loopback */
  $("#lb-go").addEventListener("click", async () => {
    const btn = $("#lb-go");
    busy(btn, true, "running…");
    $("#lb-out").textContent = "creating a transfer…";
    try {
      const tx = await API.post("/api/suite/s2c/transmit", {
        text: "loopback check — the quick brown fox jumps over the lazy dog. " +
              "光学调制解调器 loopback verification payload.",
        name: "loopback.txt",
        repeat: 1,
        fps: 2,
      });
      lastTransfer = tx.transfer.id;
      $("#lb-out").textContent = "decoding with the active model…";
      const rep = await API.post(
        `/api/suite/s2c/${lastTransfer}/loopback`,
        { frames: parseInt($("#lb-frames").value, 10) || 3,
          level: parseFloat($("#lb-level").value) || 0.35 });
      const rows = rep.frames.map((f) =>
        `<tr><td>${f.frame}</td><td>${f.mode}</td>` +
        `<td>${f.ok ? `<span class="chip ok">ok</span>` : `<span class="chip bad">no</span>`}</td>` +
        `<td class="num">${f.payload_matches ? "✓" : "✗"}</td>` +
        `<td class="num">${fmt.num(f.corner_err, 3)}</td>` +
        `<td class="num">${f.ms} ms</td>` +
        `<td class="muted small">${f.reason || ""}</td></tr>`).join("");
      $("#lb-out").innerHTML =
        `<div class="row wrap" style="gap:6px;margin-bottom:8px">
           <span class="chip ${rep.decoded === rep.frames.length ? "ok" : "bad"}">
             ${rep.decoded}/${rep.frames.length} frames decoded</span>
           <span class="chip">median corner err ${fmt.num(rep.corner_err_median, 3)}</span>
           <span class="chip ${rep.sha1_ok ? "ok" : ""}">
             ${rep.sha1_ok ? "payload byte-identical" : "payload not reassembled"}</span>
         </div>
         <table class="t"><thead><tr><th>#</th><th>mode</th><th>decode</th>
         <th class="num">payload</th><th class="num">corner err</th>
         <th class="num">time</th><th>note</th></tr></thead><tbody>${rows}</tbody></table>`;
      if (rep.decoded === rep.frames.length) UI.toast("loopback clean");
    } catch (e) {
      $("#lb-out").innerHTML = `<div class="callout bad">${e.message}</div>`;
    } finally { busy(btn, false); }
  });

  /* ------------------------------------------------------------ boot */
  chrome("/evaluate");
  $("#run").addEventListener("change", () => {
    activeRun = $("#run").value;
    loadReport(activeRun);
  });
  loadRuns();
})();
