import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Camera,
  Database,
  ExternalLink,
  Monitor,
  Plus,
  QrCode,
  RotateCcw,
  SkipForward,
  Smartphone,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

interface SessionRow {
  id: string;
  label: string;
  created?: number;
  index: number;
  count: number;
  status: string;
  accepted: number;
  resolution: string;
  code?: string;
}

export function Capture() {
  const { show } = useToast();
  const navigate = useNavigate();
  const [caps, setCaps] = useState<any>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [selectedSid, setSelectedSid] = useState<string>("");
  const [activeDetail, setActiveDetail] = useState<any>(null);

  // New session form
  const [label, setLabel] = useState("desk-1080p-calibration");
  const [count, setCount] = useState(30);
  const [resolution, setResolution] = useState("1280x720");
  const [selMods, setSelMods] = useState<string[]>(["rgb8", "rgb4"]);
  const [selEccs, setSelEccs] = useState<string[]>(["rs32"]);
  const [busy, setBusy] = useState(false);

  const [importName, setImportName] = useState("captured-optical");

  const load = useCallback(async () => {
    try {
      const list = await api.listCaptureSessions();
      setSessions(list);
      if (!selectedSid && list.length > 0) {
        setSelectedSid(list[0].id);
      }
    } catch {
      // ignore
    }
  }, [selectedSid]);

  useEffect(() => {
    api.captureCapabilities().then(setCaps).catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!selectedSid) {
      setActiveDetail(null);
      return;
    }
    api
      .getCaptureSession(selectedSid)
      .then(setActiveDetail)
      .catch(() => {});
  }, [selectedSid, sessions]);

  const toggleItem = (
    arr: string[],
    item: string,
    setter: (v: string[]) => void
  ) => {
    if (arr.includes(item)) {
      if (arr.length > 1) setter(arr.filter((x) => x !== item));
    } else {
      setter([...arr, item]);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const s = await api.createCaptureSession({
        label: label.trim() || "optical-capture",
        count,
        resolution,
        modulations: selMods,
        eccs: selEccs,
      });
      setSelectedSid(s.id);
      setActiveDetail(s);
      await load();
      show(`Capture session ${s.id} ready (code ${s.code || "—"})`);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Failed to create session", true);
    } finally {
      setBusy(false);
    }
  };

  const handleUndo = async (sid: string) => {
    try {
      await api.sessionUndoShot(sid);
      show("Rejected last shot");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Nothing to undo", true);
    }
  };

  const handleSkip = async (sid: string) => {
    try {
      await api.sessionSkipFrame(sid);
      show("Skipped current frame");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Cannot skip", true);
    }
  };

  const handleImportDataset = async () => {
    setBusy(true);
    try {
      const res = await api.capturesDataset({
        name: importName.trim() || "captures",
        val_frac: 0.2,
      });
      show(`Started capture dataset build: ${res.job.id}`);
      navigate("/jobs");
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Import failed", true);
    } finally {
      setBusy(false);
    }
  };

  const active =
    sessions.find((s) => s.id === selectedSid) || sessions[0] || null;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const displayUrl = active ? `${origin}/display?session=${active.id}` : "";
  const cameraUrl = active
    ? `${origin}/camera?session=${active.id}${
        activeDetail?.code ? `&code=${activeDetail.code}` : ""
      }`
    : "";

  const availMods: string[] = caps?.modulations || ["rgb8", "rgb4", "gray4"];
  const availEccs: string[] = caps?.eccs || ["rs32", "rs16", "ldpc12"];

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Camera size={13} /> Real-World Screen &amp; Sensor Calibration
          </div>
          <h1>Paired Capture Station</h1>
          <p>
            Coordinate a screen display and a smartphone camera to collect real optical shots with automatic classical-ring pose pre-labelling.
          </p>
        </div>
        {active && (
          <div className="row">
            <a
              href={`/display?session=${active.id}`}
              target="_blank"
              rel="noreferrer"
              className="btn secondary"
            >
              <Monitor size={15} /> Open Display <ExternalLink size={13} />
            </a>
            <a
              href={`/camera?session=${active.id}`}
              target="_blank"
              rel="noreferrer"
              className="btn"
            >
              <Smartphone size={15} /> Open Camera <ExternalLink size={13} />
            </a>
          </div>
        )}
      </div>

      <div className="grid-2" style={{ alignItems: "start" }}>
        {/* Create New Capture Session */}
        <form onSubmit={handleCreate} className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>New Capture Plan</h2>
            <span className="pill run">{count} target shots</span>
          </div>

          <div className="grid-3">
            <label className="field">
              <span>Session Label</span>
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. oled-phone-angled"
              />
            </label>
            <label className="field">
              <span>Shot Count</span>
              <input
                type="number"
                min={1}
                max={2000}
                value={count}
                onChange={(e) =>
                  setCount(Math.max(1, Number(e.target.value) || 20))
                }
              />
            </label>
            <label className="field">
              <span>Display Resolution</span>
              <select
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
              >
                <option value="1280x720">1280×720</option>
                <option value="1920x1080">1920×1080</option>
                <option value="2560x1440">2560×1440</option>
              </select>
            </label>
          </div>

          <div className="mt">
            <span className="small" style={{ display: "block", marginBottom: 6 }}>
              Modulations to Cycle
            </span>
            <div className="row" style={{ gap: 6 }}>
              {availMods.map((m: string) => {
                const on = selMods.includes(m);
                return (
                  <button
                    key={m}
                    type="button"
                    className={`sm ${on ? "" : "secondary"}`}
                    onClick={() => toggleItem(selMods, m, setSelMods)}
                  >
                    {m}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt">
            <span className="small" style={{ display: "block", marginBottom: 6 }}>
              FEC Schemes to Cycle
            </span>
            <div className="row" style={{ gap: 6 }}>
              {availEccs.map((ec: string) => {
                const on = selEccs.includes(ec);
                return (
                  <button
                    key={ec}
                    type="button"
                    className={`sm ${on ? "" : "secondary"}`}
                    onClick={() => toggleItem(selEccs, ec, setSelEccs)}
                  >
                    {ec}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt" style={{ marginTop: 18 }}>
            <button type="submit" disabled={busy} style={{ width: "100%" }}>
              <Plus size={15} />
              {busy ? "Creating…" : "Create Paired Capture Session"}
            </button>
          </div>
        </form>

        {/* Active Session Pairing & Controls */}
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>Device Pairing &amp; Telemetry</h2>
            {active && (
              <span
                className={`pill ${active.status === "done" ? "ok" : "run"}`}
              >
                {active.accepted}/{active.count} accepted
              </span>
            )}
          </div>

          {active ? (
            <>
              <div className="row spread small" style={{ marginBottom: 8 }}>
                <span>
                  Session <code>{active.id}</code> ({active.label})
                </span>
                {activeDetail?.code && (
                  <span>
                    Pair Code:{" "}
                    <code style={{ color: "var(--accent)", fontWeight: 700 }}>
                      {activeDetail.code}
                    </code>
                  </span>
                )}
              </div>
              <div className="progress-track" style={{ marginBottom: 14 }}>
                <div
                  className="progress-fill"
                  style={{
                    width: `${
                      active.count
                        ? Math.round((active.index / active.count) * 100)
                        : 0
                    }%`,
                  }}
                />
              </div>

              <div className="grid-2" style={{ gap: 12, marginBottom: 14 }}>
                <div className="card-subtle" style={{ textAlign: "center" }}>
                  <div
                    className="row"
                    style={{ justifyContent: "center", gap: 6, marginBottom: 6 }}
                  >
                    <QrCode size={14} color="#22d3ee" />
                    <strong style={{ fontSize: 12 }}>1. Laptop Display</strong>
                  </div>
                  <img
                    crossOrigin="use-credentials" src={api.qrSvgUrl(displayUrl)}
                    alt="Display QR"
                    style={{
                      width: 112,
                      height: 112,
                      background: "#fff",
                      padding: 6,
                      borderRadius: 8,
                    }}
                  />
                  <div className="mt">
                    <a
                      href={`/display?session=${active.id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="btn secondary sm"
                      style={{ width: "100%" }}
                    >
                      Launch /display
                    </a>
                  </div>
                </div>

                <div className="card-subtle" style={{ textAlign: "center" }}>
                  <div
                    className="row"
                    style={{ justifyContent: "center", gap: 6, marginBottom: 6 }}
                  >
                    <Smartphone size={14} color="#10b981" />
                    <strong style={{ fontSize: 12 }}>2. Phone Camera</strong>
                  </div>
                  <img
                    crossOrigin="use-credentials" src={api.qrSvgUrl(cameraUrl)}
                    alt="Camera QR"
                    style={{
                      width: 112,
                      height: 112,
                      background: "#fff",
                      padding: 6,
                      borderRadius: 8,
                    }}
                  />
                  <div className="mt">
                    <a
                      href={`/camera?session=${active.id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="btn sm"
                      style={{ width: "100%" }}
                    >
                      Launch /camera
                    </a>
                  </div>
                </div>
              </div>

              <div className="row spread">
                <div className="row" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={() => handleUndo(active.id)}
                  >
                    <RotateCcw size={14} /> Undo Shot
                  </button>
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={() => handleSkip(active.id)}
                  >
                    <SkipForward size={14} /> Skip Frame
                  </button>
                </div>

                {active.accepted > 0 && (
                  <div className="row" style={{ gap: 6 }}>
                    <input
                      value={importName}
                      onChange={(e) => setImportName(e.target.value)}
                      style={{ width: 135, padding: "5px 8px", fontSize: 12 }}
                      placeholder="dataset name"
                    />
                    <button
                      type="button"
                      className="success sm"
                      disabled={busy}
                      onClick={handleImportDataset}
                    >
                      <Database size={14} /> Build Dataset
                    </button>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="empty-state">
              <Camera size={28} color="#22d3ee" style={{ marginBottom: 8 }} />
              <h3>No Capture Sessions Yet</h3>
              <p className="small">
                Create a session on the left to generate paired QR codes for your display and smartphone camera.
              </p>
            </div>
          )}
        </div>
      </div>

      {sessions.length > 0 && (
        <div className="card mt" style={{ marginTop: 18 }}>
          <div className="card-header">
            <h3>All Capture Sessions ({sessions.length})</h3>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Session ID</th>
                  <th>Label</th>
                  <th>Resolution</th>
                  <th>Status</th>
                  <th className="num">Accepted / Plan</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td className="mono">{s.id}</td>
                    <td>{s.label}</td>
                    <td className="mono">{s.resolution}</td>
                    <td>
                      <span
                        className={`pill ${s.status === "done" ? "ok" : "run"}`}
                      >
                        {s.status}
                      </span>
                    </td>
                    <td className="num">
                      {s.accepted} / {s.count}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="secondary sm"
                        onClick={() => setSelectedSid(s.id)}
                      >
                        Select &amp; Pair
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
