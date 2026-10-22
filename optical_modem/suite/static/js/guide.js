/* Guide page: fill in live status chips + the environment grid. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt } = window.SUITE;
  const $ = UI.$;

  function chip(el, ok, text) {
    if (!el) return;
    el.className = "chip " + (ok ? "ok" : "");
    el.textContent = text;
  }

  function envGrid(env, disk, active) {
    const rows = [
      ["Python", env.python],
      ["torch", env.torch || "missing"],
      ["ultralytics", env.ultralytics || "missing"],
      ["OpenCV", env.opencv || "?"],
      ["CPU", `${env.cpus_available || env.cpu_count || "?"} cores`,
       env.cuda ? `CUDA: ${env.gpu || "yes"}` : "no GPU (CPU training)"],
      ["RAM", env.mem_total_bytes ? fmt.bytes(env.mem_total_bytes) : "?"],
      ["Disk free", disk && disk.total_bytes ? fmt.bytes(disk.free_bytes) : "?"],
      ["Active model", active ? active.name : "none"],
    ];
    const table = document.createElement("table");
    table.className = "t";
    rows.forEach(([k, v]) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="muted" style="width:140px">${k}</td><td>${
        Array.isArray(v) ? v.join(" · ") : v}</td>`;
      table.appendChild(tr);
    });
    $("#env-grid").innerHTML = "";
    $("#env-grid").appendChild(table);
  }

  async function load() {
    let state;
    try {
      state = await API.get("/api/suite/status");
    } catch (e) {
      $("#env-grid").textContent = "cannot reach the server: " + e.message;
      return;
    }
    // the richer, project-aware stage list (falls back to the status one)
    let prog = null;
    try { prog = await API.get("/api/suite/progress"); } catch (e) { prog = null; }
    if (prog && window.VIZ) {
      $("#pipeline").innerHTML = window.VIZ.pipeline(prog.stages);
      const note = document.createElement("p");
      note.className = "hint";
      note.innerHTML = prog.complete
        ? "Every stage has run on this box."
        : `Next up: <b>${prog.next.title}</b> — ${prog.next.detail}.`;
      $("#pipeline").appendChild(note);
    } else if ($("#pipeline")) {
      $("#pipeline").textContent = "";
    }
    const byId = Object.fromEntries((prog ? prog.stages : state.stages).map((s) => [s.id, s]));
    chip($("#s-env"), byId.env.done,
         byId.env.done ? "ready" : "torch missing");
    chip($("#s-dataset"), byId.dataset.done, byId.dataset.detail);
    chip($("#s-train"), byId.train.done, byId.train.detail);
    if (byId.project && $("#s-project")) {
      chip($("#s-project"), byId.project.done, byId.project.detail);
    }
    chip($("#s-verify"), (byId.verify || byId.evaluate).done,
         (byId.verify || byId.evaluate).detail);
    chip($("#s-models"), !!state.active,
         state.active ? "active: " + state.active.name : "nothing promoted");
    envGrid(state.env, state.disk, state.active);
  }

  chrome("/guide");
  load();
})();
