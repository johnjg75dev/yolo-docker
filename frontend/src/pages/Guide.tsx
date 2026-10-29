import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Compass, FlaskConical, Radio } from "lucide-react";
import { api } from "../api/client";
import { SwatchStrip } from "../components/Visuals";
import type { PaletteEntry } from "../types";

const ANATOMY_TOPICS = [
  {
    id: "ring",
    title: "1. High-Contrast Outer Sync Ring",
    detail:
      "A crisp black-and-white nested border enclosing the entire frame. In clean lighting, OpenCV contour hierarchy localizes this quad in ~2 ms; under heavy motion blur or specular glare, YOLO26-Pose predicts the 4 corners directly and refines them to sub-pixel precision.",
  },
  {
    id: "finders",
    title: "2. Four Asymmetric Corner Finders (TL, TR, BR, BL)",
    detail:
      "Three bold fiducial bullseyes (bullseye, square, diamond, or cross) plus a distinct bottom-right orientation marker resolve 90°/180°/270° rotational ambiguity before homography warp.",
  },
  {
    id: "pilots",
    title: "3. Calibrated Colour Reference Pilots",
    detail:
      "Known constellation swatches embedded inside the frame header teach the demodulator how your specific display gamut + camera white-balance transformed every RGB symbol on that exact exposure.",
  },
  {
    id: "lattice",
    title: "4. Reed-Solomon / LDPC Symbol Lattice",
    detail:
      "Each cell carries 1 to 5 bits across greyscale or colour constellations. Soft log-likelihood ratios (LLRs) feed either GF(256) Reed-Solomon or belief-propagation LDPC to repair corrupted cells without retransmission.",
  },
  {
    id: "crc",
    title: "5. Packet Header & End-to-End SHA-1",
    detail:
      "Every frame carries sequence metadata and a per-packet CRC-32 so out-of-order carousel captures reassemble cleanly and verify against the full payload SHA-1.",
  },
];

