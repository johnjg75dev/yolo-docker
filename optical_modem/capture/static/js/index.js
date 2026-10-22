/* Landing page: login, role choice, resume an existing session. */
(function () {
  "use strict";
  const { API, UI } = window.OM;
  const $ = UI.$;

  const loginPanel = $("#login-panel");
  const rolePanel = $("#role-panel");

  function fmtAge(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }

  async function showSessions() {
    const list = $("#session-list");
    let data;
    try {
      data = await API.get("/api/sessions");
    } catch (e) {
      list.textContent = "could not load sessions: " + e.message;
      return;
    }
    const sessions = (data.sessions || []).slice(0, 8);
    if (!sessions.length) {
      list.innerHTML = '<span class="muted">none yet — start one from the display device</span>';
      return;
    }
    list.innerHTML = "";
    sessions.forEach((s) => {
      const row = document.createElement("div");
      row.className = "row spread";
      row.style.cssText = "padding:8px 0;border-bottom:1px solid var(--line)";
      const done = s.index >= s.count;
      row.innerHTML =
        `<div class="grow"><div style="color:var(--fg)">${s.label || s.id}</div>` +
        `<div class="muted small">${s.accepted}/${s.count} shots · ${s.resolution} · ${fmtAge(s.created)}</div></div>`;
      const a = document.createElement("a");
      a.className = "btn ghost";
      a.style.cssText = "padding:8px 12px;font-size:.85rem";
      a.href = done ? `/display?session=${encodeURIComponent(s.id)}`
                    : `/display?session=${encodeURIComponent(s.id)}`;
      a.textContent = done ? "Review" : "Open";
      row.appendChild(a);
      list.appendChild(row);
    });
  }

  async function boot() {
    let me;
    try {
      me = await API.get("/api/me");
    } catch (e) {
      me = null;
    }
    if (!me || !me.authenticated) {
      loginPanel.classList.remove("hidden");
      rolePanel.classList.add("hidden");
      try {
        const caps = await API.json("/api/capabilities", { method: "GET" })
          .catch(() => null);
        if (caps && !caps.password_set) {
          $("#login-note").textContent =
            "No APP_PASSWORD is set on the server, so any password works. Set " +
            "APP_PASSWORD in the container environment before exposing this on " +
            "the internet.";
        }
      } catch (e) {}
      const next = new URLSearchParams(location.search).get("next");
      if (next) $("#login-note").textContent = "Sign in to continue to " + next;
      return;
    }
    loginPanel.classList.add("hidden");
    rolePanel.classList.remove("hidden");
    $("#who").innerHTML = `<span class="dot"></span> signed in`;
    showSessions();
    fillResolutions();
  }

  async function fillResolutions() {
    try {
      const caps = await API.get("/api/capabilities");
      const sel = $("#new-resolution");
      const current = caps.defaults.resolution;
      sel.innerHTML = "";
      (caps.displays || []).forEach((d) => {
        const o = document.createElement("option");
        o.value = d.label;
        o.textContent = `${d.label}  (${d.width}×${d.height})`;
        o.selected = d.label === current;
        sel.appendChild(o);
      });
    } catch (e) { /* keep the empty select */ }
  }

  $("#new-session").addEventListener("click", async () => {
    const btn = $("#new-session");
    btn.disabled = true;
    try {
      const snap = await API.post("/api/sessions", {
        count: parseInt($("#new-count").value, 10) || 100,
        resolution: $("#new-resolution").value || undefined,
      });
      location.href = `/display?session=${encodeURIComponent(snap.id)}`;
    } catch (e) {
      UI.toast(e.message || "could not start a session", true);
      btn.disabled = false;
    }
  });

  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector("button");
    btn.disabled = true;
    try {
      await API.post("/api/login", { password: $("#password").value });
      const next = new URLSearchParams(location.search).get("next");
      if (next && next.startsWith("/")) { location.href = next; return; }
      await boot();
    } catch (e) {
      UI.toast(e.message || "login failed", true);
      btn.disabled = false;
    }
  });

  $("#logout").addEventListener("click", async () => {
    await API.post("/api/logout", {});
    location.reload();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  boot();
})();
