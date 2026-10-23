export function Capture() {
  return (
    <div className="wrap">
      <h1>Capture</h1>
      <p className="small">Camera capture station for S2C decoding.</p>

      <div className="card">
        <h2>Capture station</h2>
        <p>The capture station runs as part of this server. Open the display or camera pages directly:</p>
        <div className="row mt">
          <a href="/display" target="_blank" rel="noreferrer">
            <button>Display (show codes)</button>
          </a>
          <a href="/camera" target="_blank" rel="noreferrer">
            <button>Camera (capture codes)</button>
          </a>
        </div>
      </div>

      <div className="card">
        <h2>Usage</h2>
        <ol className="small">
          <li>Open the <strong>Display</strong> page on the laptop showing the encoded video.</li>
          <li>Open the <strong>Camera</strong> page on the phone to photograph the screen.</li>
          <li>Upload captured images via the S2C page to decode.</li>
        </ol>
      </div>
    </div>
  );
}