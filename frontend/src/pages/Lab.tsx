import { useEffect, useState } from "react";
import { CheckCircle2, FlaskConical, Play, Upload, XCircle } from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { SwatchStrip } from "../components/Visuals";
import type {
  FinderShapeOption,
  LabDecodeResponse,
  LabSimulateResponse,
  PaletteEntry,
} from "../types";

export function Lab() {
  const { show } = useToast();
  const [labMode, setLabMode] = useState<"simulate" | "upload">("simulate");
  const [modulations, setModulations] = useState<string[]>(["rgb8"]);
  const [eccs, setEccs] = useState<string[]>(["rs32", "rs16", "ldpc12"]);
  const [resolutions, setResolutions] = useState<string[]>([
    "1280x720",
    "1920x1080",
  ]);
  const [finderShapes, setFinderShapes] = useState<FinderShapeOption[]>([]);
  const [palette, setPalette] = useState<PaletteEntry[]>([]);

  // Simulator controls
  const [text, setText] = useState("OPTICAL LAB PROBE 2026");
  const [modulation, setModulation] = useState("rgb8");
  const [ecc, setEcc] = useState("rs32");
  const [resolution, setResolution] = useState("1280x720");
  const [symbolPx, setSymbolPx] = useState(16);
  const [finder, setFinder] = useState("bullseye");
  const [level, setLevel] = useState(0.35);
  const [detectorMode, setDetectorMode] = useState<"auto" | "classical" | "yolo">("auto");
  const [seed, setSeed] = useState(42);
  const [busy, setBusy] = useState(false);

  const [simResult, setSimResult] = useState<LabSimulateResponse | null>(null);
  const [decResult, setDecResult] = useState<LabDecodeResponse | null>(null);

  useEffect(() => {
    api
      .s2cOptions()
      .then((opt) => {
        if (opt.modulations?.length) setModulations(opt.modulations);
        if (opt.eccs?.length) setEccs(opt.eccs);
        if (opt.resolutions?.length) setResolutions(opt.resolutions);
        if (opt.finder_shapes?.length) setFinderShapes(opt.finder_shapes);
      })
      .catch(() => {});

    api
      .labSimulate({
        text: "OPTICAL LAB PROBE 2026",
        modulation: "rgb8",
        ecc: "rs32",
        resolution: "1280x720",
        symbol_px: 16,
        finder: "none",
        level: 0.35,
        seed: 42,
        mode: "auto",
      })
      .then(setSimResult)
      .catch(() => {});
  }, []);

  useEffect(() => {
    api
      .s2cPalette(32, ecc)
      .then((p) => setPalette(p.modulations || []))
      .catch(() => {});
  }, [ecc]);

  const runSimulation = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    try {
      const res = await api.labSimulate({
        text,
        modulation,
        ecc,
        resolution,
        symbol_px: symbolPx,
        finder,
        level,
        mode: detectorMode,
        seed,
      });
      setDecResult(null);
      setSimResult(res);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Simulation failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleUploadInspect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const res = await api.labDecode(f, {
        modulation,
        ecc,
        resolution,
        symbol_px: symbolPx,
        finder,
        mode: detectorMode,
      });
      setSimResult(null);
      setDecResult(res);
      show(
        res.ok
          ? "Frame decoded cleanly!"
          : `Inspected: ${res.telemetry.reason || "no decode"}`,
        !res.ok
      );
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Decode inspection failed", true);
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  };

  const palEntry = palette.find((p) => p.name === modulation);
  const activeRes = simResult || decResult;
  const txPreview = simResult?.tx_preview;
  const cornerErrPct = simResult?.telemetry?.corner_err_pct;

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <FlaskConical size={13} /> Physical Layer &amp; Pose Forensics
          </div>
          <h1>Optical Channel Lab</h1>
          <p>
            Stress-test modulation constellations, corner finders, homography rectification, and Reed-Solomon/LDPC recovery across synthetic camera physics or real uploaded photos.
          </p>
        </div>
        <div className="tabs">
          <button
            type="button"
            className={`tab-btn ${labMode === "simulate" ? "active" : ""}`}
            onClick={() => setLabMode("simulate")}
          >
            Synthetic Physics Channel
          </button>
          <button
            type="button"
            className={`tab-btn ${labMode === "upload" ? "active" : ""}`}
            onClick={() => setLabMode("upload")}
          >
            Forensic Photo Decoder
          </button>
        </div>
      </div>

      {/* Control Strip */}
      <form onSubmit={runSimulation} className="card">
        <div className="grid-4">
          <label className="field">
            <span>Modulation Constellation</span>
            <select
              value={modulation}
              onChange={(e) => setModulation(e.target.value)}
            >
              {modulations.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>FEC Scheme</span>
            <select value={ecc} onChange={(e) => setEcc(e.target.value)}>
              {eccs.map((ec) => (
                <option key={ec} value={ec}>
                  {ec.toUpperCase()}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Corner Finder</span>
            <select value={finder} onChange={(e) => setFinder(e.target.value)}>
              {(finderShapes.length
                ? finderShapes.map((s) => s.name)
                : ["none", "bullseye", "square", "cross"]
              ).map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Detector Architecture</span>
            <select
              value={detectorMode}
              onChange={(e) => setDetectorMode(e.target.value as any)}
            >
              <option value="auto">Hybrid Auto (YOLO26 + Ring)</option>
              <option value="classical">Classical Contour Ring</option>
              <option value="yolo">YOLO26-Pose Only</option>
            </select>
          </label>
        </div>

        <div className="grid-3 mt">
          <label className="field">
            <span>Resolution</span>
            <select
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
            >
              {resolutions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Symbol Pitch (px)</span>
            <input
              type="number"
              min={8}
              max={40}
              value={symbolPx}
              onChange={(e) => setSymbolPx(Number(e.target.value) || 16)}
            />
          </label>

          {labMode === "simulate" ? (
            <label className="field">
              <span>RNG Seed</span>
              <div className="row" style={{ gap: 6 }}>
                <input
                  type="number"
                  value={seed}
                  onChange={(e) => setSeed(Number(e.target.value) || 0)}
                />
                <button
                  type="button"
                  className="secondary sm"
                  onClick={() => setSeed(Math.floor(Math.random() * 9999))}
                >
                  Dice
                </button>
              </div>
            </label>
          ) : (
            <div className="field">
              <span>Inspect Camera Image</span>
              <label className="btn" style={{ cursor: "pointer", width: "100%" }}>
                <Upload size={15} />
                {busy ? "Decoding…" : "Upload Photo"}
                <input
                  type="file"
                  accept="image/*"
                  style={{ display: "none" }}
                  onChange={handleUploadInspect}
                />
              </label>
            </div>
          )}
        </div>

        {labMode === "simulate" && (
          <div className="grid-2 mt" style={{ alignItems: "end" }}>
            <label className="field">
              <span>Single-Frame Test Payload</span>
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Short probe string…"
              />
            </label>

            <div className="row" style={{ gap: 14, alignItems: "flex-end" }}>
              <label className="field" style={{ flex: 1 }}>
                <div className="row spread">
                  <span>Channel Noise / Perspective / Glare Level</span>
                  <code style={{ color: "var(--accent)" }}>{level.toFixed(2)}</code>
                </div>
                <input
                  type="range"
                  min={0}
                  max={0.95}
                  step={0.05}
                  value={level}
                  onChange={(e) => setLevel(Number(e.target.value))}
                />
              </label>
              <button type="submit" disabled={busy}>
                <Play size={15} />
                {busy ? "Running…" : "Simulate Frame"}
              </button>
            </div>
          </div>
        )}

        {palEntry?.hex && (
          <div className="row spread mt" style={{ paddingTop: 6 }}>
            <SwatchStrip hexes={palEntry.hex} max={20} />
            <span className="small mono">
              {palEntry.bits} b/sym · Max payload {palEntry.max_user_payload} B/frame
            </span>
          </div>
        )}
      </form>

      {/* 3-Stage Visual Pipeline Inspection */}
      {activeRes && (
        <>
          <div
            className={`grid-${txPreview ? "3" : "2"}`}
            style={{ marginBottom: 16 }}
          >
            {txPreview && (
              <div className="card" style={{ marginBottom: 0 }}>
                <div className="row spread" style={{ marginBottom: 8 }}>
                  <h3>1. Ideal Screen Frame</h3>
                  <span className="pill">TX Symbol Grid</span>
                </div>
                <div
                  className="optical-frame-stage"
                  style={{ minHeight: 230, padding: 10 }}
                >
                  <img
                    src={txPreview}
                    alt="Ideal transmitted frame"
                    style={{ maxHeight: 250 }}
                  />
                </div>
              </div>
            )}

            <div className="card" style={{ marginBottom: 0 }}>
              <div className="row spread" style={{ marginBottom: 8 }}>
                <h3>{txPreview ? "2." : "1."} Sensor + Pose Quad</h3>
                <span
                  className={`pill ${
                    activeRes.telemetry.detections > 0 ? "ok" : "warn"
                  }`}
                >
                  {activeRes.telemetry.detector || "no lock"}
                </span>
              </div>
              <div
                className="optical-frame-stage"
                style={{ minHeight: 230, padding: 10, background: "#05070c" }}
              >
                <img
                  src={activeRes.cam_overlay}
                  alt="Camera capture with detected pose quad"
                  style={{ maxHeight: 250, imageRendering: "auto" }}
                />
              </div>
            </div>

            <div className="card" style={{ marginBottom: 0 }}>
              <div className="row spread" style={{ marginBottom: 8 }}>
                <h3>{txPreview ? "3." : "2."} Homography Rectified</h3>
                <span className={`pill ${activeRes.ok ? "ok" : "bad"}`}>
                  {activeRes.ok
                    ? "Demod + CRC ✓"
                    : activeRes.telemetry.reason || "failed"}
                </span>
              </div>
              <div
                className="optical-frame-stage"
                style={{ minHeight: 230, padding: 10 }}
              >
                {activeRes.rectified ? (
                  <img
                    src={activeRes.rectified}
                    alt="Rectified symbol lattice"
                    style={{ maxHeight: 250 }}
                  />
                ) : (
                  <span className="small">No homography quad localized</span>
                )}
              </div>
            </div>
          </div>

          {/* Forensic Telemetry Card */}
          <div className="card">
            <div className="card-header">
              <div className="row" style={{ gap: 10 }}>
                {activeRes.ok ? (
                  <CheckCircle2 size={20} color="#34d399" />
                ) : (
                  <XCircle size={20} color="#f87171" />
                )}
                <div>
                  <h2>
                    {activeRes.ok
                      ? "Frame Demodulated & Verified"
                      : `Demodulation Failed (${
                          activeRes.telemetry.reason || "no frame"
                        })`}
                  </h2>
                  {activeRes.decoded_text && (
                    <div className="mono small" style={{ color: "#34d399" }}>
                      Decoded UTF-8: "{activeRes.decoded_text}"
                    </div>
                  )}
                </div>
              </div>
              <span className="mono small">
                Total {activeRes.telemetry.elapsed_ms ?? "—"} ms
              </span>
            </div>

            <div className="grid-4">
              <div className="kpi-card">
                <div className="kpi-label">Pose Corner Error</div>
                <div className="kpi-value">
                  {typeof cornerErrPct === "number"
                    ? `${cornerErrPct.toFixed(2)}%`
                    : "—"}
                </div>
                <div className="kpi-sub">Target &lt; 2.0% of frame side</div>
              </div>

              <div className="kpi-card">
                <div className="kpi-label">FEC Symbols Repaired</div>
                <div className="kpi-value">{activeRes.telemetry.n_corrected}</div>
                <div className="kpi-sub">Scheme: {ecc.toUpperCase()}</div>
              </div>

              <div className="kpi-card">
                <div className="kpi-label">Ring / Finder Score</div>
                <div className="kpi-value">
                  {activeRes.telemetry.ring_score?.toFixed(2) ?? "—"} /{" "}
                  {activeRes.telemetry.finder_score?.toFixed(2) ?? "—"}
                </div>
                <div className="kpi-sub">
                  Quality: {activeRes.telemetry.quality?.toFixed(2) ?? "—"}
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-label">Detector Latency</div>
                <div className="kpi-value">
                  {activeRes.telemetry.elapsed_ms ?? "—"} ms
                </div>
                <div className="kpi-sub">
                  Active detector: {activeRes.telemetry.detector || "none"}
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
