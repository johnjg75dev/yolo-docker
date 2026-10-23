import { useEffect, useState } from "react";

const KEY = "s2c.dataSaver";

export function Settings() {
  const [dataSaver, setDataSaver] = useState(false);
  const [maxDim, setMaxDim] = useState(1280);
  const [quality, setQuality] = useState(0.92);

  useEffect(() => {
    try {
      const v = localStorage.getItem(KEY);
      if (v) {
        const o = JSON.parse(v);
        setDataSaver(!!o.dataSaver);
        setMaxDim(o.maxDim || 1280);
        setQuality(o.quality || 0.92);
      }
    } catch (e) {}
  }, []);

  const save = () => {
    localStorage.setItem(KEY, JSON.stringify({ dataSaver, maxDim, quality }));
    alert("saved");
  };

  return (
    <div className="wrap">
      <h1>Settings</h1>
      <p className="small">Preferences for capture and upload.</p>

      <div className="card">
        <h2>Data saver</h2>
        <label className="field">
          <span>enable data saver (resize + compress before upload)</span>
          <input type="checkbox" checked={dataSaver}
                 onChange={(e) => setDataSaver(e.target.checked)} />
        </label>
        <label className="field mt">
          <span>max edge (px)</span>
          <input type="number" value={maxDim}
                 onChange={(e) => setMaxDim(Number(e.target.value))} />
        </label>
        <label className="field mt">
          <span>jpeg quality (0.1-1.0)</span>
          <input type="number" step="0.05" min="0.1" max="1" value={quality}
                 onChange={(e) => setQuality(Number(e.target.value))} />
        </label>
        <div className="mt">
          <button onClick={save}>save</button>
        </div>
      </div>

      <div className="card">
        <h2>About</h2>
        <p className="small">Screen2Camera optical modem PWA. Autonomous capture, data saver, and custom architectures supported.</p>
      </div>
    </div>
  );
}
