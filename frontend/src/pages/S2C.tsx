import { useState, useEffect } from "react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function S2C() {
  const [txText, setTxText] = useState("");
  const [modulation, setModulation] = useState("rgb8");
  const [ecc, setEcc] = useState("rs32");
  const [resolution, setResolution] = useState("1280x720");
  const [symbolPx, setSymbolPx] = useState("16");
  const [repeat, setRepeat] = useState("2");
  const [fps, setFps] = useState("3");
  const [finder, setFinder] = useState("square");
  const [options, setOptions] = useState<any>(null);
  const [txResult, setTxResult] = useState<any>(null);
  const [txLoading, setTxLoading] = useState(false);

  const [rxFile, setRxFile] = useState<File | null>(null);
  const [rxTid, setRxTid] = useState("");
  const [rxResult, setRxResult] = useState<{ ok: boolean; complete: boolean } | null>(null);
  const [rxLoading, setRxLoading] = useState(false);

  const { show } = useToast();

  useEffect(() => {
    api.s2cOptions().then(setOptions).catch((e) => show(e.message, true));
  }, []);

  const transmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!txText.trim()) { show("enter text to transmit", true); return; }
    setTxLoading(true);
    try {
      const r = await api.s2cTransmit({
        text: txText,
        modulation,
        ecc,
        resolution,
        symbol_px: symbolPx,
        repeat,
        fps,
        finder,
      });
      setTxResult(r);
      show("transmitted — " + r.transfer.id);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "transmit failed", true);
    } finally {
      setTxLoading(false);
    }
  };

  const receive = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rxFile || !rxTid.trim()) { show("select file and enter transfer id", true); return; }
    setRxLoading(true);
    try {
      const r = await api.s2cReceive(rxTid.trim(), rxFile);
      const result = r.result;
      setRxResult({ ok: result.ok, complete: result.complete });
      if (result.complete) {
        show("transfer complete — payload ready to download");
      }
    } catch (e: any) {
      show(e instanceof Error ? e.message : "receive failed", true);
    } finally {
      setRxLoading(false);
    }
  };

  return (
    <div className="wrap">
      <h1>Screen-to-Camera (S2C)</h1>
      <p className="small">Encode text into a video, display on screen, capture with camera, decode back.</p>

      <div className="card">
        <h2>Transmit</h2>
        <form onSubmit={transmit}>
          <label className="field">
            <span>text</span>
            <textarea value={txText} onChange={(e) => setTxText(e.target.value)} placeholder="Enter text to transmit…" />
          </label>
          <div className="row mt">
            <label className="field grow">
              <span>modulation</span>
              <select value={modulation} onChange={(e) => setModulation(e.target.value)}>
                {(options?.modulations || ["rgb8"]).map((m: string) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </label>
            <label className="field grow">
              <span>ecc</span>
              <select value={ecc} onChange={(e) => setEcc(e.target.value)}>
                {(options?.eccs || ["rs32"]).map((e: string) => (
                  <option key={e} value={e}>{e}</option>
                ))}
              </select>
            </label>
            <label className="field grow">
              <span>resolution</span>
              <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
                {(options?.resolutions || ["1280x720"]).map((r: string) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="row mt">
            <label className="field grow">
              <span>symbol px</span>
              <input type="number" value={symbolPx} onChange={(e) => setSymbolPx(e.target.value)} min="4" max="64" />
            </label>
            <label className="field grow">
              <span>repeat</span>
              <input type="number" value={repeat} onChange={(e) => setRepeat(e.target.value)} min="1" max="10" />
            </label>
            <label className="field grow">
              <span>fps</span>
              <input type="number" value={fps} onChange={(e) => setFps(e.target.value)} min="1" max="30" />
            </label>
            <label className="field grow">
              <span>finder</span>
              <select value={finder} onChange={(e) => setFinder(e.target.value)}>
                {(options?.finder_shapes || [{ name: "square" }]).map((f: any) => (
                  <option key={f.name} value={f.name}>{f.name}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt">
            <button type="submit" disabled={txLoading}>
              {txLoading ? "transmitting…" : "transmit"}
            </button>
          </div>
        </form>

        {txResult && (
          <div className="mt">
            <span className="pill ok">transfer created</span>
            <p className="mono small mt">id: {txResult.transfer.id}</p>
            <p className="small">frames: {txResult.transfer.frames} · bytes: {txResult.transfer.bytes}</p>
            <a href={api.s2cDownload(txResult.transfer.id)} target="_blank" rel="noreferrer">
              download video
            </a>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Receive</h2>
        <form onSubmit={receive}>
          <label className="field">
            <span>transfer id</span>
            <input value={rxTid} onChange={(e) => setRxTid(e.target.value)} placeholder="paste transfer id…" />
          </label>
          <label className="field mt">
            <span>captured image</span>
            <input type="file" accept="image/*" onChange={(e) => setRxFile(e.target.files?.[0] || null)} />
          </label>
          <div className="mt">
            <button type="submit" disabled={rxLoading || !rxFile || !rxTid.trim()}>
              {rxLoading ? "receiving…" : "receive"}
            </button>
          </div>
        </form>
        {rxResult && (
          <div className="mt">
            <span className={`pill ${rxResult.complete ? "ok" : "warn"}`}>
              {rxResult.complete ? "complete" : "partial"}
            </span>
            <p className="small mt">ok: {String(rxResult.ok)}</p>
          </div>
        )}
      </div>
    </div>
  );
}