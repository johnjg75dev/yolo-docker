/* Models page: registry, activation, deletion, model cards. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const { chrome, fmt, busy, emptyState } = window.SUITE;
  const $ = UI.$;

  async function load() {
    let data;
    try {
      data = await API.get("/api/suite/models");
    } catch (e) {
      $("#models").textContent = "cannot load: " + e.message;
      return;
    }
    const active = data.active;
    $("#active-chip").className = "chip " + (active ? "ok" : "");
    $("#active-chip").textContent = active ? "active: " + active.name : "none active";

    const host = $("#models");
    if (!data.models.length) {
      host.innerHTML = emptyState({
        icon: "check", title: "No model promoted yet",
        body: "The registry holds the model the S2C receiver and the auto-labeller " +
              "use. Train a run below, or the bundled yolo26n_optical stays active.",
        cta: "Go to training", href: "/train" });
    } else {
      const table = document.createElement("table");
      table.className = "t";
      table.innerHTML = `<thead><tr><th>model</th><th class="num">size</th>` +
        `<th class="num">box mAP50-95</th><th class="num">pose mAP50-95</th>` +
        `<th>state</th><th class="num">actions</th></tr></thead><tbody></tbody>`;
      const body = table.querySelector("tbody");
      data.models.forEach((m) => {
        const met = m.metrics || {};
        const tr = document.createElement("tr");
        tr.innerHTML =
          `<td><b>${m.name}</b><div class="muted small">${fmt.ago(m.modified)}` +
          `${m.card && m.card.promoted && m.card.promoted.from_run
            ? ` · from ${m.card.promoted.from_run}` : ""}</div></td>` +
          `<td class="num">${fmt.bytes(m.bytes)}</td>` +
          `<td class="num">${fmt.num(met.box_map50_95, 3)}</td>` +
          `<td class="num">${fmt.num(met.pose_map50_95, 3)}</td>` +
          `<td>${m.active ? `<span class="chip ok">active</span>`
                          : `<span class="chip">standby</span>`}</td>` +
          `<td class="num" style="white-space:nowrap"></td>`;
        const acts = tr.querySelector("td:last-child");
        const card = document.createElement("button");
        card.className = "ghost";
        card.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
        card.textContent = "card";
        card.addEventListener("click", () => showCard(m));
        acts.appendChild(card);
        if (!m.active) {
          const act = document.createElement("button");
          act.className = "ghost";
          act.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
          act.textContent = "activate";
          act.addEventListener("click", async () => {
            busy(act, true, "…");
            try {
              await API.post(`/api/suite/models/${encodeURIComponent(m.name)}/activate`, {});
              UI.toast("active: " + m.name);
              await load();
            } catch (e) { UI.toast(e.message, true); busy(act, false); }
          });
          acts.appendChild(act);
        }
        const del = document.createElement("button");
        del.className = "ghost";
        del.style.cssText = "padding:5px 10px;font-size:.8rem;margin-left:4px";
        del.textContent = "delete";
        del.addEventListener("click", async () => {
          if (!confirm(`Delete model ${m.name}?`)) return;
          try {
            await API.json(`/api/suite/models/${encodeURIComponent(m.name)}`,
                           { method: "DELETE" });
            await load();
          } catch (e) { UI.toast(e.message, true); }
        });
        acts.appendChild(del);
        body.appendChild(tr);
      });
      host.innerHTML = "";
      host.appendChild(table);
    }

    const runsHost = $("#runs");
    const promotable = data.runs.filter((r) => r.has_weights && !r.promoted);
    if (!promotable.length) {
      runsHost.innerHTML = `<div class="callout small">Every trained run is already
        in the registry above. Train another on the <a href="/train">Train page</a>.</div>`;
    } else {
      runsHost.innerHTML = "";
      promotable.forEach((r) => {
        const row = document.createElement("div");
        row.className = "row spread";
        row.style.cssText = "padding:8px 0;border-bottom:1px solid var(--line)";
        row.innerHTML =
          `<div class="grow"><div>${r.name}</div>
             <div class="muted small">pose mAP50-95 ${fmt.num((r.metrics || {}).pose_map50_95, 3)}
             · ${fmt.bytes(r.weights_bytes)} · ${fmt.ago(r.modified)}</div></div>`;
        const btn = document.createElement("button");
        btn.className = "ghost";
        btn.style.cssText = "padding:6px 12px;font-size:.85rem";
        btn.textContent = "promote";
        btn.addEventListener("click", async () => {
          busy(btn, true, "…");
          try {
            await API.post("/api/suite/models/promote", { run: r.name });
            UI.toast("promoted " + r.name);
            await load();
          } catch (e) { UI.toast(e.message, true); busy(btn, false); }
        });
        row.appendChild(btn);
        runsHost.appendChild(row);
      });
    }
  }

  function showCard(m) {
    $("#card-card").style.display = "";
    $("#card-title").textContent = "model card · " + m.name;
    $("#card-json").textContent = JSON.stringify(
      { weights: m.weights, metrics: m.metrics, card: m.card }, null, 2);
  }
  $("#card-close").addEventListener("click", () => {
    $("#card-card").style.display = "none";
  });

  chrome("/models");
  load();
})();
