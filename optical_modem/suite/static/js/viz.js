/* Pure-SVG visuals — no chart library, no CDN, works offline in the PWA.
 *
 * Everything here returns an SVG *string* so callers can drop it into
 * innerHTML. Kept deliberately small: the suite has no build step, and an
 * <svg> built from data is the cheapest way to make state legible.
 */
(function () {
  "use strict";

  const NS = 'xmlns="http://www.w3.org/2000/svg"';
  const C = {
    fg: "#e9f2f7", muted: "#8fa3b3", line: "#222b33", panel: "#121820",
    accent: "#3ea6ff", ok: "#35d0a5", warn: "#ffb020", bad: "#ff5c5c",
  };

  const esc = (s) => String(s == null ? "" : s)
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ------------------------------------------------------------- icons */
  /* 24x24, stroke-only, currentColor: they inherit the chip/card colour. */
  const PATHS = {
    chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    grid: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
    brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V15a3 3 0 0 0 4 2.8V20h6v-2.2A3 3 0 0 0 19 15v-2.2A3 3 0 0 0 18 7a3 3 0 0 0-3-3z"/><path d="M12 4v16"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    signal: '<path d="M4 20V10M9 20V6M14 20v-7M19 20V4"/>',
    camera: '<path d="M3 8a2 2 0 0 1 2-2h2l1.5-2h7L17 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="12.5" r="3.5"/>',
    screen: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    lock: '<rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3"/>',
    download: '<path d="M12 3v12M7 11l5 5 5-5M4 21h16"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  };

  function icon(name, size = 20, extra = "") {
    const d = PATHS[name] || PATHS.info;
    return `<svg ${NS} width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
      stroke-linejoin="round" ${extra}>${d}</svg>`;
  }

  /* --------------------------------------------------------- progress ring */
  function ring(pct, { size = 92, stroke = 8, label, sub } = {}) {
    const r = (size - stroke) / 2;
    const circ = 2 * Math.PI * r;
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    const dash = (circ * p) / 100;
    const colour = p >= 100 ? C.ok : C.accent;
    return `<svg ${NS} width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"
        role="img" aria-label="${p}% complete">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
              stroke="${C.line}" stroke-width="${stroke}"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
              stroke="${colour}" stroke-width="${stroke}" stroke-linecap="round"
              stroke-dasharray="${dash.toFixed(1)} ${circ.toFixed(1)}"
              transform="rotate(-90 ${size / 2} ${size / 2})"
              style="transition:stroke-dasharray .5s ease"/>
      <text x="50%" y="${sub ? "47%" : "53%"}" text-anchor="middle"
            fill="${C.fg}" font-size="${size * 0.26}" font-weight="700">${label != null ? label : p + "%"}</text>
      ${sub ? `<text x="50%" y="66%" text-anchor="middle" fill="${C.muted}"
            font-size="${Math.max(9, size * 0.12)}">${esc(sub)}</text>` : ""}
    </svg>`;
  }

  /* ---------------------------------------------------------- sparkline */
  function sparkline(values, { width = 120, height = 28, colour = C.accent } = {}) {
    const vals = (values || []).filter((v) => Number.isFinite(v));
    if (vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const span = hi - lo || 1;
    const pts = vals.map((v, i) => [
      (i / (vals.length - 1)) * (width - 2) + 1,
      height - 2 - ((v - lo) / span) * (height - 4),
    ]);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    return `<svg ${NS} width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <path d="${d}" fill="none" stroke="${colour}" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="${pts[pts.length - 1][0].toFixed(1)}" cy="${pts[pts.length - 1][1].toFixed(1)}"
              r="2" fill="${colour}"/></svg>`;
  }

  /* --------------------------------------------------------- line chart */
  /* series: {"label": [numbers...]}; epochs: [1..n]                          */
  const SERIES_COLOURS = [C.accent, C.ok, C.warn, "#b48ef7", C.bad, "#4fd1c5"];

  function lineChart(epochs, series, { width = 560, height = 200, pad = 34,
                                       maxSeries = 5 } = {}) {
    const keys = Object.keys(series || {})
      .filter((k) => (series[k] || []).some((v) => Number.isFinite(v)))
      .slice(0, maxSeries);
    if (!keys.length || !(epochs || []).length) return "";
    const all = keys.flatMap((k) => series[k].filter((v) => Number.isFinite(v)));
    let lo = Math.min(...all), hi = Math.max(...all);
    if (lo === hi) { lo -= 0.5; hi += 0.5; }
    const n = epochs.length;
    const x = (i) => pad + (n <= 1 ? 0 : (i / (n - 1)) * (width - pad - 10));
    const y = (v) => height - pad + 12 - ((v - lo) / (hi - lo)) * (height - pad - 22);

    const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const yy = height - pad + 12 - f * (height - pad - 22);
      const val = lo + f * (hi - lo);
      return `<line x1="${pad}" y1="${yy.toFixed(1)}" x2="${width - 10}" y2="${yy.toFixed(1)}"
                 stroke="${C.line}" stroke-width="1"/>
              <text x="${pad - 6}" y="${(yy + 3).toFixed(1)}" text-anchor="end"
                    fill="${C.muted}" font-size="9">${val.toFixed(2)}</text>`;
    }).join("");

    const paths = keys.map((k, idx) => {
      const colour = SERIES_COLOURS[idx % SERIES_COLOURS.length];
      const d = series[k].map((v, i) => Number.isFinite(v)
        ? `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}` : "").join(" ")
        .replace(/\s+/g, " ");
      return `<path d="${d}" fill="none" stroke="${colour}" stroke-width="1.8"
                    stroke-linejoin="round"/>`;
    }).join("");

    const legend = keys.map((k, idx) =>
      `<rect x="${pad + idx * 118}" y="${height - 12}" width="8" height="8" rx="2"
             fill="${SERIES_COLOURS[idx % SERIES_COLOURS.length]}"/>
       <text x="${pad + idx * 118 + 12}" y="${height - 5}" fill="${C.muted}"
             font-size="9">${esc(k.replace(/^metrics\//, "").slice(0, 16))}</text>`).join("");

    const ticks = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i)
      .map((i) => `<text x="${x(i).toFixed(1)}" y="${height - pad + 26}" text-anchor="middle"
                        fill="${C.muted}" font-size="9">${epochs[i]}</text>`).join("");

    return `<svg ${NS} viewBox="0 0 ${width} ${height}" width="100%" height="${height}"
        preserveAspectRatio="xMidYMid meet" role="img" aria-label="training curves">
      ${grid}${paths}${legend}${ticks}
      <text x="${pad}" y="10" fill="${C.muted}" font-size="9">epoch →</text>
    </svg>`;
  }

  /* ------------------------------------------------------------ bar rows */
  function bars(items, { width = 420, height = 16, gap = 8, max = null } = {}) {
    if (!items || !items.length) return "";
    const top = max == null ? Math.max(...items.map((i) => i.value)) : max;
    return items.map((it) => {
      const pct = top > 0 ? Math.max(2, (100 * it.value) / top) : 0;
      const colour = it.colour || (it.value >= (it.target == null ? 0.9 : it.target)
        ? C.ok : it.value >= 0.5 ? C.warn : C.bad);
      return `<div class="vbar">
        <span class="vbar-l">${esc(it.label)}</span>
        <span class="vbar-track"><i style="width:${pct.toFixed(1)}%;background:${colour}"></i></span>
        <span class="vbar-v">${it.text != null ? esc(it.text) : it.value.toFixed(2)}</span>
      </div>`;
    }).join("");
  }

  /* ------------------------------------------------------- pipeline diagram */
  /* The one picture that explains the whole product; state-aware so the user
     can see how far along they are without reading a word. */
  function pipeline(stages, { active = null } = {}) {
    const order = ["screen", "optic", "camera", "detector", "fec", "file"];
    const labels = {
      screen: "screen", optic: "lens / light", camera: "camera",
      detector: "YOLO26 + ring", fec: "RS / LDPC", file: "file",
    };
    const doneIds = new Set((stages || []).filter((s) => s.done).map((s) => s.id));
    const state = {
      screen: doneIds.has("send"),
      optic: doneIds.has("send"),
      camera: doneIds.has("dataset") || doneIds.has("send"),
      detector: doneIds.has("verify") || doneIds.has("train"),
      fec: doneIds.has("send"),
      file: doneIds.has("send"),
    };
    const W = 720, H = 132, boxW = 96, boxH = 56, y = 38;
    const gap = (W - 40 - boxW * order.length) / (order.length - 1);
    const parts = order.map((id, i) => {
      const x = 20 + i * (boxW + gap);
      const on = state[id];
      const isActive = active === id;
      const colour = on ? C.ok : C.line;
      const stroke = isActive ? C.accent : colour;
      const dash = on ? "" : 'stroke-dasharray="5 5"';
      const iconName = { screen: "screen", optic: "spark", camera: "camera",
                         detector: "brain", fec: "lock", file: "download" }[id];
      const arrow = i < order.length - 1
        ? `<g><line x1="${x + boxW + 4}" y1="${y + boxH / 2}" x2="${x + boxW + gap - 8}"
                   y2="${y + boxH / 2}" stroke="${on ? C.ok : C.line}" stroke-width="2"
                   ${on ? 'marker-end="url(#ph-arrow)"' : 'stroke-dasharray="4 4"'}/>
             <text x="${x + boxW + gap / 2}" y="${y + boxH / 2 - 8}" text-anchor="middle"
                   fill="${C.muted}" font-size="8">${["photons", "optics", "pixels", "tiles", "bytes"][i]}</text>
           </g>` : "";
      return `${arrow}
        <g>
          <rect x="${x}" y="${y}" width="${boxW}" height="${boxH}" rx="10"
                fill="${on ? "rgba(53,208,165,.10)" : C.panel}" stroke="${stroke}"
                stroke-width="${isActive ? 2 : 1.4}" ${dash}/>
          <g transform="translate(${x + boxW / 2 - 9},${y + 8})" stroke="${on ? C.ok : C.muted}"
             fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
             <svg width="18" height="18" viewBox="0 0 24 24">${PATHS[iconName]}</svg>
          </g>
          <text x="${x + boxW / 2}" y="${y + boxH - 8}" text-anchor="middle"
                fill="${on ? C.fg : C.muted}" font-size="9.5">${labels[id]}</text>
        </g>`;
    }).join("");
    return `<svg ${NS} class="pipeline" viewBox="0 0 ${W} ${H}" width="100%" height="${H}"
        preserveAspectRatio="xMidYMid meet" role="img"
        aria-label="screen to camera pipeline">
      <defs><marker id="ph-arrow" viewBox="0 0 10 10" refX="8" refY="5"
        markerWidth="5" markerHeight="5" orient="auto-start-reverse">
        <path d="M0 0 L10 5 L0 10 z" fill="${C.ok}"/></marker></defs>
      <text x="20" y="20" fill="${C.muted}" font-size="10">laptop screen</text>
      <text x="${W - 20}" y="20" text-anchor="end" fill="${C.muted}" font-size="10">the original bytes</text>
      ${parts}
      <text x="20" y="${H - 12}" fill="${C.muted}" font-size="9">
        solid green = this stage has run; dashed = waiting for you</text>
    </svg>`;
  }

  /* -------------------------------------------------------- finder picker */
  function finderTile(shape, { size = 56, selected = false } = {}) {
    const img = `<img src="/api/suite/finder/${encodeURIComponent(shape)}.svg?size=${size * 2}"
      width="${size}" height="${size}" alt="${esc(shape)} finder pattern" loading="lazy">`;
    return `<button type="button" class="finder-tile${selected ? " sel" : ""}"
      data-shape="${esc(shape)}" title="${esc(shape)}">${img}
      <span>${esc(shape)}</span></button>`;
  }

  /* ------------------------------------------------------------ swatches */
  function swatchStrip(hexes, { max = 32, cell = 16 } = {}) {
    const shown = (hexes || []).slice(0, max);
    return `<span class="swatches">` + shown.map((h) =>
      `<i style="background:${esc(h)}" title="${esc(h)}"></i>`).join("") +
      (hexes && hexes.length > max ? `<span class="muted small">+${hexes.length - max}</span>` : "") +
      `</span>`;
  }

  window.VIZ = { icon, ring, sparkline, lineChart, bars, pipeline, finderTile,
                 swatchStrip, colours: C };
})();
