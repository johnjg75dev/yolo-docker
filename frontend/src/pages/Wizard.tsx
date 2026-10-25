import { useState } from "react";
import { Link } from "react-router-dom";

const STEPS = [
  { title: "Encode", description: "Enter your message and encode it into a video." },
  { title: "Display", description: "Play the video on your screen at full brightness." },
  { title: "Capture", description: "Use a camera to capture the screen." },
  { title: "Decode", description: "Upload the captured image to decode the message." },
];

export function Wizard() {
  const [step, setStep] = useState(0);

  const current = STEPS[step];

  return (
    <div className="wrap">
      <h1>Wizard</h1>
      <p className="small">Guided flow for screen-to-camera communication.</p>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          {STEPS.map((s, i) => (
            <span key={i} className={`pill ${i === step ? "run" : i < step ? "ok" : ""}`}>
              {i + 1}. {s.title}
            </span>
          ))}
        </div>
        <h2>{current.title}</h2>
        <p>{current.description}</p>

        <div className="row mt">
          {step > 0 && (
            <button className="secondary" onClick={() => setStep(step - 1)}>
              back
            </button>
          )}
          {step < STEPS.length - 1 ? (
            <button onClick={() => setStep(step + 1)}>next</button>
          ) : (
            <Link to="/s2c">
              <button>go to decode</button>
            </Link>
          )}
        </div>
      </div>

      <div className="card">
        <h3>Quick links</h3>
        <div className="row">
          <Link to="/s2c"><button className="secondary">S2C</button></Link>
          <Link to="/s2c"><button className="secondary">Decode</button></Link>
          <Link to="/datasets"><button className="secondary">Datasets</button></Link>
        </div>
      </div>
    </div>
  );
}