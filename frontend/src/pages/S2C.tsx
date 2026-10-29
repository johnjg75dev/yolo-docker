import { useCallback, useEffect, useRef, useState } from "react";
import {
  Camera,
  CheckCircle2,
  Download,
  FileUp,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  QrCode,
  Radio,
  RefreshCw,
  RotateCcw,
  SkipBack,
  SkipForward,
  Sparkles,
  Upload,
  Video,
  VideoOff,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { SwatchStrip } from "../components/Visuals";
import type {
  FinderShapeOption,
  PaletteEntry,
  S2CLoopbackReport,
  S2CResultInfo,
  S2CTransferInfo,
} from "../types";

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function S2C() {
  const { show } = useToast();
  const [tab, setTab] = useState<"tx" | "rx" | "loopback">("tx");

  // Server options & palette
  const [modulations, setModulations] = useState<string[]>(["rgb8"]);
  const [eccs, setEccs] = useState<string[]>(["rs32", "rs16", "ldpc12"]);
  const [resolutions, setResolutions] = useState<string[]>([
    "1280x720",
    "1920x1080",
  ]);
  const [finderShapes, setFinderShapes] = useState<FinderShapeOption[]>([]);
  const [palette, setPalette] = useState<PaletteEntry[]>([]);
  const [transfers, setTransfers] = useState<S2CTransferInfo[]>([]);

  // TX state
  const [payloadKind, setPayloadKind] = useState<"text" | "file">("text");
  const [text, setText] = useState(
    "Hello over photons! Optical Modem S2C verified payload."
  );
  const [binaryFile, setBinaryFile] = useState<{
    name: string;
    size: number;
    b64: string;
  } | null>(null);
  const [modulation, setModulation] = useState("rgb8");
  const [ecc, setEcc] = useState("rs32");
  const [resolution, setResolution] = useState("1280x720");
  const [symbolPx, setSymbolPx] = useState(16);
  const [finder, setFinder] = useState("bullseye");
  const [repeat, setRepeat] = useState(2);
  const [fps, setFps] = useState(3);
  const [txBusy, setTxBusy] = useState(false);

  // Active transfer + player state
  const [activeTransfer, setActiveTransfer] = useState<S2CTransferInfo | null>(
    null
  );
  const [frameIdx, setFrameIdx] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [cinema, setCinema] = useState(false);
  const [showQr, setShowQr] = useState(false);

  // RX state
  const [rxTransferId, setRxTransferId] = useState("");
  const [rxResult, setRxResult] = useState<S2CResultInfo | null>(null);
  const [rxBusy, setRxBusy] = useState(false);
  const [decodeLog, setDecodeLog] = useState<any[]>([]);

  // Live camera scanner state
  const [camActive, setCamActive] = useState(false);
  const [scanHits, setScanHits] = useState(0);
  const [scanShots, setScanShots] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const scanBusyRef = useRef(false);

  // Loopback state
  const [loopText, setLoopText] = useState(
    "Loopback verification: end-to-end Reed-Solomon + Hybrid Pose decode."
  );
  const [loopLevel, setLoopLevel] = useState(0.3);
  const [loopSeed, setLoopSeed] = useState(42);
  const [loopBusy, setLoopBusy] = useState(false);
  const [loopReport, setLoopReport] = useState<S2CLoopbackReport | null>(null);

  const loadList = useCallback(async () => {
    try {
      const list = await api.s2cTransfers();
      setTransfers(list);
      if (!rxTransferId && list.length > 0) {
        setRxTransferId(list[0].id);
      }
      if (!activeTransfer && list.length > 0) {
        setActiveTransfer(list[0]);
      } else if (activeTransfer) {
        const up = list.find((t) => t.id === activeTransfer.id);
        if (up) setActiveTransfer(up);
      }
    } catch {
      // ignore
    }
  }, [activeTransfer, rxTransferId]);

  useEffect(() => {
    api
      .s2cOptions()
      .then((opt) => {
        if (opt.modulations?.length) setModulations(opt.modulations);
        if (opt.eccs?.length) setEccs(opt.eccs);
        if (opt.resolutions?.length) setResolutions(opt.resolutions);
        if (opt.finder_shapes?.length) {
          setFinderShapes(opt.finder_shapes);
          const firstNonNone = opt.finder_shapes.find((s) => s.name !== "none");
          if (firstNonNone) setFinder(firstNonNone.name);
        }
        if (opt.transfers?.length) {
          setTransfers(opt.transfers);
          setActiveTransfer(opt.transfers[0]);
          setRxTransferId(opt.transfers[0].id);
        }
      })
      .catch(() => {});
    loadList();
  }, []);

  useEffect(() => {
    api
      .s2cPalette(32, ecc)
      .then((p) => setPalette(p.modulations || []))
      .catch(() => {});
  }, [ecc]);

  useEffect(() => {
    if (!rxTransferId) return;
    api
      .s2cGet(rxTransferId)
      .then((d) => {
        setRxResult(d.result);
        if (d.log?.length) setDecodeLog(d.log);
      })
      .catch(() => {});
  }, [rxTransferId]);

  // Frame player ticker
  useEffect(() => {
    const nFrames = activeTransfer?.frames || 0;
    if (!playing || nFrames <= 1) return;
    const ms = Math.max(80, Math.round(1000 / Math.max(0.5, fps)));
    const t = setInterval(() => {
      setFrameIdx((prev) => (prev + 1) % nFrames);
    }, ms);
    return () => clearInterval(t);
  }, [playing, fps, activeTransfer?.frames]);

  // Escape exits cinema fullscreen
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && cinema) setCinema(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cinema]);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((tr) => tr.stop());
      streamRef.current = null;
    }
    setCamActive(false);
  }, []);

  useEffect(() => {
    return () => stopCamera();
  }, [stopCamera]);

  const startCamera = async () => {
    if (!rxTransferId) {
      show("Select or transmit a transfer first", true);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setScanHits(0);
      setScanShots(0);
      setCamActive(true);
    } catch (e: any) {
      show(
        e instanceof Error
          ? `Camera unavailable: ${e.message}`
          : "Camera permission denied or HTTPS required",
        true
      );
    }
  };

  useEffect(() => {
    if (!camActive || !rxTransferId) return;
    const canvas = document.createElement("canvas");
    const timer = setInterval(async () => {
      const vid = videoRef.current;
      if (!vid || vid.readyState < 2 || scanBusyRef.current) return;
      scanBusyRef.current = true;
      try {
        canvas.width = vid.videoWidth || 960;
        canvas.height = vid.videoHeight || 540;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.drawImage(vid, 0, 0, canvas.width, canvas.height);
        const blob: Blob | null = await new Promise((res) =>
          canvas.toBlob(res, "image/jpeg", 0.9)
        );
        if (!blob) return;
        setScanShots((s) => s + 1);
        const out = await api.s2cReceive(rxTransferId, blob, "camera.jpg");
        if (out.decode?.ok) setScanHits((h) => h + 1);
        setRxResult(out.result);
        setDecodeLog((prev) => [out.decode, ...prev.slice(0, 7)]);
        setTransfers((prev) =>
          prev.map((t) => (t.id === out.transfer.id ? out.transfer : t))
        );
        if (out.result.complete) {
          show("Transfer 100% received & SHA-1 verified!");
          stopCamera();
        }
      } catch {
        // ignore transient camera frame error
      } finally {
        scanBusyRef.current = false;
      }
    }, 650);
    return () => clearInterval(timer);
  }, [camActive, rxTransferId, show, stopCamera]);

  const handlePickBinaryFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 64 * 1024) {
      show("Keep optical S2C files under 64 KB for practical frame counts", true);
      return;
    }
    const buf = await f.arrayBuffer();
    setBinaryFile({
      name: f.name,
      size: f.size,
      b64: arrayBufferToBase64(buf),
    });
  };

  const handleTransmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTxBusy(true);
    try {
      const res = await api.s2cTransmit({
        text: payloadKind === "text" ? text : undefined,
        data_b64: payloadKind === "file" ? binaryFile?.b64 : undefined,
        name: payloadKind === "file" ? binaryFile?.name : "message.txt",
        modulation,
        ecc,
        resolution,
        cells: null,
        symbol_px: symbolPx,
        repeat,
        fps,
        finder,
      });
      setActiveTransfer(res.transfer);
      setRxTransferId(res.transfer.id);
      setRxResult(res.result);
      setFrameIdx(0);
      setPlaying(true);
      await loadList();
      show(
        `Encoded ${res.transfer.bytes} bytes into ${res.transfer.frames} optical frame(s)`
      );
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Transmit failed", true);
    } finally {
      setTxBusy(false);
    }
  };

  const handleUploadPhotos = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (!files.length || !rxTransferId) return;
    setRxBusy(true);
    let accepted = 0;
    try {
      for (const f of files) {
        const res = await api.s2cReceive(rxTransferId, f, f.name);
        if (res.decode?.ok) accepted++;
        setRxResult(res.result);
        setDecodeLog((prev) => [res.decode, ...prev.slice(0, 9)]);
      }
      await loadList();
      show(`Processed ${files.length} image(s) · ${accepted} frame(s) decoded`);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Decode failed", true);
    } finally {
      setRxBusy(false);
      e.target.value = "";
    }
  };

  const handleResetRx = async (tid: string) => {
    try {
      const r = await api.s2cReset(tid);
      setRxResult(r.result);
      await loadList();
      show("Reset received packet buffer");
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Reset failed", true);
    }
  };

  const handleRunLoopback = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoopBusy(true);
    try {
      // Create a dedicated transfer for loopback testing so any text/constellation works immediately
      const tx = await api.s2cTransmit({
        text: loopText,
        name: "loopback-probe.txt",
        modulation,
        ecc,
        resolution,
        cells: null,
        symbol_px: symbolPx,
        repeat,
        fps,
        finder,
      });
      const rep = await api.s2cLoopback(tx.transfer.id, {
        level: loopLevel,
        seed: loopSeed,
      });
      setLoopReport(rep);
      setActiveTransfer(tx.transfer);
      await loadList();
      show(
        rep.sha1_ok
          ? `Loopback SHA-1 verified (${rep.decoded}/${rep.total_frames} frames decoded)`
          : `Loopback incomplete at noise level ${loopLevel.toFixed(2)}`,
        !rep.sha1_ok
      );
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Loopback failed", true);
    } finally {
      setLoopBusy(false);
    }
  };

  const palEntry = palette.find((p) => p.name === modulation);
  const payloadBytes =
    payloadKind === "file"
      ? binaryFile?.size || 0
      : new TextEncoder().encode(text).length;

  const selectedRxTransfer =
    transfers.find((t) => t.id === rxTransferId) || activeTransfer;
  const qrPairUrl =
    typeof window !== "undefined" ? `${window.location.origin}/s2c` : "/s2c";

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Radio size={13} /> Air-Gapped Optical Data Link
          </div>
          <h1>Screen-to-Camera Transceiver</h1>
          <p>
            Encode UTF-8 text or binary files into calibrated colour symbol frames, broadcast at 1:1 native pixel pitch,
            and demodulate via live camera, uploaded photos, or synthetic loopback.
          </p>
        </div>
        <div className="tabs">
          <button
            type="button"
            className={`tab-btn ${tab === "tx" ? "active" : ""}`}
            onClick={() => {
              stopCamera();
              setTab("tx");
            }}
          >
            1. Transmitter (Screen)
          </button>
          <button
            type="button"
            className={`tab-btn ${tab === "rx" ? "active" : ""}`}
            onClick={() => setTab("rx")}
          >
            2. Receiver (Camera)
          </button>
          <button
            type="button"
            className={`tab-btn ${tab === "loopback" ? "active" : ""}`}
            onClick={() => {
              stopCamera();
              setTab("loopback");
            }}
          >
            3. Simulated Loopback
          </button>
        </div>
      </div>

      {/* TAB 1: TRANSMITTER */}
      {tab === "tx" && (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <form onSubmit={handleTransmit} className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>Configure Optical Broadcast</h2>
              <div className="tabs">
                <button
                  type="button"
                  className={`tab-btn ${payloadKind === "text" ? "active" : ""}`}
                  onClick={() => setPayloadKind("text")}
                >
                  UTF-8 Text
                </button>
                <button
                  type="button"
                  className={`tab-btn ${payloadKind === "file" ? "active" : ""}`}
                  onClick={() => setPayloadKind("file")}
                >
                  Binary File
                </button>
              </div>
            </div>

            {payloadKind === "text" ? (
              <label className="field">
                <span>Payload Message ({payloadBytes} bytes)</span>
                <textarea
                  rows={3}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Enter message, credentials, SSH key, or JSON…"
                />
              </label>
            ) : (
              <div className="field">
                <span>Binary Payload File (max 64 KB)</span>
                <label
                  className="card-subtle"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    cursor: "pointer",
                    borderStyle: "dashed",
                  }}
                >
                  <FileUp size={20} color="#22d3ee" />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, color: "var(--fg)" }}>
                      {binaryFile ? binaryFile.name : "Choose file from disk…"}
                    </div>
                    <div className="small">
                      {binaryFile
                        ? `${binaryFile.size} bytes ready for packetization`
                        : "Supports keys, configs, archives, or small images"}
                    </div>
                  </div>
                  <input
                    type="file"
                    style={{ display: "none" }}
                    onChange={handlePickBinaryFile}
                  />
                </label>
              </div>
            )}

            <div className="grid-3 mt">
              <label className="field">
                <span>Modulation</span>
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
                <span>FEC Code</span>
                <select value={ecc} onChange={(e) => setEcc(e.target.value)}>
                  {eccs.map((ec) => (
                    <option key={ec} value={ec}>
                      {ec.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Canvas Resolution</span>
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
            </div>

            <div className="grid-2 mt">
              <label className="field">
                <span>Symbol Cell Pitch (px)</span>
                <input
                  type="number"
                  min={8}
                  max={40}
                  value={symbolPx}
                  onChange={(e) => setSymbolPx(Number(e.target.value) || 16)}
                />
              </label>

              <label className="field">
                <span>Carousel Repeat Passes</span>
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={repeat}
                  onChange={(e) => setRepeat(Math.max(1, Number(e.target.value) || 1))}
                />
              </label>
            </div>

            {palEntry && (
              <div className="card-subtle mt" style={{ padding: "10px 12px" }}>
                <div className="row spread" style={{ marginBottom: 6 }}>
                  <span className="small">
                    <strong>{palEntry.name}</strong> · {palEntry.bits} bit(s)/sym ·{" "}
                    {palEntry.colours} colours · up to{" "}
                    <code>{palEntry.max_user_payload} B/frame</code>
                  </span>
                </div>
                <SwatchStrip hexes={palEntry.hex} max={18} />
              </div>
            )}

            {finderShapes.length > 0 && (
              <div className="mt">
                <span
                  style={{
                    display: "block",
                    fontSize: "0.78rem",
                    fontWeight: 550,
                    color: "var(--fg-soft)",
                    marginBottom: 6,
                  }}
                >
                  Corner Finder Pattern
                </span>
                <div className="grid-4" style={{ gap: 8 }}>
                  {finderShapes.slice(0, 8).map((fs) => (
                    <button
                      key={fs.name}
                      type="button"
                      onClick={() => setFinder(fs.name)}
                      style={{
                        background:
                          finder === fs.name
                            ? "rgba(34, 211, 238, 0.14)"
                            : "var(--inset)",
                        border:
                          finder === fs.name
                            ? "1px solid var(--accent)"
                            : "1px solid var(--border)",
                        padding: "8px 6px",
                        flexDirection: "column",
                        gap: 5,
                        color: "var(--fg)",
                      }}
                    >
                      {fs.name !== "none" ? (
                        <img
                          src={api.finderSvgUrl(fs.name, 36)}
                          alt={fs.name}
                          style={{ width: 26, height: 26, borderRadius: 3 }}
                        />
                      ) : (
                        <span className="mono small" style={{ height: 26, lineHeight: "26px" }}>
                          ∅
                        </span>
                      )}
                      <span className="small" style={{ fontSize: 11 }}>
                        {fs.name}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="row spread mt" style={{ marginTop: 18 }}>
              <button
                type="button"
                className="ghost sm"
                onClick={() => setShowQr((v) => !v)}
              >
                <QrCode size={15} /> Pair Phone Receiver
              </button>
              <button type="submit" disabled={txBusy}>
                <Radio size={15} />
                {txBusy ? "Encoding Frames…" : "Generate & Broadcast Frames"}
              </button>
            </div>

            {showQr && (
              <div
                className="card-subtle mt"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: 14,
                }}
              >
                <img
                  src={api.qrSvgUrl(qrPairUrl)}
                  alt="Phone receiver QR"
                  style={{
                    width: 112,
                    height: 112,
                    background: "#fff",
                    padding: 6,
                    borderRadius: 8,
                  }}
                />
                <div>
                  <h4>Scan with Phone Camera</h4>
                  <p className="small" style={{ margin: "4px 0 6px" }}>
                    Opens this transceiver on your phone so you can switch to{" "}
                    <strong>2. Receiver (Camera)</strong> and point at this screen.
                  </p>
                  <code style={{ fontSize: 11 }}>{qrPairUrl}</code>
                </div>
              </div>
            )}
          </form>

          {/* Native-PNG Optical Frame Player */}
          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <div>
                <h2>Optical Frame Player</h2>
                <p className="small" style={{ margin: 0 }}>
                  Rendered at 1:1 native PNG pitch (no lossy scaling) for clean camera capture.
                </p>
              </div>
              {activeTransfer && (
                <div className="row" style={{ gap: 6 }}>
                  <span className="pill ok">ID {activeTransfer.id}</span>
                  <button
                    type="button"
                    className="secondary sm"
                    onClick={() => setCinema(true)}
                  >
                    <Maximize2 size={14} /> Cinema Fullscreen
                  </button>
                </div>
              )}
            </div>

            {activeTransfer ? (
              <>
                <div className="optical-frame-stage" style={{ minHeight: 340 }}>
                  <img
                    src={api.s2cFrameUrl(activeTransfer.id, frameIdx)}
                    alt={`Optical frame ${frameIdx + 1}`}
                    style={{ maxHeight: 420 }}
                  />
                </div>

                <div className="row spread mt">
                  <div className="row" style={{ gap: 6 }}>
                    <button
                      type="button"
                      className="secondary sm"
                      onClick={() =>
                        setFrameIdx(
                          (i) =>
                            (i - 1 + activeTransfer.frames) %
                            Math.max(1, activeTransfer.frames)
                        )
                      }
                    >
                      <SkipBack size={14} />
                    </button>
                    <button
                      type="button"
                      className="sm"
                      onClick={() => setPlaying((p) => !p)}
                    >
                      {playing ? <Pause size={14} /> : <Play size={14} />}
                      {playing ? "Pause" : "Play"}
                    </button>
                    <button
                      type="button"
                      className="secondary sm"
                      onClick={() =>
                        setFrameIdx((i) => (i + 1) % Math.max(1, activeTransfer.frames))
                      }
                    >
                      <SkipForward size={14} />
                    </button>
                  </div>

                  <span className="mono small">
                    Frame {frameIdx + 1} / {activeTransfer.frames} · SHA-1{" "}
                    {activeTransfer.sha1.slice(0, 8)}
                  </span>

                  <label className="row small" style={{ gap: 6 }}>
                    <span>{fps} fps</span>
                    <input
                      type="range"
                      min={1}
                      max={12}
                      step={1}
                      value={fps}
                      onChange={(e) => setFps(Number(e.target.value))}
                      style={{ width: 90 }}
                    />
                  </label>
                </div>
              </>
            ) : (
              <div className="empty-state">
                <Radio size={28} color="#22d3ee" style={{ marginBottom: 8 }} />
                <h3>No Active Broadcast Yet</h3>
                <p className="small">
                  Click <strong>Generate &amp; Broadcast Frames</strong> on the left to encode your first optical packet carousel.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: RECEIVER */}
      {tab === "rx" && (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>Optical Receiver &amp; Scanner</h2>
              <span className="pill run">Hybrid Pose + FEC</span>
            </div>

            <label className="field">
              <span>Target Transfer Session</span>
              <select
                value={rxTransferId}
                onChange={(e) => setRxTransferId(e.target.value)}
              >
                {transfers.length === 0 && (
                  <option value="">No transfers yet — create one in TX</option>
                )}
                {transfers.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.id} · {t.name || "payload"} ({t.modulation}/{t.ecc} ·{" "}
                    {t.received}/{t.frames} frames)
                  </option>
                ))}
              </select>
            </label>

            {/* Live WebRTC Scanner */}
            <div className="card-subtle mt">
              <div className="row spread" style={{ marginBottom: 10 }}>
                <div>
                  <h4>Live Camera Auto-Scanner</h4>
                  <p className="small" style={{ margin: 0 }}>
                    Point your webcam or phone rear camera at the transmitting screen.
                  </p>
                </div>
                {camActive ? (
                  <button type="button" className="danger sm" onClick={stopCamera}>
                    <VideoOff size={14} /> Stop Camera
                  </button>
                ) : (
                  <button
                    type="button"
                    className="sm"
                    onClick={startCamera}
                    disabled={!rxTransferId}
                  >
                    <Video size={14} /> Start Live Camera
                  </button>
                )}
              </div>

              {camActive && (
                <div>
                  <div
                    style={{
                      position: "relative",
                      background: "#000",
                      borderRadius: 8,
                      overflow: "hidden",
                    }}
                  >
                    <video
                      ref={videoRef}
                      playsInline
                      muted
                      style={{ width: "100%", maxHeight: 280, display: "block" }}
                    />
                    <div
                      style={{
                        position: "absolute",
                        inset: "14% 18%",
                        border: "2px dashed rgba(34, 211, 238, 0.75)",
                        borderRadius: 10,
                        pointerEvents: "none",
                      }}
                    />
                  </div>
                  <div className="row spread small mt">
                    <span>
                      Frames sampled: <code>{scanShots}</code>
                    </span>
                    <span style={{ color: "var(--ok)" }}>
                      Packets accepted: <code>{scanHits}</code>
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Still Photo Upload */}
            <div className="card-subtle mt">
              <div className="row spread">
                <div>
                  <h4>Upload Camera Photo(s)</h4>
                  <p className="small" style={{ margin: 0 }}>
                    Decode JPEG/PNG captures taken with any smartphone or DSLR.
                  </p>
                </div>
                <label className="btn secondary sm" style={{ cursor: "pointer" }}>
                  <Upload size={14} />
                  {rxBusy ? "Decoding…" : "Select Photo(s)"}
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    disabled={!rxTransferId || rxBusy}
                    style={{ display: "none" }}
                    onChange={handleUploadPhotos}
                  />
                </label>
              </div>
            </div>

            {decodeLog.length > 0 && (
              <div className="mt">
                <h4 style={{ marginBottom: 6 }}>Recent Frame Telemetry</h4>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {decodeLog.slice(0, 5).map((d, i) => (
                    <div
                      key={i}
                      className="card-subtle"
                      style={{ padding: "7px 10px", fontSize: 12 }}
                    >
                      <div className="row spread">
                        <span className={`pill ${d?.ok ? "ok" : "warn"}`}>
                          {d?.ok ? `Seq #${d.seq ?? 0} ✓` : d?.reason || "no lock"}
                        </span>
                        <span className="mono small">
                          det={d?.detector || "—"} · {d?.ms ?? "—"} ms
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Reassembly & Verified Payload */}
          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>Packet Reassembly &amp; Payload</h2>
              {selectedRxTransfer && (
                <div className="row" style={{ gap: 6 }}>
                  <span
                    className={`pill ${
                      selectedRxTransfer.complete ? "ok" : "run"
                    }`}
                  >
                    {selectedRxTransfer.received} / {selectedRxTransfer.frames}{" "}
                    received
                  </span>
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={() => handleResetRx(selectedRxTransfer.id)}
                    title="Reset received buffer"
                  >
                    <RotateCcw size={13} />
                  </button>
                </div>
              )}
            </div>

            {selectedRxTransfer ? (
              <>
                <div className="row spread small" style={{ marginBottom: 10 }}>
                  <span>
                    Transfer <code>{selectedRxTransfer.id}</code> ·{" "}
                    {selectedRxTransfer.bytes} B
                  </span>
                  <span>
                    Expected SHA-1:{" "}
                    <code>{selectedRxTransfer.sha1.slice(0, 12)}…</code>
                  </span>
                </div>

                <div className="progress-track" style={{ marginBottom: 14 }}>
                  <div
                    className="progress-fill"
                    style={{
                      width: `${
                        selectedRxTransfer.frames
                          ? Math.min(
                              100,
                              Math.round(
                                (selectedRxTransfer.received /
                                  selectedRxTransfer.frames) *
                                  100
                              )
                            )
                          : 0
                      }%`,
                    }}
                  />
                </div>

                <div className="grid-3" style={{ marginBottom: 14 }}>
                  <div className="card-subtle" style={{ padding: 10 }}>
                    <div className="small">Accepted</div>
                    <div className="mono" style={{ fontSize: 16, fontWeight: 700 }}>
                      {selectedRxTransfer.received}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ padding: 10 }}>
                    <div className="small">Duplicates</div>
                    <div className="mono" style={{ fontSize: 16, fontWeight: 700 }}>
                      {selectedRxTransfer.duplicates}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ padding: 10 }}>
                    <div className="small">Failed</div>
                    <div className="mono" style={{ fontSize: 16, fontWeight: 700 }}>
                      {selectedRxTransfer.failed}
                    </div>
                  </div>
                </div>

                {selectedRxTransfer.complete ? (
                  <div className="callout ok">
                    <div className="row spread">
                      <div className="row" style={{ gap: 8 }}>
                        <CheckCircle2 size={18} color="#34d399" />
                        <strong style={{ color: "#fff" }}>
                          SHA-1 Verified Complete Payload
                        </strong>
                      </div>
                      <a
                        href={api.s2cPayloadUrl(selectedRxTransfer.id)}
                        download={selectedRxTransfer.name || `s2c-${selectedRxTransfer.id}.bin`}
                        className="btn success sm"
                      >
                        <Download size={14} /> Download Payload
                      </a>
                    </div>
                    {rxResult?.text && (
                      <pre className="terminal mt" style={{ maxHeight: 160 }}>
                        {rxResult.text}
                      </pre>
                    )}
                  </div>
                ) : (
                  <p className="small">
                    Point your camera or upload captures until all unique packet frames are received.
                    {selectedRxTransfer.last_error
                      ? ` Last note: ${selectedRxTransfer.last_error}`
                      : ""}
                  </p>
                )}
              </>
            ) : (
              <div className="empty-state">
                <Camera size={26} color="#64748b" style={{ marginBottom: 8 }} />
                <p className="small">Select an active transfer to inspect packet slots.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 3: SIMULATED LOOPBACK */}
      {tab === "loopback" && (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <form onSubmit={handleRunLoopback} className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <div>
                <h2>Synthetic Optical Loopback</h2>
                <p className="small" style={{ margin: 0 }}>
                  Verify end-to-end packetization, perspective warp, glare, pose detection, and FEC without a second device.
                </p>
              </div>
              <Sparkles size={18} color="#22d3ee" />
            </div>

            <label className="field">
              <span>Test Message</span>
              <textarea
                rows={3}
                value={loopText}
                onChange={(e) => setLoopText(e.target.value)}
              />
            </label>

            <div className="grid-3 mt">
              <label className="field">
                <span>Modulation</span>
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
                <span>FEC Code</span>
                <select value={ecc} onChange={(e) => setEcc(e.target.value)}>
                  {eccs.map((ec) => (
                    <option key={ec} value={ec}>
                      {ec.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>RNG Seed</span>
                <input
                  type="number"
                  value={loopSeed}
                  onChange={(e) => setLoopSeed(Number(e.target.value) || 42)}
                />
              </label>
            </div>

            <label className="field mt">
              <div className="row spread">
                <span>Simulated Camera Impairment Level</span>
                <code style={{ color: "var(--accent)" }}>{loopLevel.toFixed(2)}</code>
              </div>
              <input
                type="range"
                min={0}
                max={0.95}
                step={0.05}
                value={loopLevel}
                onChange={(e) => setLoopLevel(Number(e.target.value))}
              />
              <div className="row spread small">
                <span>0.0 Tripod Clean</span>
                <span>0.4 Handheld</span>
                <span>0.8 Heavy Tilt / Glare</span>
              </div>
            </label>

            <div className="mt" style={{ marginTop: 18 }}>
              <button type="submit" disabled={loopBusy} style={{ width: "100%" }}>
                <RefreshCw size={15} />
                {loopBusy ? "Simulating Optical Channel…" : "Run End-to-End Loopback"}
              </button>
            </div>
          </form>

          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>Loopback Telemetry</h2>
              {loopReport && (
                <span className={`pill ${loopReport.sha1_ok ? "ok" : "bad"}`}>
                  {loopReport.sha1_ok ? "SHA-1 Bit-Exact ✓" : "Incomplete"}
                </span>
              )}
            </div>

            {loopReport ? (
              <>
                <div className="grid-3" style={{ marginBottom: 14 }}>
                  <div className="card-subtle" style={{ padding: 10 }}>
                    <div className="small">Decoded Frames</div>
                    <div className="mono" style={{ fontSize: 18, fontWeight: 700 }}>
                      {loopReport.decoded}/{loopReport.total_frames}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ padding: 10 }}>
                    <div className="small">Corner Err Median</div>
                    <div className="mono" style={{ fontSize: 18, fontWeight: 700 }}>
                      {loopReport.corner_err_median != null
                        ? `${loopReport.corner_err_median.toFixed(2)} px`
                        : "—"}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ padding: 10 }}>
                    <div className="small">SHA-1 Verify</div>
                    <div
                      className="mono"
                      style={{
                        fontSize: 18,
                        fontWeight: 700,
                        color: loopReport.sha1_ok ? "#34d399" : "#f87171",
                      }}
                    >
                      {loopReport.sha1_ok ? "PASS" : "FAIL"}
                    </div>
                  </div>
                </div>

                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Frame</th>
                        <th>Status</th>
                        <th>Mode</th>
                        <th className="num">Corner Err</th>
                        <th className="num">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(loopReport.frames || []).map((f) => (
                        <tr key={f.frame}>
                          <td className="mono">#{f.frame}</td>
                          <td>
                            <span className={`pill ${f.ok ? "ok" : "bad"}`}>
                              {f.ok ? "ok ✓" : f.reason || "err"}
                            </span>
                          </td>
                          <td className="mono">{f.mode || "—"}</td>
                          <td className="num">
                            {f.corner_err != null ? `${f.corner_err.toFixed(2)}` : "—"}
                          </td>
                          <td className="num">{f.ms.toFixed(1)} ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="empty-state">
                <p className="small">
                  Run a simulated loopback on the left to measure frame-by-frame corner accuracy, latency, and SHA-1 verification.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Session Transfers Ledger */}
      {transfers.length > 0 && (
        <div className="card mt" style={{ marginTop: 18 }}>
          <div className="card-header">
            <h3>Session Transfer Ledger ({transfers.length})</h3>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Modulation / ECC</th>
                  <th>Finder</th>
                  <th className="num">Bytes</th>
                  <th className="num">Frames</th>
                  <th>Progress</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {transfers.slice(0, 10).map((t) => (
                  <tr key={t.id}>
                    <td className="mono">{t.id}</td>
                    <td>
                      <code>
                        {t.modulation}/{t.ecc}
                      </code>
                    </td>
                    <td>{t.finder || "none"}</td>
                    <td className="num">{t.bytes} B</td>
                    <td className="num">{t.frames}</td>
                    <td>
                      <span className={`pill ${t.complete ? "ok" : "run"}`}>
                        {t.received}/{t.frames} {t.complete ? "verified ✓" : "rx"}
                      </span>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => {
                            setActiveTransfer(t);
                            setFrameIdx(0);
                            setTab("tx");
                          }}
                        >
                          Display
                        </button>
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => {
                            setRxTransferId(t.id);
                            setTab("rx");
                          }}
                        >
                          Receive
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Cinema Fullscreen Overlay */}
      {cinema && activeTransfer && (
        <div className="cinema-overlay">
          <div className="cinema-stage">
            <img
              src={api.s2cFrameUrl(activeTransfer.id, frameIdx)}
              alt={`Fullscreen optical frame ${frameIdx + 1}`}
            />
          </div>
          <div className="cinema-hud">
            <div className="row" style={{ gap: 10 }}>
              <span className="pill ok">TX {activeTransfer.id}</span>
              <span className="mono small" style={{ color: "#cbd5e1" }}>
                Frame {frameIdx + 1}/{activeTransfer.frames} ·{" "}
                {activeTransfer.modulation}/{activeTransfer.ecc} · {fps} fps
              </span>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="secondary sm"
                onClick={() => setPlaying((p) => !p)}
              >
                {playing ? <Pause size={14} /> : <Play size={14} />}
                {playing ? "Pause" : "Play"}
              </button>
              <button
                type="button"
                className="sm"
                onClick={() => setCinema(false)}
              >
                <Minimize2 size={14} /> Exit Fullscreen (Esc)
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
