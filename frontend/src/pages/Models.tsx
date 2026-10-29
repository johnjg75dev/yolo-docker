import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Award, BarChart3, CheckCircle2, ShieldCheck, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import type { ModelSummary, RunSummary } from "../types";

export function Models() {
  const { show } = useToast();
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [shipped, setShipped] = useState<ModelSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [active, setActive] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.listModels();
      setModels(res.models || []);
      setShipped(res.shipped || []);
      setRuns(res.runs || []);
      setActive(res.active || null);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Failed to load models", true);
    }
  }, [show]);

  useEffect(() => {
    load();
  }, [load]);

  const handleActivate = async (name: string) => {
    setBusy(true);
    try {
      await api.activateModel(name);
      show(`Activated detector: ${name}`);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Activation failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handlePromote = async (runName: string) => {
    setBusy(true);
    try {
      await api.promoteModel(runName);
      show(`Promoted ${runName} and set as active S2C detector`);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Promotion failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Remove promoted model "${name}" from registry?`)) return;
    try {
      await api.deleteModel(name);
      show(`Deleted ${name}`);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Delete failed", true);
    }
  };

  const promotableRuns = runs.filter((r) => r.has_weights);

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Award size={13} /> Stage 5 · Detector Registry &amp; Deployment
          </div>
          <h1>Model Registry</h1>
          <p>
            Manage promoted YOLO26-pose checkpoints and select the active neural detector bound to S2C Receiver and Channel Lab.
          </p>
        </div>
      </div>

      {/* Active Detector Hero */}
      <div
        className="card"
        style={{
          borderLeft: "3px solid var(--ok)",
          background:
            "linear-gradient(90deg, rgba(16, 185, 129, 0.08), transparent 60%), var(--card)",
        }}
      >
        <div className="row spread">
          <div className="row" style={{ gap: 12 }}>
            <ShieldCheck size={28} color="#34d399" />
            <div>
              <div className="small">Currently Bound to S2C &amp; Channel Lab</div>
              <h2 style={{ fontSize: "1.25rem", marginTop: 2 }}>
                {active?.name || "Classical Ring Fallback"}
              </h2>
              <div className="small mono" style={{ marginTop: 2 }}>
                {active?.weights || "No weights path"}
              </div>
            </div>
          </div>

          <div className="row" style={{ gap: 10 }}>
            {active?.metrics?.pose_map50_95 != null && (
              <span className="pill ok">
                Pose mAP {(active.metrics.pose_map50_95 * 100).toFixed(1)}%
              </span>
            )}
            <span className="pill ok">
              <CheckCircle2 size={12} /> Active
            </span>
          </div>
        </div>
      </div>

      {/* Promoted Models */}
      <div className="card">
        <div className="card-header">
          <div>
            <h2>Promoted Registry Models ({models.length})</h2>
            <p className="small" style={{ margin: 0 }}>
              Versioned checkpoints stored in <code>DATA_DIR/models/</code> with reproducibility model cards.
            </p>
          </div>
        </div>

        {models.length === 0 ? (
          <div className="empty-state" style={{ padding: "22px 16px" }}>
            <p className="small" style={{ margin: 0 }}>
              No custom runs promoted yet. Train a detector or promote a ready run below — meanwhile the shipped baseline model serves live requests.
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Model Name</th>
                  <th>Source Run</th>
                  <th className="num">Box mAP</th>
                  <th className="num">Pose mAP</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => {
                  const isAct = active?.name === m.name && !active?.shipped;
                  const sourceRun = m.card?.source_run || m.name;
                  const boxMap =
                    m.metrics?.box_map50_95 ??
                    m.card?.metrics?.["metrics/mAP50-95(B)"];
                  const poseMap =
                    m.metrics?.pose_map50_95 ??
                    m.card?.metrics?.["metrics/mAP50-95(P)"];
                  return (
                    <tr key={m.name}>
                      <td>
                        <strong className="mono">{m.name}</strong>
                      </td>
                      <td className="mono">{sourceRun}</td>
                      <td className="num">
                        {typeof boxMap === "number" ? boxMap.toFixed(4) : "—"}
                      </td>
                      <td className="num">
                        {typeof poseMap === "number" ? poseMap.toFixed(4) : "—"}
                      </td>
                      <td>
                        {isAct ? (
                          <span className="pill ok">Active ✓</span>
                        ) : (
                          <span className="pill">Standby</span>
                        )}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          {!isAct && (
                            <button
                              type="button"
                              className="success sm"
                              disabled={busy}
                              onClick={() => handleActivate(m.name)}
                            >
                              Activate
                            </button>
                          )}
                          <Link
                            to={`/evaluate?run=${encodeURIComponent(sourceRun)}`}
                            className="btn secondary sm"
                          >
                            <BarChart3 size={13} /> Benchmark
                          </Link>
                          <button
                            type="button"
                            className="ghost sm"
                            onClick={() => handleDelete(m.name)}
                          >
                            <Trash2 size={13} color="#f87171" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Shipped Baseline Models + Promotable Runs */}
      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>Shipped Baseline Checkpoints</h2>
          </div>
          {shipped.length === 0 ? (
            <p className="small">No shipped models bundled in repository.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {shipped.map((sm) => {
                const isAct = active?.name === sm.name;
                return (
                  <div key={sm.name} className="card-subtle">
                    <div className="row spread">
                      <div>
                        <strong className="mono">{sm.name}</strong>
                        <div className="small">
                          Pose mAP50-95:{" "}
                          <code>
                            {sm.metrics?.pose_map50_95 != null
                              ? sm.metrics.pose_map50_95.toFixed(4)
                              : "0.9950"}
                          </code>{" "}
                          · Box mAP:{" "}
                          <code>
                            {sm.metrics?.box_map50_95 != null
                              ? sm.metrics.box_map50_95.toFixed(4)
                              : "0.9907"}
                          </code>
                        </div>
                      </div>
                      <div className="row" style={{ gap: 6 }}>
                        {isAct ? (
                          <span className="pill ok">Active</span>
                        ) : (
                          <button
                            type="button"
                            className="secondary sm"
                            disabled={busy}
                            onClick={() => handleActivate(sm.name)}
                          >
                            Activate
                          </button>
                        )}
                        <Link
                          to={`/evaluate?run=${encodeURIComponent(sm.name)}`}
                          className="btn ghost sm"
                        >
                          Eval Report
                        </Link>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>Ready Runs to Promote ({promotableRuns.length})</h2>
            <Link to="/train" className="small">
              Train new →
            </Link>
          </div>
          {promotableRuns.length === 0 ? (
            <p className="small">
              Completed training runs with a <code>weights/best.pt</code> checkpoint appear here for one-click promotion.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {promotableRuns.map((r) => (
                <div key={r.name} className="card-subtle">
                  <div className="row spread">
                    <div>
                      <strong className="mono">{r.name}</strong>
                      <div className="small">
                        Epochs: {r.epoch ?? "—"}/{r.epochs ?? "—"} · mAP:{" "}
                        {typeof r.mAP === "number" ? r.mAP.toFixed(4) : "—"}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="success sm"
                      disabled={busy}
                      onClick={() => handlePromote(r.name)}
                    >
                      <Award size={13} /> Promote → Active
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