export function Guide() {
  const [activeTopic, setActiveTopic] = useState(ANATOMY_TOPICS[0]);
  const [mods, setMods] = useState<PaletteEntry[]>([]);

  useEffect(() => {
    api
      .s2cPalette(32, "rs32")
      .then((p) => setMods(p.modulations || []))
      .catch(() => {});
  }, []);

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <BookOpen size={13} /> Architecture &amp; Field Manual
          </div>
          <h1>Optical Frame Anatomy &amp; Protocol Guide</h1>
          <p>
            How Optical Modem turns any LCD/OLED screen and camera sensor into a calibrated, air-gapped digital communication channel.
          </p>
        </div>
        <div className="row">
          <Link to="/wizard" className="btn secondary">
            <Compass size={15} /> Guided Setup
          </Link>
          <Link to="/lab" className="btn">
            <FlaskConical size={15} /> Try in Channel Lab
          </Link>
        </div>
      </div>

      {/* Interactive Frame Anatomy */}
      <div className="grid-2" style={{ alignItems: "center", marginBottom: 16 }}>
        <div className="card" style={{ marginBottom: 0 }}>
          <h3 style={{ marginBottom: 10 }}>Click Any Frame Layer to Inspect</h3>
          <svg
            viewBox="0 0 340 340"
            width="100%"
            height="300"
            style={{
              background: "#05070c",
              borderRadius: 12,
              border: "1px solid var(--border)",
            }}
          >
            <rect
              x="22"
              y="22"
              width="296"
              height="296"
              rx="6"
              fill="#0b111b"
              stroke={activeTopic.id === "ring" ? "#22d3ee" : "#f8fafc"}
              strokeWidth="10"
              style={{ cursor: "pointer" }}
              onClick={() => setActiveTopic(ANATOMY_TOPICS[0])}
            />
            <rect
              x="36"
              y="36"
              width="268"
              height="268"
              fill="#090d16"
              stroke="#334155"
              strokeWidth="2"
            />

            {[
              [48, 48],
              [252, 48],
              [48, 252],
            ].map(([fx, fy], i) => (
              <g
                key={i}
                style={{ cursor: "pointer" }}
                onClick={() => setActiveTopic(ANATOMY_TOPICS[1])}
              >
                <rect
                  x={fx}
                  y={fy}
                  width="40"
                  height="40"
                  fill="none"
                  stroke={activeTopic.id === "finders" ? "#22d3ee" : "#e2e8f0"}
                  strokeWidth="5"
                />
                <rect
                  x={fx + 12}
                  y={fy + 12}
                  width="16"
                  height="16"
                  fill={activeTopic.id === "finders" ? "#22d3ee" : "#e2e8f0"}
                />
              </g>
            ))}
            <g
              style={{ cursor: "pointer" }}
              onClick={() => setActiveTopic(ANATOMY_TOPICS[1])}
            >
              <circle
                cx="272"
                cy="272"
                r="18"
                fill="none"
                stroke="#f59e0b"
                strokeWidth="5"
              />
              <circle cx="272" cy="272" r="6" fill="#f59e0b" />
            </g>

            <g
              style={{ cursor: "pointer" }}
              onClick={() => setActiveTopic(ANATOMY_TOPICS[2])}
            >
              {["#ef4444", "#22c55e", "#3b82f6", "#eab308", "#ec4899", "#06b6d4"].map(
                (col, i) => (
                  <rect
                    key={i}
                    x={102 + i * 23}
                    y="54"
                    width="19"
                    height="18"
                    rx="2"
                    fill={col}
                    stroke={activeTopic.id === "pilots" ? "#fff" : "none"}
                    strokeWidth="2"
                  />
                )
              )}
            </g>

            <g
              style={{ cursor: "pointer" }}
              onClick={() => setActiveTopic(ANATOMY_TOPICS[4])}
            >
              <rect
                x="102"
                y="80"
                width="136"
                height="16"
                rx="3"
                fill="rgba(139, 92, 246, 0.35)"
                stroke={activeTopic.id === "crc" ? "#c4b5fd" : "#6d28d9"}
              />
              <text
                x="170"
                y="91"
                textAnchor="middle"
                fill="#ede9fe"
                fontSize="8.5"
                fontFamily="var(--font-mono)"
              >
                PKT #0 · CRC32 · FEC PARITY
              </text>
            </g>

            <g
              style={{ cursor: "pointer" }}
              onClick={() => setActiveTopic(ANATOMY_TOPICS[3])}
            >
              {Array.from({ length: 7 }).map((_, r) =>
                Array.from({ length: 9 }).map((__, c) => {
                  const palette = [
                    "#22d3ee",
                    "#10b981",
                    "#3b82f6",
                    "#f59e0b",
                    "#ec4899",
                    "#a855f7",
                  ];
                  return (
                    <rect
                      key={`${r}-${c}`}
                      x={62 + c * 24}
                      y={106 + r * 20}
                      width="20"
                      height="16"
                      rx="2"
                      fill={palette[(r * 5 + c * 3) % palette.length]}
                      opacity={activeTopic.id === "lattice" ? 1 : 0.8}
                    />
                  );
                })
              )}
            </g>
          </svg>
        </div>

        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>Physical Layer Breakdown</h2>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {ANATOMY_TOPICS.map((t) => {
              const sel = t.id === activeTopic.id;
              return (
                <div
                  key={t.id}
                  onClick={() => setActiveTopic(t)}
                  className="card-subtle"
                  style={{
                    cursor: "pointer",
                    borderColor: sel ? "var(--accent)" : "var(--border)",
                    background: sel
                      ? "rgba(34, 211, 238, 0.08)"
                      : "var(--inset)",
                  }}
                >
                  <strong style={{ color: sel ? "var(--accent)" : "var(--fg)" }}>
                    {t.title}
                  </strong>
                  {sel && (
                    <p className="small" style={{ marginTop: 6, marginBottom: 0 }}>
                      {t.detail}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Modulation Constellations Reference Table */}
      {mods.length > 0 && (
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Supported Modulation Constellations ({mods.length})</h2>
              <p className="small" style={{ margin: 0 }}>
                Lower-order constellations maximize robustness across glare and motion; higher-order constellations maximize byte throughput on crisp displays.
              </p>
            </div>
            <Link to="/s2c" className="btn sm">
              <Radio size={14} /> Use in S2C
            </Link>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Preset</th>
                  <th>Palette</th>
                  <th className="num">Bits/Sym</th>
                  <th className="num">Colours</th>
                  <th>Default ECC</th>
                  <th className="num">Max Bytes/Frame</th>
                </tr>
              </thead>
              <tbody>
                {mods.map((m) => (
                  <tr key={m.name}>
                    <td>
                      <strong className="mono">{m.name}</strong>
                    </td>
                    <td>
                      <SwatchStrip hexes={m.hex || []} max={14} />
                    </td>
                    <td className="num">{m.bits}</td>
                    <td className="num">{m.colours}</td>
                    <td>
                      <span className="pill">{(m.ecc || "rs32").toUpperCase()}</span>
                    </td>
                    <td className="num">{m.max_user_payload} B</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Field Shooting Best Practices */}
      <div className="grid-3">
        <div className="card" style={{ marginBottom: 0 }}>
          <h3>1. Display Setup</h3>
          <p className="small" style={{ margin: 0 }}>
            Use <strong>Cinema Fullscreen</strong> in S2C at 70–90% brightness and disable OS night-shift / warm colour filters when using high-order colour constellations.
          </p>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <h3>2. Camera Framing</h3>
          <p className="small" style={{ margin: 0 }}>
            Let the outer sync ring fill ~60–80% of the camera viewfinder. Slight off-axis angle (10–15°) avoids overhead room-light glare on glossy panels.
          </p>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <h3>3. Domain Fine-Tuning</h3>
          <p className="small" style={{ margin: 0 }}>
            If you deploy on an unusual display (projector, e-ink, curved ultrawide), shoot 30 frames in <strong>Capture Station</strong>, merge with synthetic data, and fine-tune for 15 epochs.
          </p>
        </div>
      </div>
    </div>
  );
}
