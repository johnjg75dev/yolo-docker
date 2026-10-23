/* Shared helpers: API calls, login, live state (SSE + polling fallback). */
(function () {
  "use strict";

  const API = {
    async json(path, opts = {}) {
      const res = await fetch(path, {
        credentials: "same-origin",
        headers: opts.body && !(opts.body instanceof FormData)
          ? { "Content-Type": "application/json" } : undefined,
        ...opts,
        body: opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string"
          ? JSON.stringify(opts.body) : opts.body,
      });
      // 401 handling: bounce to the landing page once, but never for
      // /api/me or /api/login — those are how a page *asks* whether it is
      // signed in, and redirecting on them is an infinite loop.
      if (res.status === 401) {
        const anon = path.startsWith("/api/me") || path.startsWith("/api/login");
        const onLogin = location.pathname === "/login" || location.pathname === "/";
        if (!anon && !onLogin) {
          location.href = "/login?next=" +
            encodeURIComponent(location.pathname + location.search);
        }
        throw new Error(anon ? "not signed in" : "login required");
      }
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok) {
        const msg = (data && (data.detail || data.message)) || res.statusText;
        const err = new Error(msg);
        err.status = res.status;
        throw err;
      }
      return data;
    },
    get(path) { return API.json(path); },
    post(path, body) { return API.json(path, { method: "POST", body: body || {} }); },
    patch(path, body) { return API.json(path, { method: "PATCH", body: body || {} }); },
    del(path) { return API.json(path, { method: "DELETE" }); },
    async sse(path, handlers) {
      /* EventSource with a graceful "no support" story: returns null instead of
         throwing, so callers can fall back to polling. */
      if (!window.EventSource) return null;
      const es = new EventSource(path);
      Object.entries(handlers || {}).forEach(([name, fn]) => es.addEventListener(name, fn));
      return es;
    },
    async postForm(path, form) {
      return API.json(path, { method: "POST", body: form });
    },
  };

  const UI = {
    $(sel, root) { return (root || document).querySelector(sel); },
    $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); },
    toast(msg, bad) {
      let el = document.querySelector(".toast");
      if (!el) {
        el = document.createElement("div");
        el.className = "toast";
        document.body.appendChild(el);
      }
      el.textContent = msg;
      el.classList.toggle("bad", !!bad);
      el.classList.add("show");
      clearTimeout(el._t);
      el._t = setTimeout(() => el.classList.remove("show"), 2600);
    },
    vibrate(ms) { if (navigator.vibrate) navigator.vibrate(ms); },
    bytes(n) {
      if (n == null) return "–";
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " kB";
      return (n / 1024 / 1024).toFixed(1) + " MB";
    },
  };

  /* ------------------------------------------------------------------ */
  /* live session state: EventSource, with a polling fallback             */
  /* ------------------------------------------------------------------ */
  function live(sessionId, role, onState, onError) {
    let es = null;
    let pollTimer = null;
    let closed = false;
    let last = 0;

    const handle = (snap) => {
      if (!snap || closed) return;
      last = Date.now();
      onState(snap);
    };

    function startPolling() {
      if (pollTimer || closed) return;
      pollTimer = setInterval(async () => {
        try {
          const snap = await API.get(
            `/api/sessions/${sessionId}?role=${role}`);
          handle(snap);
        } catch (e) { /* keep trying */ }
      }, 2500);
    }

    function connect() {
      if (closed) return;
      // Prefer WebSocket when available: cheaper on mobile radios and bidirectional.
      if (typeof WebSocket !== "undefined") {
        try {
          const proto = location.protocol === "https:" ? "wss:" : "ws:";
          const url = proto + "//" + location.host + "/ws/sessions/" + sessionId + "?role=" + encodeURIComponent(role);
          const ws = new WebSocket(url);
          ws.onmessage = (ev) => {
            try {
              const msg = JSON.parse(ev.data);
              if (msg && msg.data) handle(msg.data);
            } catch (e) {}
          };
          ws.onopen = () => { last = Date.now(); };
          ws.onerror = () => { try { ws.close(); } catch (e) {} };
          ws.onclose = () => {
            if (closed) return;
            // fall back to SSE/polling
            es = null;
            if (typeof EventSource === "undefined") { startPolling(); return; }
            trySSE();
          };
          // store for close()
          es = { close() { try { ws.close(); } catch (e) {} } };
          return;
        } catch (e) { /* fall through */ }
      }
      trySSE();
    }

    function trySSE() {
      if (closed) return;
      if (typeof EventSource === "undefined") { startPolling(); return; }
      const url = `/api/sessions/${sessionId}/events?role=${role}`;
      es = new EventSource(url, { withCredentials: true });
      es.addEventListener("state", (ev) => {
        try { handle(JSON.parse(ev.data)); } catch (e) {}
      });
      es.addEventListener("shot", (ev) => {
        try { handle(JSON.parse(ev.data)); } catch (e) {}
      });
      es.addEventListener("reject", (ev) => {
        try { handle(JSON.parse(ev.data)); } catch (e) {}
      });
      es.addEventListener("skip", (ev) => {
        try { handle(JSON.parse(ev.data)); } catch (e) {}
      });
      es.addEventListener("seek", (ev) => {
        try { handle(JSON.parse(ev.data)); } catch (e) {}
      });
      es.onerror = () => {
        if (onError) onError();
        setTimeout(() => {
          if (!closed && Date.now() - last > 6000) startPolling();
        }, 3000);
      };
      setTimeout(() => {
        if (!closed && Date.now() - last > 6000) startPolling();
      }, 7000);
    }

    connect();
    return {
      close() {
        closed = true;
        if (es) es.close();
        if (pollTimer) clearInterval(pollTimer);
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* image helpers (camera side)                                         */
  /* ------------------------------------------------------------------ */
  async function blobFromVideo(video, maxEdge = 2560, quality = 0.92) {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) throw new Error("camera not ready");
    const k = Math.min(1, maxEdge / Math.max(w, h));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * k);
    canvas.height = Math.round(h * k);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) =>
      canvas.toBlob((b) => b ? resolve(b) : reject(new Error("encode failed")),
        "image/jpeg", quality));
  }

  async function blobFromFile(file, maxEdge = 2560, quality = 0.92) {
    // createImageBitmap with imageOrientation applies the EXIF rotation, so the
    // upload is always "what the phone showed", which is what the labeller and
    // any future training run expect
    let bmp;
    try {
      bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (e) {
      bmp = await createImageBitmap(file);
    }
    const k = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * k);
    canvas.height = Math.round(bmp.height * k);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) =>
      canvas.toBlob((b) => b ? resolve(b) : reject(new Error("encode failed")),
        "image/jpeg", quality));
  }

  /* --------------------------------------------------- version handshake */
  /* A deploy must reach the browser, even with a service worker installed:
   *  - re-register /sw.js with a cache-busting query, so the browser re-fetches
   *    the (build-stamped) worker instead of trusting the old one;
   *  - when a new worker takes control, reload once so the page runs the new
   *    JavaScript instead of the previous release's cached files.
   * Both are no-ops on plain http (service workers only exist in a secure
   * context) — which is exactly why a stale shell only ever shows up on https.
   */
  function updateServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js?build=" + Date.now(), { scope: "/" })
      .then((reg) => { if (reg.update) reg.update().catch(() => {}); })
      .catch(() => {});
    let reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloaded) return;
      reloaded = true;
      const key = "om.reloaded." + location.pathname;
      try {
        if (sessionStorage.getItem(key)) return;   // never loop
        sessionStorage.setItem(key, "1");
      } catch (e) { /* private mode */ }
      location.reload();
    });
  }

  /** Ask the server which build it is, and reload if it is newer than this page. */
  async function checkBuild(expected) {
    try {
      const res = await fetch("/healthz", { cache: "no-store" });
      const info = await res.json();
      if (expected && info.build && info.build !== expected && !window.__buildReload) {
        window.__buildReload = true;
        location.reload();
      }
      return info;
    } catch (e) { return null; }
  }

  function deviceName() {
    const ua = navigator.userAgent || "";
    const m = ua.match(/\((.*?)\)/);
    return (m ? m[1] : ua).slice(0, 90);
  }

  updateServiceWorker();

  /* If the tab was rendered by an older build than the one now running, reload
   * once (guarded, so it cannot loop). This is what makes "docker compose up
   * --build" reach open browsers — including phones — without a manual refresh.
   */
  (function watchBuild() {
    const meta = document.querySelector('meta[name="om-build"]');
    const expected = meta && meta.content;
    if (!expected || expected === "__BUILD__") return;
    checkBuild(expected);
  })();

  window.OM = { API, UI, live, blobFromVideo, blobFromFile, deviceName,
                updateServiceWorker, checkBuild };
})();
