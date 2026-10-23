import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Home() {
  const [health, setHealth] = useState<any>(null);
  const { show } = useToast();

  useEffect(() => {
    api.health().then(setHealth).catch((e) => show(e.message, true));
  }, []);

  return (
    <div className="wrap">
      <h1>Screen2Camera Optical Modem</h1>
      <p className="small">Encode text into a video, display on screen, capture with camera, decode back.</p>

      <div className="card">
        <h2>Status</h2>
        {health ? (
          <div className="row">
            <span className="pill ok">healthy</span>
            <span className="mono small">{JSON.stringify(health)}</span>
          </div>
        ) : (
          <div className="pill run">checking…</div>
        )}
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h2>Quick start</h2>
          <p>1. Login with the shared password.</p>
          <p>2. Use the Wizard to encode a message.</p>
          <p>3. Display the video, capture with camera.</p>
          <p>4. Decode the captured image.</p>
        </div>
        <div className="card">
          <h2>Modules</h2>
          <ul className="small">
            <li><strong>Wizard</strong> — guided encode/decode flow</li>
            <li><strong>S2C</strong> — screen-to-camera transmit</li>
            <li><strong>Decode</strong> — decode captured images</li>
            <li><strong>Datasets</strong> — manage training data</li>
            <li><strong>Train</strong> — train YOLO finder model</li>
            <li><strong>Projects</strong> — organize work</li>
            <li><strong>Capture</strong> — camera capture station</li>
          </ul>
        </div>
      </div>
    </div>
  );
}