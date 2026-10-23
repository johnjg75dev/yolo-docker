/* Camera role: live rear camera, shoot the frame on the laptop, upload, repeat.
 *
 * Two capture paths:
 *   - getUserMedia + canvas snapshot (best; needs HTTPS, which Coolify gives us)
 *   - <input type="file" capture="environment"> fallback (works everywhere,
 *     including when the embedded browser refuses camera access)
 * Both upload identical payloads: a JPEG normalised through a canvas, so EXIF
 * rotation is already applied and the server never has to guess.
 */
(function () {
  "use strict";
  const { API, UI, live, blobFromVideo, blobFromFile, deviceName } = window.OM;
  const $ = UI.$;

  const params = new URLSearchParams(location.search);
  let sessionId = params.get("session");
  let code = (params.get("code") || "").toUpperCase();
  let state = null;
  let link = null;
  let stream = null;
  let useFileInput = false;
  let busy = false;
  let lastIndexUploaded = -1;
  let autoMode = false;
  let dataSaver = false;
  let maxDim = 1280;
  let autoTimer = null;
  let lastPeek = 0;
  //: what the status line says when nothing is happening (differs when the
  //: page has no live stream and has to fall back to the system camera)
  let idleMessage = "point the phone at the laptop screen";

  /* ---------------- join ---------------- */
  function showJoinNote(msg, bad) {
    const el = $("#join-note");
    el.textContent = msg || "";
    el.style.color = bad ? "var(--bad)" : "";
  }

  async function joinSession(sid, withCode) {
    const snap = await API.post(`/api/sessions/${sid}/join`,
      { role: "camera", code: withCode });
    sessionId = sid;
    history.replaceState(null, "", `/camera?session=${sid}`);
    enterCamera(snap);
  }

  async function lookupByCode(entered) {
    // the code is the pairing secret; the server resolves it to a session
    const res = await API.get(`/api/sessions/lookup?code=${encodeURIComponent(entered)}`);
    return res.session_id;
  }

  /* ---------------- camera ---------------- */
  async function startStream() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return fallback("camera API unavailable in this browser");
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 2560 },
          height: { ideal: 1440 },
        },
        audio: false,
      });
    } catch (e) {
      return fallback(e && e.name ? e.name : "camera permission denied");
    }
    const video = $("#video");
    video.srcObject = stream;
    try { await video.play(); } catch (e) {}
    $("#guide").classList.remove("hidden");
    $("#nocam").classList.add("hidden");
    document.body.classList.remove("filemode");
    idleMessage = "point the phone at the laptop screen";
    $("#status-line").textContent = idleMessage;
    const track = stream.getVideoTracks()[0];
    const s = track.getSettings ? track.getSettings() : {};
    if (s.width) {
      $("#status-line").textContent =
        `camera ${s.width}×${s.height} — point at the laptop screen`;
    }
  }

  function fallback(why) {
    useFileInput = true;
    document.body.classList.add("filemode");
    $("#guide").classList.add("hidden");
    $("#video").classList.add("hidden");
    $("#nocam").classList.remove("hidden");
    $("#shutter").classList.remove("hidden");
    idleMessage = "tap the shutter to open the system camera";
    $("#status-line").textContent = idleMessage;
    console.info("capture: file-input mode (" + why + ")");
  }

  /* ---------------- state ---------------- */
  function render(snap) {
    state = snap;
    const cur = snap.current;
    $("#counter").textContent = snap.done ? "done"
      : `${snap.index + 1}/${snap.count}`;
    const roles = snap.roles || {};
    $("#link").innerHTML = `<span class="dot"></span>` +
      (roles.display ? " laptop" : " no display") +
      (roles.camera ? " · phone" : "");
    $("#link").classList.toggle("on", !!roles.display);
    $("#link").classList.toggle("warn", !roles.display);

    if (snap.done) {
      $("#shutter").disabled = true;
      $("#status-line").textContent =
        `session complete — ${snap.accepted} shots saved`;
      $("#target").style.opacity = "0.3";
      return;
    }
    $("#shutter").disabled = busy;
    $("#target").style.opacity = "1";
    if (cur && snap.frame_url) {
      $("#target").src = snap.frame_url + `&t=${snap.index}`;
    }
    if (!busy) {
      $("#status-line").textContent = cur
        ? (useFileInput ? idleMessage
           : `frame ${snap.index + 1}: ${cur.modulation} · ${cur.cells}² — ` +
             "shoot when the laptop shows the code")
        : "";
    }
  }

  /* ---------------- upload ---------------- */
  async function upload(blob, via, w, h) {
    if (!state || state.done || busy) return;
    busy = true;
    $("#shutter").disabled = true;
    $("#shutter").classList.add("busy");
    $("#status-line").textContent = "uploading…";
    const index = state.index;
    try {
      const form = new FormData();
      form.append("file", blob, `shot_${index}.jpg`);
      form.append("index", String(index));
      form.append("device", deviceName());
      form.append("via", via);
      form.append("width", String(w || 0));
      form.append("height", String(h || 0));
      const res = await API.postForm(`/api/sessions/${sessionId}/shots`, form);
      lastIndexUploaded = index;
      $("#shutter").classList.add("done");
      UI.vibrate(35);
      const lbl = res.shot && res.shot.label;
      $("#status-line").textContent =
        "saved #" + (index + 1) + (lbl && lbl.found ? " ✓ frame located" : "");
      if (res.state) render(res.state);
      setTimeout(() => {
        $("#shutter").classList.remove("done");
        if (state && !state.done) {
          $("#status-line").textContent = useFileInput
            ? idleMessage : "next frame — shoot when ready";
        }
      }, 650);
    } catch (e) {
      if (e.status === 409) {
        UI.toast("session moved on — refreshing", true);
        try { render(await API.get(`/api/sessions/${sessionId}?role=camera`)); } catch (_) {}
      } else {
        UI.toast(e.message || "upload failed", true);
        $("#status-line").textContent = "upload failed: " + (e.message || "");
      }
    } finally {
      busy = false;
      $("#shutter").disabled = !!(state && state.done);
      $("#shutter").classList.remove("busy");
    }
  }

  async function shoot() {
    if (busy || !state || state.done) return;
    if (useFileInput) { $("#file-input").click(); return; }
    try {
      const video = $("#video");
      const edge = dataSaver ? Math.min(maxDim, 960) : maxDim;
      const q = dataSaver ? 0.7 : 0.92;
      const blob = await blobFromVideo(video, edge, q);
      await upload(blob, "getusermedia", video.videoWidth, video.videoHeight);
    } catch (e) {
      UI.toast(e.message || "capture failed", true);
    }
  }

  async function autoTick() {
    if (!autoMode || busy || !state || state.done || useFileInput) return;
    const now = Date.now();
    if (now - lastPeek < 900) return;
    lastPeek = now;
    const video = $("#video");
    if (!video.videoWidth) return;
    try {
      const edge = dataSaver ? Math.min(maxDim, 640) : 960;
      const blob = await blobFromVideo(video, edge, 0.6);
      const form = new FormData();
      form.append("file", blob, "peek.jpg");
      form.append("index", String(state.index));
      const res = await API.postForm(`/api/sessions/${sessionId}/peek`, form);
      if (res && res.auto_ok) {
        // found & verified -> capture for real
        await shoot();
      } else if (res && res.found) {
        $("#status-line").textContent = "code seen (" + (res.score || 0).toFixed(2) + ") — refining…";
      }
    } catch (e) {
      // ignore peek errors; keep sampling
    }
  }

  /* ---------------- wiring ---------------- */
  $("#btn-auto")?.addEventListener("click", () => {
    autoMode = !autoMode;
    $("#btn-auto").textContent = "auto: " + (autoMode ? "on" : "off");
    if (autoMode) {
      if (!autoTimer) autoTimer = setInterval(autoTick, 1000);
      UI.toast("autonomous capture on — set the phone down");
    } else {
      if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
      UI.toast("autonomous capture off");
    }
  });
  $("#btn-datasaver")?.addEventListener("click", () => {
    dataSaver = !dataSaver;
    $("#btn-datasaver").textContent = "data saver: " + (dataSaver ? "on" : "off");
  });
  $("#shutter").addEventListener("click", shoot);
  $("#btn-undo").addEventListener("click", async () => {
    try {
      const snap = await API.post(`/api/sessions/${sessionId}/reject`, {});
      if (snap && snap.id) render(snap);
    } catch (e) { UI.toast(e.message, true); }
  });
  $("#btn-skip").addEventListener("click", async () => {
    if (!sessionId) return;
    try {
      const snap = await API.post(`/api/sessions/${sessionId}/skip`, {});
      if (snap && snap.id) render(snap);
    } catch (e) { UI.toast(e.message, true); }
  });
  $("#file-input").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (!file) return;
    try {
      const blob = await blobFromFile(file);
      const bmp = await createImageBitmap(blob);
      await upload(blob, "file-input", bmp.width, bmp.height);
    } catch (e) {
      UI.toast(e.message || "could not read photo", true);
    }
  });

  $("#code-input")?.addEventListener("input", (ev) => {
    ev.target.value = ev.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
  });
  $("#join-btn")?.addEventListener("click", async () => {
    const entered = ($("#code-input").value || "").trim().toUpperCase();
    if (!entered) { showJoinNote("enter the code shown on the laptop", true); return; }
    try {
      const sid = await lookupByCode(entered);
      await joinSession(sid, entered);
    } catch (e) {
      showJoinNote(e.status === 404 ? "no live session with that code"
                                    : e.message, true);
    }
  });

  function enterCamera(snap) {
    $("#join").classList.add("hidden");
    $("#view").classList.remove("hidden");
    render(snap);
    startStream();
    link = live(sessionId, "camera", render, () => {
      $("#link").innerHTML = `<span class="dot"></span> reconnecting`;
      $("#link").classList.remove("on");
    });
  }

  async function boot() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    try {
      await API.get("/api/me");
    } catch (e) {
      // not signed in: the 401 handler has already redirected
      return;
    }
    if (sessionId) {
      try {
        // resume: we have the id; the code is only needed the first time
        const snap = await API.get(`/api/sessions/${sessionId}?role=camera`);
        if (code) {
          await joinSession(sessionId, code);
        } else {
          enterCamera(snap);
        }
        return;
      } catch (e) {
        showJoinNote("that session is gone — " + e.message, true);
      }
    }
    // no session: offer the code form and any live sessions
    try {
      const data = await API.get("/api/sessions");
      const live_ = (data.sessions || []).filter((s) => s.index < s.count);
      if (live_.length) {
        $("#sessions-card").style.display = "";
        const list = $("#session-list");
        list.innerHTML = "";
        live_.forEach((s) => {
          const b = document.createElement("button");
          b.className = "ghost block";
          b.style.margin = "6px 0";
          b.textContent = `${s.label} — ${s.accepted}/${s.count}`;
          b.addEventListener("click", () => {
            $("#code-input").value = "";
            UI.toast("enter the code shown on the laptop to join " + s.label);
          });
          list.appendChild(b);
        });
      }
    } catch (e) { /* ignore */ }
    $("#code-input").focus();
  }

  boot();
})();
