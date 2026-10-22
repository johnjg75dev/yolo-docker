/* Projects page: the durable state view (database-backed).
 *
 * Reads /api/suite/state (one call: counts, projects, artefacts, events) and
 * writes through /api/suite/projects.  Job/project events arrive live over SSE
 * (/api/events) and fall back to polling if EventSource is unavailable.
 */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const $ = (sel) => document.querySelector(sel);
  const fmt = window.SUITE.fmt;

  let projects = [];

  /* ------------------------------------------------------------- loading */
  async function loadState() {
    const st = await API.get("/api/suite/state");
    const db = st.database || {};
    $("#db-chip").className = "chip " + (st.enabled ? "ok" : "bad");
    $("#db-chip").textContent = st.enabled ? `db: ${db.kind}` : "db: off";
    $("#db-kind").textContent = st.enabled ? db.url : "disabled (SUITE_DB=0)";
    const bus = st.bus || {};
    $("#bus-chip").className = "chip " + (bus.kind === "redis" ? "ok" : "");
    $("#bus-chip").textContent = `bus: ${bus.kind || "?"}`;
    $("#bus-chip").title = JSON.stringify(bus);

    projects = st.projects || [];
    renderProjects();
    renderCounts(st);
    renderOverview(st);
    renderEvents(st.events || []);
  }

  function renderProjects() {
    const filter = $("#p-filter").value;
    const rows = projects.filter((p) => !filter || p.status === filter);
    const host = $("#projects");
    if (!rows.length) {
      host.innerHTML = `<p>no projects yet — create one above, or pass
        <code>"project": "slug"</code> to the dataset/train endpoints.</p>`;
      return;
    }
    host.innerHTML = rows.map((p) => `
      <div class="row spread" style="align-items:flex-start;gap:10px;padding:8px 0;border-top:1px solid #ffffff12">
        <div style="min-width:0">
          <div><b>${esc(p.title || p.slug)}</b>
            <span class="chip ${p.status === "finished" ? "ok" : ""}">${p.status}</span>
            ${p.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
          <div class="small muted">${esc(p.slug)} · ${p.n_datasets} dataset(s) ·
            ${p.n_runs} run(s) · updated ${fmt.when(p.updated)}</div>
          ${p.description ? `<div class="small">${esc(p.description)}</div>` : ""}
        </div>
        <div class="row" style="gap:6px;flex-wrap:nowrap">
          <select data-slug="${esc(p.slug)}" class="p-status-sel">
            ${["development", "finished", "archived"].map((s) =>
              `<option value="${s}" ${s === p.status ? "selected" : ""}>${s}</option>`).join("")}
          </select>
          <button class="ghost" data-slug="${esc(p.slug)}" data-act="detail">detail</button>
          <button class="ghost" data-slug="${esc(p.slug)}" data-act="delete">delete</button>
        </div>
      </div>`).join("");

    host.querySelectorAll(".p-status-sel").forEach((sel) => {
      sel.addEventListener("change", async (ev) => {
        const slug = ev.target.dataset.slug;
        try {
          await API.patch(`/api/suite/projects/${encodeURIComponent(slug)}`,
                          { status: ev.target.value });
          UI.toast(`${slug} → ${ev.target.value}`);
          await loadState();
        } catch (e) { UI.toast(e.message, true); }
      });
    });
    host.querySelectorAll('button[data-act="detail"]').forEach((btn) => {
      btn.addEventListener("click", () => showDetail(btn.dataset.slug));
    });
    host.querySelectorAll('button[data-act="delete"]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Delete project ${btn.dataset.slug}? Its datasets and runs are
kept, but they will no longer belong to a project.`)) return;
        try {
          await API.del(`/api/suite/projects/${encodeURIComponent(btn.dataset.slug)}`);
          UI.toast("project deleted");
          await loadState();
        } catch (e) { UI.toast(e.message, true); }
      });
    });
  }

  async function showDetail(slug) {
    const d = await API.get(`/api/suite/projects/${encodeURIComponent(slug)}`);
    const p = d.project;
    const lines = [
      `<b>${esc(p.title)}</b> — ${p.status}`,
      `datasets: ${(d.datasets || []).map((x) => `${esc(x.name)} (${x.images})`).join(", ") || "none"}`,
      `runs: ${(d.runs || []).map((x) => `${esc(x.name)} [${x.status}]`).join(", ") || "none"}`,
      `transfers: ${(d.transfers || []).length} · jobs: ${(d.jobs || []).length}`,
    ];
    $("#overview").innerHTML = lines.join("<br>");
    $("#overview").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderCounts(st) {
    const c = st.counts || {};
    $("#counts").textContent = Object.entries(c)
      .map(([k, v]) => `${k}: ${v}`).join(" · ") || "no database";
  }

  function renderOverview(st) {
    if (!st.enabled) {
      $("#overview").innerHTML = `<p>The database is disabled
        (<code>SUITE_DB=0</code>). Artefacts still work — they live on the volume —
        but nothing outside the filesystem is recorded.</p>`;
      return;
    }
    const dev = (st.projects || []).filter((p) => p.status === "development");
    const done = (st.projects || []).filter((p) => p.status === "finished");
    $("#overview").innerHTML = `
      <div><b>${dev.length}</b> in development · <b>${done.length}</b> finished</div>
      <div class="small muted" style="margin-top:6px">
        ${(st.datasets || []).length} datasets · ${(st.runs || []).length} runs ·
        ${(st.models || []).length} models ·
        active model <b>${esc((st.active || {}).name || "bundled")}</b></div>`;
  }

  function renderEvents(events) {
    const host = $("#events");
    if (!events.length) { host.innerHTML = `<p class="muted">none yet</p>`; return; }
    host.innerHTML = events.map((e) => `
      <div class="row" style="gap:8px;padding:3px 0">
        <span class="muted" style="min-width:78px">${fmt.when(e.ts)}</span>
        <span class="chip">${esc(e.kind)}</span>
        <span>${esc(e.subject || (e.job && e.job.id) || "")}</span>
      </div>`).join("");
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  /* ------------------------------------------------------------- create */
  $("#p-create").addEventListener("click", async () => {
    const body = {
      slug: $("#p-slug").value.trim(),
      title: $("#p-title").value.trim(),
      description: $("#p-desc").value.trim(),
      status: $("#p-status").value,
      tags: $("#p-tags").value.split(",").map((t) => t.trim()).filter(Boolean),
    };
    if (!body.slug) { UI.toast("give the project a name", true); return; }
    try {
      await API.post("/api/suite/projects", body);
      UI.toast(`project ${body.slug} created`);
      $("#p-slug").value = $("#p-title").value = $("#p-desc").value = $("#p-tags").value = "";
      await loadState();
    } catch (e) { UI.toast(e.message, true); }
  });

  $("#p-filter").addEventListener("change", renderProjects);
  $("#p-refresh").addEventListener("click", () => loadState());

  /* --------------------------------------------------------------- live */
  function listen() {
    if (!window.EventSource) return;
    const es = new EventSource("/api/events");
    ["project.created", "project.updated", "dataset.upserted", "run.upserted",
     "model.promoted", "job.finished", "job.failed"].forEach((name) => {
      es.addEventListener(name, () => loadState());
    });
    es.onerror = () => { /* browser retries; polling below still works */ };
  }

  loadState().then(listen).catch((e) => UI.toast(e.message, true));
})();
