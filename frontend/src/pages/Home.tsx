import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Home() {
  const [health, setHealth] = useState<any>(null);
  const [runs, setRuns] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const { show } = useToast();

  useEffect(() => {
    api.health().then(setHealth).catch((e) => show(e.message, true));
    api.listRuns().then(setRuns).catch(() => {});
    api.listDatasets().then(setDatasets).catch(() => {});
    api.listProjects().then(setProjects).catch(() => {});
  }, []);

  const cards = [
    { to: "/wizard", title: "Wizard", desc: "Guided encode -> display -> capture -> decode", color: "#4f8cff" },
    { to: "/s2c", title: "S2C", desc: "Screen-to-camera transmit & receive", color: "#7c5cff" },
    { to: "/datasets", title: "Datasets", desc: datasets.length + " datasets", color: "#2ec27e" },
    { to: "/train", title: "Train", desc: runs.length + " runs", color: "#ffb347" },
    { to: "/projects", title: "Projects", desc: projects.length + " projects", color: "#ff5d5d" },
    { to: "/capture", title: "Capture", desc: "Camera capture station", color: "#4f8cff" },
    { to: "/settings", title: "Settings", desc: "Data saver, preferences", color: "#8b92a1" },
  ];

  return (
    <div className="wrap">
      <div style={{ marginBottom: 20 }}>
        <h1>Screen2Camera Optical Modem</h1>
        <p className="small">Encode text into a video, display on screen, capture with camera, decode back.</p>
      </div>

      <div className="card" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span className={"pill " + (health ? "ok" : "run")}>{health ? "healthy" : "checking..."}</span>
        {health && (
          <span className="mono small" style={{ color: "var(--muted)" }}>
            {health.service || "suite"} - build {health.build || "?"}
          </span>
        )}
        <span style={{ flex: 1 }} />
      </div>

      <div className="grid grid-3" style={{ marginTop: 16 }}>
        {cards.map((c) => (
          <Link key={c.to} to={c.to} style={{ textDecoration: "none" }}>
            <div className="card" style={{
              margin: 0, borderLeft: "4px solid " + c.color,
              transition: "transform 0.15s, box-shadow 0.15s", cursor: "pointer",
            }}>
              <h2 style={{ color: c.color }}>{c.title}</h2>
              <p className="small">{c.desc}</p>
            </div>
          </Link>
        ))}
      </div>

      {runs.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Recent training runs</h2>
          <table>
            <thead>
              <tr><th>Name</th><th>Status</th><th>Epoch</th><th>mAP</th></tr>
            </thead>
            <tbody>
              {runs.slice(0, 5).map((r: any) => (
                <tr key={r.id || r.name}>
                  <td>{r.name}</td>
                  <td>
                    <span className={"pill " + (r.status === "done" ? "ok" : r.status === "running" ? "run" : "warn")}>
                      {r.status}
                    </span>
                  </td>
                  <td>{r.epoch}/{r.epochs}</td>
                  <td>{typeof r.mAP === "number" ? r.mAP.toFixed(3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
