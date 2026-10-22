/* Shared suite chrome: nav, formatting, job polling, small components.
 *
 * Loaded after the capture station's common.js, whose OM.API / OM.UI helpers
 * this file reuses (same cookie auth, same toasts).
 */
(function () {
  "use strict";
  const { API, UI } = window.OM;

  const PAGES = [
    ["/", "Overview"],
    ["/wizard", "Setup"],
    ["/projects", "Projects"],
    ["/guide", "Guide"],
    ["/dataset", "Dataset"],
    ["/train", "Train"],
    ["/evaluate", "Verify"],
    ["/models", "Models"],
    ["/s2c", "S2C"],
  ];

  const fmt = {
    bytes(n) {
      if (n == null) return "–";
      const u = ["B", "kB", "MB", "GB", "TB"];
      let i = 0, v = n;
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
    },
    when(ts) {
      if (!ts) return "–";
      const d = new Date(Number(ts) * 1000);
      return d.toLocaleString(undefined, { month: "short", day: "numeric",
                                           hour: "2-digit", minute: "2-digit" });
    },
    ago(ts) {
      if (!ts) return "–";
      const s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 90) return `${Math.round(s)}s ago`;
      if (s < 5400) return `${Math.round(s / 60)}m ago`;
      if (s < 172800) return `${Math.round(s / 3600)}h ago`;
      return `${Math.round(s / 86400)}d ago`;
    },
    dur(s) {
      if (s == null) return "–";
      if (s < 90) return `${Math.round(s)}s`;
      const m = Math.floor(s / 60);
      if (m < 90) return `${m}m ${Math.round(s % 60)}s`;
      return `${Math.floor(m / 60)}h ${m % 60}m`;
    },
    pct(x) {
      if (x == null || Number.isNaN(x)) return "–";
      return `${(100 * x).toFixed(1)}%`;
    },
    num(x, d = 3) {
      if (x == null || Number.isNaN(x)) return "–";
      return Number(x).toFixed(d);
    },
  };

  /* ---------------------------------------------------------------- nav */
  function chrome(active) {
    const bar = document.querySelector(".top");
    if (!bar) return;
    const brand = bar.querySelector(".brand");
    if (brand) brand.innerHTML = 'Screen<b>2</b>Camera <span class="muted small">suite</span>';
    const nav = bar.querySelector(".nav");
    if (nav) {
      nav.innerHTML = "";
      PAGES.forEach(([href, label]) => {
        const a = document.createElement("a");
        a.href = href;
        a.textContent = label;
        if (href === active) a.classList.add("on");
        nav.appendChild(a);
      });
      const cap = document.createElement("a");
      cap.href = "/display";
      cap.textContent = "Capture →";
      cap.title = "capture station: show codes on the laptop, photograph them with the phone";
      nav.appendChild(cap);
      markNav(nav);
    }
  }

  /* The nav doubles as a progress indicator: once the server says which stages
     are done, the matching links get a tick and "Setup" says how far along the
     box is. Cheap (one request, cached) and it is the only always-visible hint
     that there is a next step. */
  const STAGE_PAGE = { env: "/guide", project: "/projects", dataset: "/dataset",
                       train: "/train", verify: "/models", send: "/s2c" };
  let _progress = null;

  async function markNav(nav) {
    try {
      _progress = _progress || await API.get("/api/suite/progress");
    } catch (e) { return; }
    const donePages = new Set((_progress.stages || [])
      .filter((st) => st.done).map((st) => STAGE_PAGE[st.id]).filter(Boolean));
    nav.querySelectorAll("a").forEach((a) => {
      if (donePages.has(a.getAttribute("href"))) a.classList.add("done");
    });
    const setup = nav.querySelector('a[href="/wizard"]');
    if (setup) {
      const left = (_progress.stages || []).filter((st) => !st.done).length;
      setup.textContent = left ? `Setup (${left})` : "Setup ✓";
      if (!left) setup.classList.add("done");
      setup.title = left
        ? `${left} step(s) left — guided setup walks you through them`
        : "all steps done";
    }
  }

  /** Cached `/api/suite/progress` (pass ``true`` to refetch). */
  async function progress(refresh = false) {
    if (refresh || !_progress) _progress = await API.get("/api/suite/progress");
    return _progress;
  }

  /* ---------------------------------------------------------- empty state */
  /** A dead end nobody likes: say what is missing and give one way out. */
  function emptyState({ title, body, cta, href, icon = "info" }) {
    const art = window.VIZ ? window.VIZ.icon(icon, 34) : "";
    return `<div class="empty">
      <span style="color:var(--muted)">${art}</span>
      <div>
        <div class="e-t">${title}</div>
        <div class="e-b">${body}</div>
        ${cta ? `<a class="chip" href="${href}">${cta} →</a>` : ""}
      </div>
    </div>`;
  }

  /* ------------------------------------------------------------- polling */
  /** Poll `fn` every `ms`, stopping when `done(value)` is true or on error. */
  function poll(fn, ms, done) {
    let stopped = false;
    let timer = null;
    const tick = async () => {
      if (stopped) return;
      let value = null;
      try {
        value = await fn();
      } catch (e) {
        if (e && e.status === 404) { stopped = true; return; }
      }
      if (stopped) return;
      if (value !== null && done && done(value)) return;
      timer = setTimeout(tick, ms);
    };
    tick();
    return {
      stop() { stopped = true; clearTimeout(timer); },
    };
  }

  /** Watch a job until it ends, updating `onUpdate(snapshot)` each poll. */
  function watchJob(jid, onUpdate, onEnd, ms = 1200) {
    return poll(async () => API.get(`/api/jobs/${jid}?lines=400`), ms, (snap) => {
      if (onUpdate) onUpdate(snap);
      if (!snap.live) {
        if (onEnd) onEnd(snap);
        return true;
      }
      return false;
    });
  }

  function statusChip(status) {
    const cls = status === "ok" ? "ok"
      : status === "running" || status === "queued" ? "run"
      : status === "interrupted" ? "bad"
      : status === "failed" ? "bad" : "";
    const span = document.createElement("span");
    span.className = `chip ${cls}`;
    span.textContent = status;
    return span;
  }

  /** Render a job's log into `el` with the tail's last lines, newest at bottom. */
  function renderLog(el, text) {
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.textContent = text || "";
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  /* -------------------------------------------------- project selector */
  /* Every artefact the suite creates can belong to a project, and the choice
   * sticks across pages (localStorage) so the user picks it once per session.
   */
  const PROJECT_KEY = "om.project";

  async function fillProjects(select) {
    if (!select) return;
    let data = { projects: [] };
    try { data = await API.get("/api/suite/projects"); } catch (e) { /* db off */ }
    const keep = localStorage.getItem(PROJECT_KEY) || "";
    select.innerHTML = '<option value="">— no project —</option>' +
      (data.projects || []).map((p) =>
        `<option value="${p.slug}">${p.slug} · ${p.status}</option>`).join("");
    if (!(data.projects || []).length) {
      select.innerHTML = '<option value="">— no projects yet (see Projects) —</option>';
    } else if (keep && Array.from(select.options).some((o) => o.value === keep)) {
      select.value = keep;
    }
    select.addEventListener("change", () => {
      if (select.value) localStorage.setItem(PROJECT_KEY, select.value);
      else localStorage.removeItem(PROJECT_KEY);
    });
  }

  /** The project an artefact should be filed under (``undefined`` = none). */
  function project() {
    return localStorage.getItem(PROJECT_KEY) || undefined;
  }

  function ensureAuth() {
    // every suite page starts unauth'd; a 401 from /api/me bounces to /?next=…
    return API.get("/api/me").catch(() => null);
  }

  function busy(btn, on, label) {
    if (!btn) return;
    btn.disabled = !!on;
    if (!btn.dataset.label) btn.dataset.label = btn.textContent;
    btn.textContent = on ? (label || "working…") : btn.dataset.label;
  }

  window.SUITE = { chrome, fmt, poll, watchJob, statusChip, renderLog, ensureAuth,
                   fillProjects, project, busy, PAGES,
                   progress, emptyState, markNav };
})();
