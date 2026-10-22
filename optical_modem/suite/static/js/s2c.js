/* S2C page: transmit frames on the laptop, receive them on the phone.
 *
 * The two halves are independent: the sender plays frames in a loop and keeps
 * no session, the receiver posts stills as fast as it can and the server
 * reassembles them in any order. So a dropped frame, a re-shoot, or a phone
 * that spends ten seconds hunting for focus only costs throughput, never
 * correctness.
 */
(function () {
  "use strict";
  const { API, UI, blobFromVideo, blobFromFile } = window.OM;
  const { chrome, fmt, busy } = window.SUITE;
  const $ = UI.$;

  let transfer = null;      // current transfer info
  let playing = false;
  let playTimer = null;
  let frameIdx = 0;
  let playCount = 0;
  let stream = null;
  let rxTimer = null;
  let rxAhead = false;      // a shot is in flight
  let received = { got: 0, count: 0 };
  let fileBlob = null;

  /* ------------------------------------------------------------ options */
  async function loadOptions() {
    const opt = await API.get("/api/suite/s2c/options");
    const mod = $("#tx-mod"), ecc = $("#tx-ecc"), res = $("#tx-res");
    mod.innerHTML = ""; ecc.innerHTML = ""; res.innerHTML = "";
    opt.modulations.forEach((m) => {
      const o = new Option(m, m);
      mod.appendChild(o);
    });
    opt.eccs.forEach((e) => ecc.appendChild(new Option(e, e)));
    opt.resolutions.forEach((r) => res.appendChild(new Option(r, r)));
    mod.value = "rgb8"; ecc.value = "rs32"; res.value = "1280x720";

    const fnd = $("#tx-finder"), span = $("#tx-finder-span");
    fnd.innerHTML = ""; span.innerHTML = "";
    (opt.finder_shapes || [{ name: "none" }]).forEach((sh) => {
      const label = sh.name === "none" ? "none (max payload)"
        : `${sh.name} — ${sh.size}² cells${sh.symmetry ? ", " + sh.symmetry : ""}`;
      const o = new Option(label, sh.name);
      o.title = sh.description || "";
      fnd.appendChild(o);
    });
    (opt.finder_spans || [{ name: "corners" }]).forEach((sp) => {
      const o = new Option(sp.name, sp.name);
      o.title = sp.description || "";
      span.appendChild(o);
    });
    fnd.value = "none"; span.value = "corners";
    fillFinderGrid(opt.finder_shapes || [], fnd);
    finderNote(opt.finder_shapes || []);
    updateModelChip(opt.active);
    renderTransfers(opt.transfers);
    loadPalettes();
  }

  /* Colours are the point of a modulation profile — show them, with the bit
     depth and the bytes/frame they buy, instead of asking the user to remember
     that "rgb8x2" is 4 bits per cell. */
  let palettes = [];
  async function loadPalettes() {
    try {
      const data = await API.get("/api/suite/palette?cells=32&ecc=" +
                                 encodeURIComponent(($("#tx-ecc") || {}).value || "rs16"));
      palettes = data.modulations || [];
    } catch (e) { palettes = []; }
    paintPalette();
  }

  function paintPalette() {
    const host = $("#mod-swatch"), bits = $("#mod-bits");
    if (!host) return;
    const name = $("#tx-mod") ? $("#tx-mod").value : "";
    const p = palettes.find((x) => x.name === name);
    if (!p) { host.textContent = ""; if (bits) bits.textContent = ""; return; }
    if (bits) bits.textContent = `· ${p.bits} bit/cell`;
    host.innerHTML = `${window.VIZ ? window.VIZ.swatchStrip(p.hex) : ""}
      <span style="margin-left:8px">${p.colours} colours ·
      ${p.pair ? "hue-differential pairs · " : ""}up to
      <b>${p.max_user_payload ?? "?"} B</b> per frame at ${p.cells}² cells</span>`;
  }

  ["#tx-mod", "#tx-ecc"].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener("change", () => {
      if (sel === "#tx-ecc") loadPalettes(); else paintPalette();
    });
  });

  /* The picker: same catalogue as the <select>, but visible. A shape you cannot
     see is not a choice — this is the control that makes the feature usable. */
  function fillFinderGrid(shapes, select) {
    const grid = $("#finder-grid");
    if (!grid || !window.VIZ) return;
    grid.innerHTML = shapes.map((sh) =>
      window.VIZ.finderTile(sh.name, { selected: sh.name === select.value })).join("");
    grid.querySelectorAll(".finder-tile").forEach((btn) => {
      btn.addEventListener("click", () => {
        select.value = btn.dataset.shape;
        select.dispatchEvent(new Event("change"));
        grid.querySelectorAll(".finder-tile").forEach((b) =>
          b.classList.toggle("sel", b === btn));
      });
    });
    select.addEventListener("change", () => {
      grid.querySelectorAll(".finder-tile").forEach((b) =>
        b.classList.toggle("sel", b.dataset.shape === select.value));
    });
  }

  /* A finder costs payload: say so before the user finds out from a 400. */
  let finderCatalogue = [];
  function finderNote(shapes = finderCatalogue) {
    finderCatalogue = shapes;
    const name = $("#tx-finder") ? $("#tx-finder").value : "none";
    const shape = shapes.find((s) => s.name === name);
    const note = $("#finder-note");
    if (!note) return;
    if (!shape || name === "none") {
      note.textContent = "no reserved cells";
      note.title = "";
      return;
    }
    // 4 blocks of size² symbols out of cells² symbols (corners span)
    const cells = parseInt((($("#tx-res") || {}).value || "1280x720").split("x")[1], 10) > 800 ? 40 : 32;
    const cost = (4 * shape.size * shape.size) / (cells * cells);
    note.textContent = `costs ≈ ${(cost * 100).toFixed(1)}% of the code`;
    note.title = "the finder's cells are reserved, never written over by data";
  }

  function updateModelChip(active) {
    const chip = $("#model-chip");
    if (active) {
      chip.className = "chip ok";
      chip.textContent = "model: " + active.name;
    } else {
      chip.className = "chip bad";
      chip.textContent = "no active model — classical only";
      chip.title = "train + promote a model, or the receiver falls back to contour detection";
    }
  }

  ["#tx-finder", "#tx-res"].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener("change", () => finderNote());
  });

  /* ------------------------------------------------------------ transmit */
  $("#tx-file").addEventListener("change", async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    fileBlob = f || null;
    if (f) $("#tx-text").value = "";
  });

  $("#tx-go").addEventListener("click", async () => {
    const btn = $("#tx-go");
    busy(btn, true, "starting…");
    stopPlayback();
    try {
      const payload = {
        modulation: $("#tx-mod").value,
        ecc: $("#tx-ecc").value,
        resolution: $("#tx-res").value,
        fps: parseFloat($("#tx-fps").value) || 3,
        repeat: parseInt($("#tx-repeat").value, 10) || 1,
        finder: $("#tx-finder").value || "none",
        finder_span: $("#tx-finder-span").value || "corners",
      };
      if (fileBlob) {
        payload.data_b64 = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(String(r.result).split(",")[1]);
          r.onerror = rej;
          r.readAsDataURL(fileBlob);
        });
        payload.name = fileBlob.name;
      } else {
        payload.text = $("#tx-text").value || "hello from the screen";
        payload.name = "message.txt";
      }
      const res = await API.post("/api/suite/s2c/transmit", payload);
      setTransfer(res.transfer);
      UI.toast(`${res.transfer.frames} frames · ${res.transfer.payload_per_frame} B each`);
      startPlayback();
      await refreshTransfers();
    } catch (e) {
      UI.toast(e.message, true);
    } finally { busy(btn, false); }
  });

  function setTransfer(t) {
    transfer = t;
    received = { got: t.received || 0, count: t.frames };
    $("#tx-chip").className = "chip " + (t.complete ? "ok" : "");
    $("#tx-chip").textContent = `${t.frames} frames · ${fmt.bytes(t.bytes)}`;
    $("#tx-info").textContent =
      `${t.modulation}/${t.ecc} · ${t.cells}² cells @${t.cell_px}px · ` +
      (t.finder ? `finder ${t.finder.shape} · ` : "") +
      `${t.payload_per_frame} B/frame · ${t.rate_fps} fps`;
    $("#tx-play").disabled = false;
    $("#tx-full").disabled = false;
    $("#rx-go").disabled = false;
    $("#rx-reset").disabled = false;
    $("#player-badge").textContent = `frame 1/${t.frames}`;
    const sel = $("#rx-transfer");
    if (!Array.from(sel.options).some((o) => o.value === t.id)) {
      sel.innerHTML = "";
    }
    sel.value = t.id;
    updateRx();
    loadFrame(0);
  }

  function frameUrl(i) {
    return `/api/suite/s2c/${transfer.id}/frame.png?index=${i}`;
  }

  async function loadFrame(i) {
    if (!transfer) return;
    const idx = ((i % transfer.frames) + transfer.frames) % transfer.frames;
    const img = $("#player-img");
    img.src = frameUrl(idx);
    $("#player-badge").textContent =
      `frame ${idx + 1}/${transfer.frames}` + (playCount ? ` · loop ${playCount}` : "");
  }

  function startPlayback() {
    if (!transfer || playing) return;
    playing = true;
    $("#tx-play").textContent = "⏸ pause";
    const ms = 1000 / Math.max(1, transfer.rate_fps || 3);
    // preload the next frame while the current one is on screen: at 3–10 fps a
    // cold PNG fetch would stutter, and a stutter is a missed exposure
    playTimer = setInterval(() => {
      frameIdx += 1;
      if (frameIdx >= transfer.frames * (transfer.repeat || 1)) {
        frameIdx = 0;
        playCount += 1;
      }
      loadFrame(frameIdx % transfer.frames);
    }, ms);
  }

  function stopPlayback() {
    playing = false;
    if (playTimer) clearInterval(playTimer);
    playTimer = null;
    $("#tx-play").textContent = "▶ play";
  }

  $("#tx-play").addEventListener("click", () => {
    if (!transfer) return;
    if (playing) stopPlayback(); else startPlayback();
  });

  $("#tx-full").addEventListener("click", async () => {
    const el = $("#player");
    try {
      if (!document.fullscreenElement) await el.requestFullscreen();
      else await document.exitFullscreen();
    } catch (e) { UI.toast("fullscreen not available"); }
  });

  /* ------------------------------------------------------------ receive */
  function updateRx() {
    const pct = received.count ? (100 * received.got) / received.count : 0;
    $("#rx-bar").style.width = Math.min(100, pct).toFixed(1) + "%";
    $("#rx-progress").textContent =
      `${received.got} / ${received.count} frames`;
  }

  async function startCamera() {
    if (stream) return true;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" },
                 width: { ideal: 2560 }, height: { ideal: 1440 } },
        audio: false,
      });
    } catch (e) {
      UI.toast("camera refused (" + (e.name || e.message) + ") — use the file button", true);
      return false;
    }
    $("#rx-video").srcObject = stream;
    try { await $("#rx-video").play(); } catch (e) {}
    return true;
  }

  function applyResult(res) {
    if (!res) return;
    const t = res.transfer, r = res.result;
    if (t) {
      transfer = { ...(transfer || {}), ...t };
      received = { got: r.received, count: t.frames };
      updateRx();
    }
    const d = res.decode || {};
    $("#rx-last").textContent = d.ok
      ? `decoded seq ${d.seq} in ${d.ms} ms (${d.mode})`
      : `missed: ${d.ms} ms${d.reason ? " — " + d.reason : ""}`;
    $("#rx-chip").className = "chip " + (r.complete ? "ok" : "run");
    $("#rx-chip").textContent = r.complete ? "complete" : "receiving";
    if (r.complete) {
      stopReceive();
      showReceipt(r);
      UI.vibrate && UI.vibrate([30, 60, 30]);
    }
  }

  function showReceipt(r) {
    const host = $("#rx-result");
    host.innerHTML = "";
    const ok = r.sha1_ok;
    host.innerHTML =
      `<div class="callout ${ok ? "" : "bad"}" style="margin-top:12px">
         <p style="margin:0"><b>${ok ? "Received intact" : "Received, but the checksum differs"}</b>
         — ${fmt.bytes(r.bytes)} in ${r.frames} frames ` +
         `(${r.duplicates} duplicates ignored, ${r.failed} missed exposures)${ok ? "" : ". Re-send at a lower fps."}</p>
       </div>`;
    if (r.text) {
      const pre = document.createElement("pre");
      pre.className = "cmd";
      pre.textContent = r.text;
      host.appendChild(pre);
    } else {
      // binary payload: the bytes live in the server-side transfer, so the link
      // points straight at it — the browser saves it as a normal download
      const a = document.createElement("a");
      a.className = "btn";
      a.style.cssText = "display:inline-block;margin-top:10px;padding:9px 14px";
      a.href = `/api/suite/s2c/${encodeURIComponent(transfer.id)}/payload`;
      a.textContent = `Download ${r.name || "received.bin"} (${fmt.bytes(r.bytes)})`;
      a.download = r.name || "received.bin";
      host.appendChild(a);
      host.insertAdjacentHTML("beforeend", `<p class="small muted" style="margin-top:8px">
        sha1 ${r.sha1}${r.sha1_ok ? " — matches what was sent" : " — does NOT match the sender"}</p>`);
    }
  }

  async function shootOnce() {
    if (!transfer || rxAhead) return;
    rxAhead = true;
    try {
      let blob = null;
      if (stream) {
        blob = await blobFromVideo($("#rx-video"), 2560, 0.9);
      }
      if (!blob) { rxAhead = false; return; }
      const form = new FormData();
      form.append("file", blob, "frame.jpg");
      const res = await API.postForm(`/api/suite/s2c/${transfer.id}/receive`, form);
      applyResult(res);
    } catch (e) {
      if (e.status === 404) { stopReceive(); UI.toast("transfer expired — start a new one", true); }
    } finally {
      rxAhead = false;
    }
  }

  function startReceive() {
    if (!transfer) { UI.toast("start or pick a transfer first", true); return; }
    if (rxTimer) return;
    if (!transfer.complete) {
      $("#rx-chip").className = "chip run";
      $("#rx-chip").textContent = "receiving";
    }
    $("#rx-go").textContent = "Stop receiving";
    rxTimer = setInterval(shootOnce, 450);
    shootOnce();
  }

  function stopReceive() {
    if (rxTimer) clearInterval(rxTimer);
    rxTimer = null;
    $("#rx-go").textContent = "Start receiving";
    if (transfer && !transfer.complete && $("#rx-chip").textContent === "receiving") {
      $("#rx-chip").textContent = "paused";
    }
  }

  $("#rx-go").addEventListener("click", async () => {
    if (rxTimer) { stopReceive(); return; }
    const ok = await startCamera();
    if (!ok) {
      $("#rx-file").click();
    }
    startReceive();
  });

  $("#rx-file-btn").addEventListener("click", () => $("#rx-file").click());

  $("#rx-file").addEventListener("change", async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (!f || !transfer) return;
    try {
      const blob = await blobFromFile(f);
      const form = new FormData();
      form.append("file", blob, "photo.jpg");
      const res = await API.postForm(`/api/suite/s2c/${transfer.id}/receive`, form);
      applyResult(res);
    } catch (e) { UI.toast(e.message, true); }
  });

  $("#rx-reset").addEventListener("click", async () => {
    if (!transfer) return;
    try {
      await API.post(`/api/suite/s2c/${transfer.id}/reset`, {});
      received = { got: 0, count: transfer.frames };
      $("#rx-result").innerHTML = "";
      $("#rx-chip").className = "chip";
      $("#rx-chip").textContent = "idle";
      updateRx();
      UI.toast("receiver reset");
    } catch (e) { UI.toast(e.message, true); }
  });

  $("#rx-transfer").addEventListener("change", async () => {
    const tid = $("#rx-transfer").value;
    if (!tid) return;
    try {
      const st = await API.get(`/api/suite/s2c/${tid}`);
      setTransfer(st.transfer);
      received = { got: st.result.received || 0, count: st.transfer.frames };
      updateRx();
      if (st.result.complete) showReceipt(st.result);
      else $("#rx-result").innerHTML = "";
      stopPlayback();
      startPlayback();
      loadFrame(0);
    } catch (e) { UI.toast(e.message, true); }
  });

  /* ------------------------------------------------------------ transfers */
  async function refreshTransfers() {
    try {
      const data = await API.get("/api/suite/s2c/transfers");
      renderTransfers(data.transfers);
      return data.transfers;
    } catch (e) { return []; }
  }

  function renderTransfers(list) {
    const host = $("#transfers");
    const sel = $("#rx-transfer");
    if (!list || !list.length) {
      host.innerHTML = `<span class="muted">no transfers yet — send something</span>`;
      return;
    }
    const keep = sel.value;
    sel.innerHTML = "";
    list.forEach((t) => {
      const o = new Option(
        `${t.name} · ${t.frames} frames · ${fmt.bytes(t.bytes)}` +
        (t.complete ? " ✓" : ""), t.id);
      sel.appendChild(o);
    });
    if (keep && list.some((t) => t.id === keep)) sel.value = keep;
    else if (transfer) sel.value = transfer.id;

    host.innerHTML = "";
    const table = document.createElement("table");
    table.className = "t";
    table.innerHTML = `<thead><tr><th>transfer</th><th>params</th>` +
      `<th class="num">size</th><th class="num">received</th><th></th></tr></thead><tbody></tbody>`;
    const body = table.querySelector("tbody");
    list.forEach((t) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><b>${t.name}</b><div class="muted small mono">${t.id}</div></td>` +
        `<td class="small">${t.modulation}/${t.ecc} · ${t.cells}² @${t.cell_px}px · ` +
        `${t.rate_fps} fps ×${t.repeat}<div class="muted small">` +
        `${t.payload_per_frame} B/frame</div></td>` +
        `<td class="num">${fmt.bytes(t.bytes)}</td>` +
        `<td class="num">${t.received}/${t.frames}` +
        `${t.complete ? ` <span class="chip ok">done</span>` : ""}</td>` +
        `<td class="num"><button class="ghost" style="padding:5px 10px;font-size:.8rem">use</button></td>`;
      tr.querySelector("button").addEventListener("click", () => {
        $("#rx-transfer").value = t.id;
        $("#rx-transfer").dispatchEvent(new Event("change"));
      });
      body.appendChild(tr);
    });
    host.appendChild(table);
  }

  /* ------------------------------------------------------------ loopback */
  $("#lb-go").addEventListener("click", async () => {
    const btn = $("#lb-go");
    busy(btn, true, "running…");
    $("#lb-out").innerHTML = `<span class="muted">creating a transfer…</span>`;
    try {
      const tx = await API.post("/api/suite/s2c/transmit", {
        text: "loopback: the quick brown fox jumps over the lazy dog · " +
              "光学调制解调器 · 0123456789",
        name: "loopback.txt", repeat: 1, fps: 2,
      });
      const rep = await API.post(`/api/suite/s2c/${tx.transfer.id}/loopback`,
                                 { frames: Math.min(3, tx.transfer.frames), level: 0.35 });
      const rows = rep.frames.map((f) =>
        `<tr><td>${f.frame}</td><td>${f.mode}</td>` +
        `<td>${f.ok ? `<span class="chip ok">ok</span>` : `<span class="chip bad">no</span>`}</td>` +
        `<td class="num">${f.payload_matches ? "✓" : "✗"}</td>` +
        `<td class="num">${fmt.num(f.corner_err, 3)}</td>` +
        `<td class="num">${f.ms} ms</td><td class="muted">${f.reason || ""}</td></tr>`).join("");
      $("#lb-out").innerHTML =
        `<div class="row wrap" style="gap:6px;margin-bottom:8px">
          <span class="chip ${rep.decoded === rep.frames.length ? "ok" : "bad"}">
            ${rep.decoded}/${rep.frames.length} decoded</span>
          <span class="chip">corner err ${fmt.num(rep.corner_err_median, 3)}</span>
          <span class="chip ${rep.sha1_ok ? "ok" : ""}">${
            rep.sha1_ok ? "bytes identical" : "payload not reassembled"}</span></div>
        <table class="t"><thead><tr><th>#</th><th>mode</th><th>decode</th>
        <th class="num">payload</th><th class="num">corner err</th><th class="num">time</th>
        <th>note</th></tr></thead><tbody>${rows}</tbody></table>`;
      await refreshTransfers();
    } catch (e) {
      $("#lb-out").innerHTML = `<div class="callout bad">${e.message}</div>`;
    } finally { busy(btn, false); }
  });

  /* ------------------------------------------------------------ boot */
  chrome("/s2c");
  (async () => {
    try {
      await loadOptions();
    } catch (e) {
      UI.toast(e.message, true);
      return;
    }
    // resume the newest transfer if the page was reloaded mid-transfer
    const list = await refreshTransfers();
    if (list && list.length) {
      const newest = list[0];
      try {
        const st = await API.get(`/api/suite/s2c/${newest.id}`);
        setTransfer(st.transfer);
        received = { got: st.result.received || 0, count: st.transfer.frames };
        updateRx();
        if (st.result.complete) showReceipt(st.result);
      } catch (e) {}
    }
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") stopPlayback();
    });
  })();
})();
