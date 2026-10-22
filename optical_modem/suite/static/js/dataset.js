/* Dataset page: synthetic generation, capture conversion, merge, inspection. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt, watchJob, statusChip, renderLog, busy,
          fillProjects, project, emptyState } = window.SUITE;
  const $ = UI.$;

  const params = new URLSearchParams(location.search);
  let datasets = [];
  let preview = { name: null, split: "train", index: 0 };

  /* --------------------------------------------------------- helpers */
  function jobPanel(title) {
    $("#job-card").style.display = "";
    $("#job-title").textContent = title;
    $("#job-status").className = "chip run";
    $("#job-status").textContent = "starting";
    $("#job-log").textContent = "";
  }

  function trackJob(res, title) {
    jobPanel(title || res.job.title);
    const bar = $("#job-bar");
    watchJob(res.job.id, (snap) => {
      const pct = Math.round(100 * (snap.progress || 0));
      bar.style.width = pct + "%";
      $("#job-status").className = "chip " +
        (snap.live ? "run" : snap.status === "ok" ? "ok" : "bad");
      $("#job-status").textContent = snap.live
        ? `${pct}% ${snap.message || ""}`.trim() : snap.status;
      renderLog($("#job-log"), snap.tail);
    }, async (snap) => {
      bar.style.width = snap.status === "ok" ? "100%" : bar.style.width;
      renderLog($("#job-log"), snap.tail);
      if (snap.status === "ok") {
        UI.toast("job finished");
        await loadDatasets();
      } else if (snap.status !== "ok") {
        UI.toast("job " + snap.status + " — see the log", true);
      }
    });
  }

  /* --------------------------------------------------------- actions */
  $("#syn-go").addEventListener("click", async () => {
    const btn = $("#syn-go");
    busy(btn, true, "starting…");
    try {
      const res = await API.post("/api/suite/datasets/synth", {
        name: $("#syn-name").value.trim() || undefined,
        project: project(),
        train: parseInt($("#syn-train").value, 10),
        val: parseInt($("#syn-val").value, 10),
        seed: parseInt($("#syn-seed").value, 10) || 1,
        level: parseFloat($("#syn-level").value) || 1.0,
      });
      $("#syn-name").value = res.dataset;
      trackJob(res);
    } catch (e) {
      UI.toast(e.message, true);
    } finally { busy(btn, false); }
  });

  $("#cap-go").addEventListener("click", async () => {
    const btn = $("#cap-go");
    busy(btn, true, "starting…");
    try {
      const res = await API.post("/api/suite/datasets/captures", {
        name: $("#cap-name").value.trim() || undefined,
        project: project(),
        val_frac: parseFloat($("#cap-val").value) || 0,
        verified_only: $("#cap-verified").value === "1",
      });
      $("#cap-name").value = res.dataset;
      trackJob(res);
    } catch (e) {
      UI.toast(e.message, true);
    } finally { busy(btn, false); }
  });

  $("#merge-go").addEventListener("click", async () => {
    const sources = Array.from(document.querySelectorAll(".merge-src:checked"))
      .map((el) => el.value);
    if (!sources.length) { UI.toast("tick at least one dataset", true); return; }
    const btn = $("#merge-go");
    busy(btn, true, "starting…");
    try {
      const res = await API.post("/api/suite/datasets/merge", {
        name: $("#merge-name").value.trim() || undefined,
        project: project(),
        sources,
      });
      $("#merge-name").value = res.dataset;
      trackJob(res);
    } catch (e) {
      UI.toast(e.message, true);
    } finally { busy(btn, false); }
  });

  /* --------------------------------------------------------- listing */
  async function loadDatasets() {
    let data;
    try {
      data = await API.get("/api/suite/datasets");
    } catch (e) {
      $("#ds-list").textContent = "cannot load: " + e.message;
      return;
    }
    datasets = data.datasets;
    const total = datasets.reduce((a, d) => a + d.images, 0);
    $("#ds-total").textContent = `${datasets.length} datasets · ${total} images`;

    // merge checkboxes
    const host = $("#merge-sources");
    host.innerHTML = "";
    if (!datasets.length) {
      host.innerHTML = emptyState({
        icon: "grid", title: "No datasets yet",
        body: "Start with synthetic frames — they are instant, cover the geometry, " +
              "and 120 of them train a usable corner detector on CPU.",
        cta: "Generate one above", href: "#syn-go" });
    }
    datasets.forEach((d) => {
      const label = document.createElement("label");
      label.className = "row";
      label.style.cssText = "gap:8px;padding:3px 0";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "merge-src";
      cb.value = d.name;
      cb.checked = true;
      label.appendChild(cb);
      const span = document.createElement("span");
      span.className = "grow";
      span.textContent = `${d.name} (${d.images})`;
      label.appendChild(span);
      host.appendChild(label);
    });

    const list = $("#ds-list");
    if (!datasets.length) {
      list.innerHTML = `<span class="muted">nothing yet — generate synthetic ` +
                       `frames or convert captured photos</span>`;
      return;
    }
    const table = document.createElement("table");
    table.className = "t";
    table.innerHTML = `<thead><tr><th>dataset</th><th class="num">train</th>` +
      `<th class="num">val</th><th class="num">size</th><th>labels</th>` +
      `<th class="num">actions</th></tr></thead><tbody></tbody>`;
    const body = table.querySelector("tbody");
    datasets.forEach((d) => {
      const tr = document.createElement("tr");
      const tr_n = (d.splits.train || {}).images || 0;
      const va_n = (d.splits.val || {}).images || 0;
      const bad = d.labels_bad || 0;
      tr.innerHTML =
        `<td><b>${d.name}</b><div class="muted small">${fmt.ago(d.modified)}</div></td>` +
        `<td class="num">${tr_n}</td><td class="num">${va_n}</td>` +
        `<td class="num">${fmt.bytes(d.bytes)}</td>` +
        `<td>${d.yaml ? `<span class="chip ok">yaml</span>` : `<span class="chip bad">no yaml</span>`}` +
        `${bad ? ` <span class="chip bad">${bad} bad</span>` : ""}</td>` +
        `<td class="num" style="white-space:nowrap"></td>`;
      const acts = tr.querySelector("td:last-child");
      const pv = document.createElement("button");
      pv.className = "ghost";
      pv.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
      pv.textContent = "preview";
      pv.addEventListener("click", () => openPreview(d.name));
      const tr_btn = document.createElement("a");
      tr_btn.className = "chip";
      tr_btn.href = `/train?dataset=${encodeURIComponent(d.name)}`;
      tr_btn.textContent = "train";
      tr_btn.style.marginLeft = "4px";
      const del = document.createElement("button");
      del.className = "ghost";
      del.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
      del.textContent = "delete";
      del.addEventListener("click", async () => {
        if (!confirm(`Delete dataset ${d.name}? The files are removed.`)) return;
        try {
          await API.json(`/api/suite/datasets/${d.name}`, { method: "DELETE" });
          await loadDatasets();
        } catch (e) { UI.toast(e.message, true); }
      });
      acts.append(pv, tr_btn, del);
      body.appendChild(tr);
    });
    list.innerHTML = "";
    list.appendChild(table);
  }

  function openPreview(name) {
    preview = { name, split: "train", index: 0 };
    $("#preview-card").style.display = "";
    $("#pv-title").textContent = "preview · " + name;
    refreshPreview();
    $("#preview-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function refreshPreview() {
    if (!preview.name) return;
    const img = $("#pv-img");
    img.src = `/api/suite/datasets/${preview.name}/preview?split=${preview.split}` +
              `&index=${preview.index}&t=${Date.now()}`;
  }

  $("#pv-prev").addEventListener("click", () => {
    preview.index = Math.max(0, preview.index - 1);
    refreshPreview();
  });
  $("#pv-next").addEventListener("click", () => {
    preview.index += 1;
    refreshPreview();
  });
  $("#pv-close").addEventListener("click", () => {
    $("#preview-card").style.display = "none";
    preview.name = null;
  });

  async function captureStats() {
    try {
      const cap = await API.get("/api/sessions");
      const list = cap.sessions || [];
      const shots = list.reduce((a, s) => a + (s.accepted || 0), 0);
      $("#cap-stats").innerHTML =
        `<span class="chip">${list.length} sessions</span> ` +
        `<span class="chip ${shots ? "ok" : ""}">${shots} accepted shots</span>`;
    } catch (e) {
      $("#cap-stats").textContent = "cannot read sessions: " + e.message;
    }
  }

  /* --------------------------------------------------------- boot */
  chrome("/dataset");
  fillProjects($("#project"));
  loadDatasets();
  captureStats();
  if (params.get("job")) {
    $("#job-card").style.display = "";
    $("#job-title").textContent = "job " + params.get("job");
    watchJob(params.get("job"), (snap) => {
      $("#job-status").className = "chip " + (snap.live ? "run" : snap.status === "ok" ? "ok" : "bad");
      $("#job-status").textContent = snap.live ? `${Math.round(100 * snap.progress)}%` : snap.status;
      $("#job-bar").style.width = Math.round(100 * (snap.progress || 0)) + "%";
      renderLog($("#job-log"), snap.tail);
    }, () => loadDatasets());
  }
})();
