/* Display role: show the current frame, pair the phone, follow the session. */
(function () {
  "use strict";
  const { API, UI, live } = window.OM;
  const $ = UI.$;

  const params = new URLSearchParams(location.search);
  let sessionId = params.get("session");
  let state = null;
  let shownIndex = -1;
  let prefetched = {};
  let wakeLock = null;
  let link = null;
  let hudHidden = false;
  let actualSize = false;

  /* ---------------- frame rendering ---------------- */
  function frameUrl(index) {
    return `/api/sessions/${sessionId}/frame.png?index=${index}`;
  }

  function prefetch(index) {
    if (index == null || prefetched[index]) return;
    const im = new Image();
    im.decoding = "async";
    im.src = frameUrl(index);
    prefetched[index] = im;
  }

  function showFrame(index) {
    if (index === shownIndex) return;
    shownIndex = index;
    const img = $("#frame");
    const im = prefetched[index];
    if (im && im.complete && im.naturalWidth) {
      img.src = im.src;
    } else {
      img.src = frameUrl(index);
    }
    prefetched = {};
    prefetch(index + 1);
  }

  /* ---------------- HUD ---------------- */
  function render(snap) {
    state = snap;
    const cur = snap.current;
    $("#counter").textContent = snap.done
      ? `done · ${snap.accepted}/${snap.count}`
      : `${snap.index + 1} / ${snap.count}`;
    $("#bar").style.width =
      (100 * Math.min(1, snap.index / Math.max(1, snap.count))).toFixed(1) + "%";
    if (cur) {
      $("#spec").textContent =
        `${cur.modulation} · ${cur.ecc} · ${cur.cells}² @${cur.cell_px}px · ${cur.payload_bytes}B`;
      const g = cur.geometry || {};
      const size = g.width && g.height ? ` · ${g.width}×${g.height}` : "";
      $("#spec").textContent += size;
    }
    const roles = snap.roles || {};
    const cam = roles.camera ? "phone connected" : "waiting for phone";
    $("#link").innerHTML = `<span class="dot"></span> ${cam}`;
    $("#link").classList.toggle("on", !!roles.camera);
    $("#link").classList.toggle("warn", !roles.camera);

    const last = snap.last_shot;
    $("#saved").textContent = last
      ? `saved #${last.index + 1}${last.label_found ? " ✓frame" : ""}`
      : "no shots yet";

    // once the phone is paired and shooting, the QR/code panel sits on top of
    // the code itself — collapse it to a chip (tap to bring it back)
    const paired = !!(snap.roles && snap.roles.camera) && snap.accepted > 0;
    const box = $("#pairbox");
    box.classList.toggle("mini", paired && !box.classList.contains("pinned"));
    if (paired && snap.accepted >= 2) dimHud(true);

    if (snap.done) {
      $("#frame").style.opacity = "0.15";
    } else {
      $("#frame").style.opacity = "1";
      if (cur) showFrame(cur.index);
    }
    $("#btn-undo").disabled = !snap.accepted;
  }

  /* HUD auto-dim: a mouse move / tap brings it back for a few seconds. */
  let dimTimer = null;
  function dimHud(on) {
    if (hudHidden) return;
    $("#hud").classList.toggle("dim", !!on);
    clearTimeout(dimTimer);
    if (on) {
      dimTimer = setTimeout(() => $("#hud").classList.add("dim"), 3500);
    }
  }
  ["mousemove", "touchstart", "keydown"].forEach((ev) =>
    window.addEventListener(ev, () => {
      if (hudHidden) return;
      $("#hud").classList.remove("dim");
      clearTimeout(dimTimer);
      dimTimer = setTimeout(() => {
        if (state && state.accepted >= 2) $("#hud").classList.add("dim");
      }, 3500);
    }, { passive: true }));

  function pairInfo(snap) {
    $("#code").textContent = snap.code;
    const url = `${location.origin}/camera?session=${snap.id}&code=${snap.code}`;
    $("#pairurl").textContent = url.replace(/^https?:\/\//, "");
    const qr = $("#qr");
    qr.hidden = false;
    // pair URL -> QR (server renders it; if segno is missing this 503s and the
    // code is still readable above)
    qr.src = `/api/qr.svg?data=${encodeURIComponent(url)}&scale=6`;
  }

  /* ---------------- actions ---------------- */
  async function act(path, body) {
    try {
      const snap = await API.post(`/api/sessions/${sessionId}/${path}`, body || {});
      if (snap && snap.id) render(snap);
    } catch (e) {
      UI.toast(e.message, true);
    }
  }

  $("#btn-undo").addEventListener("click", () => act("reject"));
  $("#btn-skip").addEventListener("click", () => act("skip"));
  $("#btn-fit").addEventListener("click", () => {
    actualSize = !actualSize;
    document.body.classList.toggle("actual", actualSize);
    $("#btn-fit").textContent = actualSize ? "Fit" : "1:1";
  });
  $("#btn-full").addEventListener("click", async () => {
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      else await document.exitFullscreen();
    } catch (e) { /* iOS Safari: not supported, ignore */ }
  });
  $("#pairbox").addEventListener("click", () => {
    // tap the chip to pin/unpin the full pairing panel
    $("#pairbox").classList.toggle("pinned");
    $("#pairbox").classList.toggle("mini",
      !$("#pairbox").classList.contains("pinned"));
  });
  $("#btn-hud").addEventListener("click", () => {
    hudHidden = !hudHidden;
    $("#hud").classList.toggle("dim", hudHidden);
    $("#btn-hud").textContent = hudHidden ? "Show" : "Hide";
  });

  /* ---------------- wake lock (keep the screen on) ---------------- */
  async function keepAwake() {
    try {
      if ("wakeLock" in navigator && !wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      }
    } catch (e) { /* not fatal */ }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") keepAwake();
  });

  /* ---------------- boot ---------------- */
  async function startNewSession() {
    const caps = await API.get("/api/capabilities");
    const snap = await API.post("/api/sessions", {
      count: 100,
      resolution: caps.defaults.resolution,
    });
    sessionId = snap.id;
    history.replaceState(null, "", `/display?session=${snap.id}`);
    return snap;
  }

  async function boot() {
    try {
      if (!sessionId) {
        pairInfo({ code: "······", id: "" });
        render(await startNewSession());
      } else {
        const snap = await API.get(`/api/sessions/${sessionId}`);
        render(snap);
      }
    } catch (e) {
      UI.toast("cannot open session: " + e.message, true);
      return;
    }
    pairInfo(state);
    link = live(sessionId, "display", render, () => {
      $("#link").innerHTML = `<span class="dot"></span> reconnecting`;
      $("#link").classList.remove("on");
      $("#link").classList.add("warn");
    });
    keepAwake();
    // keep the pairing code visible for the first seconds, then get out of the
    // way so the frame fills the screen
    setTimeout(() => { if (!state || !state.roles.camera) $("#hud").classList.remove("dim"); },
               4000);
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  boot();
})();
