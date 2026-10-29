import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BarChart3, Play } from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { EvalBarsChart } from "../components/Visuals";
import type { EvalReport, JobSnapshot, RunSummary } from "../types";

export function Evaluate() {
  const { show } = useToast();
  const [searchParams] = useSearchParams();
  const initialRun = searchParams.get("run") || "yolo26n_optical";

  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>(initialRun);
  const [nFrames, setNFrames] = useState(24);
  const [levels, setLevels] = useState("0.2,0.5,0.8");
  const [report, setReport] = useState<EvalReport | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobSnap, setJobSnap] = useState<JobSnapshot | null>(null);
  const [busy, setBusy] = useState(false);

  const loadReport = useCallback((runName: string) => {
    if (!runName) return;
    api
      .getEvalReport(runName)
      .then(setReport)
      .catch(() => setReport(null));
  }, []);

  useEffect(() => {
    api
      .listRuns()
      .then((list) => {
        setRuns(list);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadReport(selectedRun);
  }, [selectedRun, loadReport]);

  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      try {
        const snap = await api.getJob(jobId, 40);
        setJobSnap(snap);
        if (!snap.live) {
          setBusy(false);
          loadReport(selectedRun);
        }
      } catch {
        // ignore
      }
    };
    poll();
    const t = setInterval(poll, 1500);
    return () => clearInterval(t);
  }, [jobId, selectedRun, loadReport]);

  const handleRunEval = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.evaluateRun(selectedRun, {
        n: nFrames,
        levels,
      });
      setJobId(res.job.id);
      show(`Started multi-level benchmark for ${selectedRun}`);
    } catch (err: any) {
      setBusy(false);
      show(err instanceof Error ? err.message : "Evaluation failed", true);
    }
  };

  const levelKeys = Object.keys(report?.levels || {}).sort(
    (a, b) => parseFloat(a) - parseFloat(b)
  );

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <BarChart3 size={13} /> Stage 4 · Multi-Noise Receiver Benchmark
          </div>
          <h1>Detector &amp; Demodulator Benchmark</h1>
          <p>
            Measure end-to-end packet decode rate, corner localization accuracy, and latency across Classical Contour Ring, YOLO26-Pose, and Hybrid Auto modes.
          </p>
        </div>
      </div>

      <form onSubmit={handleRunEval} className="card">
        <div className="grid-4" style={{ alignItems: "end" }}>
          <label className="field">
            <span>Checkpoint / Run</span>
            <select
              value={selectedRun}
              onChange={(e) => setSelectedRun(e.target.value)}
            >
              <option value="yolo26n_optical">yolo26n_optical (Shipped Baseline)</option>
              {runs.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name} {r.has_weights ? "✓" : "(no weights)"}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Frames per Noise Level</span>
            <input
              type="number"
              min={6}
              max={200}
              value={nFrames}
              onChange={(e) => setNFrames(Number(e.target.value) || 24)}
            />
          </label>

          <label className="field">
            <span>Noise Levels (CSV)</span>
            <input
              value={levels}
              onChange={(e) => setLevels(e.target.value)}
              placeholder="0.2,0.5,0.8"
            />
          </label>

          <button type="submit" disabled={busy}>
            <Play size={15} />
            {busy ? "Benchmarking…" : "Run Benchmark"}
          </button>
        </div>

        {jobSnap && jobSnap.live && (
          <div className="mt">
            <div className="row spread small" style={{ marginBottom: 4 }}>
              <span>{jobSnap.message || jobSnap.title}</span>
              <span className="mono">{Math.round((jobSnap.progress || 0) * 100)}%</span>
            </div>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${Math.round((jobSnap.progress || 0) * 100)}%` }}
              />
            </div>
          </div>
        )}
      </form>

      {report ? (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>End-to-End Packet Decode Rate</h2>
              <span className="pill ok">{selectedRun}</span>
            </div>
            <EvalBarsChart report={report} />
          </div>

          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>Detailed Telemetry Matrix</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Level</th>
                    <th>Mode</th>
                    <th className="num">Decode %</th>
                    <th className="num">Corner Err</th>
                    <th className="num">Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {levelKeys.flatMap((lvl) => {
                    const row = report.levels?.[lvl] || {};
                    return (["classical", "yolo", "auto"] as const).map((m) => {
                      const d = row[m];
                      if (!d) return null;
                      const cErr =
                        d.corner_err_pct_median ?? d.corner_err_median ?? null;
                      const lat = d.ms_mean ?? d.ms_per_frame ?? null;
                      return (
                        <tr key={`${lvl}-${m}`}>
                          <td className="mono">L={lvl}</td>
                          <td>
                            <span
                              className={`pill ${
                                m === "auto" ? "ok" : m === "yolo" ? "run" : ""
                              }`}
                            >
                              {m}
                            </span>
                          </td>
                          <td className="num">
                            {typeof d.decode_rate === "number"
                              ? `${(d.decode_rate * 100).toFixed(1)}%`
                              : "—"}
                          </td>
                          <td className="num">
                            {typeof cErr === "number"
                              ? `${cErr.toFixed(2)}%`
                              : "—"}
                          </td>
                          <td className="num">
                            {typeof lat === "number"
                              ? `${lat.toFixed(1)} ms`
                              : "—"}
                          </td>
                        </tr>
                      );
                    });
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : (
        <div className="card empty-state">
          <BarChart3 size={28} color="#22d3ee" style={{ marginBottom: 8 }} />
          <h3>No Benchmark Report for {selectedRun} Yet</h3>
          <p className="small">
            Click <strong>Run Benchmark</strong> above to evaluate Classical vs YOLO26 vs Hybrid Auto across synthetic channel noise levels.
          </p>
        </div>
      )}
    </div>
  );
}
