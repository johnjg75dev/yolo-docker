import { useState } from "react";
import type { CurveData, EvalReport, ProgressStage } from "../types";

export function ReadinessRing({
  percent,
  sub,
  size = 108,
}: {
  percent: number;
  sub?: string;
  size?: number;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent || 0)));
  const stroke = 8;
  const r = (size - stroke) / 2 - 2;
  const c = 2 * Math.PI * r;
  const filled = (clamped / 100) * c;

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`${clamped}% complete`}
      style={{ flexShrink: 0 }}
    >
      <defs>
        <linearGradient id="omRingGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="100%" stopColor="#10b981" />
        </linearGradient>
      </defs>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="#1e293b"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="url(#omRingGrad)"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${filled.toFixed(1)} ${c.toFixed(1)}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray 0.45s ease" }}
      />
      <text
        x="50%"
        y={sub ? "46%" : "53%"}
        textAnchor="middle"
        fill="#f1f5f9"
        fontSize={size * 0.23}
        fontWeight={700}
        fontFamily="var(--font-mono)"
      >
        {clamped}%
      </text>
      {sub && (
        <text
          x="50%"
          y="67%"
          textAnchor="middle"
          fill="#8494a9"
          fontSize={size * 0.11}
        >
          {sub}
        </text>
      )}
    </svg>
  );
}

const PIPELINE_NODES = [
  { id: "screen", label: "Screen TX", sub: "Symbol Lattice", stageIdx: 5 },
  { id: "optics", label: "Photon Channel", sub: "Lens · Glare", stageIdx: 2 },
  { id: "camera", label: "Camera Capture", sub: "1080p Sensor", stageIdx: 2 },
  { id: "pose", label: "YOLO26 + Ring", sub: "4-Corner Pose", stageIdx: 3 },
  { id: "fec", label: "RS / LDPC FEC", sub: "LLR Demod + CRC", stageIdx: 4 },
  { id: "file", label: "Payload RX", sub: "SHA-1 Verified", stageIdx: 5 },
];

export function OpticalPipelineDiagram({
  stages,
  onSelectNode,
}: {
  stages?: ProgressStage[];
  onSelectNode?: (href: string) => void;
}) {
  const doneById: Record<string, boolean> = {};
  (stages || []).forEach((s) => {
    doneById[s.id] = s.done;
  });

  const nodeActive = [
    doneById["env"] ?? true,
    doneById["dataset"] ?? false,
    doneById["dataset"] ?? false,
    doneById["train"] ?? false,
    doneById["verify"] ?? false,
    doneById["send"] ?? false,
  ];

  const nodeHrefs = ["/s2c", "/lab", "/capture", "/train", "/evaluate", "/s2c"];

  return (
    <svg
      viewBox="0 0 780 124"
      width="100%"
      height="124"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Optical modem physical and neural pipeline"
    >
      <defs>
        <marker
          id="omPipeArrowOk"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="5"
          markerHeight="5"
          orient="auto-start-reverse"
        >
          <path d="M0 0 L10 5 L0 10 z" fill="#10b981" />
        </marker>
        <marker
          id="omPipeArrowIdle"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="5"
          markerHeight="5"
          orient="auto-start-reverse"
        >
          <path d="M0 0 L10 5 L0 10 z" fill="#475569" />
        </marker>
      </defs>

      <text x="14" y="16" fill="#64748b" fontSize="9.5" fontWeight="600">
        TRANSMITTER (DISPLAY)
      </text>
      <text
        x="766"
        y="16"
        textAnchor="end"
        fill="#64748b"
        fontSize="9.5"
        fontWeight="600"
      >
        RECONSTRUCTED BYTES
      </text>

      {PIPELINE_NODES.map((n, i) => {
        const x = 14 + i * 128;
        const y = 28;
        const w = 108;
        const h = 64;
        const ok = nodeActive[i];
        const stroke = ok ? "#10b981" : "#334155";
        const fill = ok ? "rgba(16, 185, 129, 0.09)" : "rgba(15, 23, 42, 0.75)";
        const edgeLabels = ["photons", "exposure", "4 corners", "soft LLR", "packets"];

        return (
          <g key={n.id}>
            {i < PIPELINE_NODES.length - 1 && (
              <g>
                <line
                  x1={x + w + 2}
                  y1={y + h / 2}
                  x2={x + 126}
                  y2={y + h / 2}
                  stroke={ok && nodeActive[i + 1] ? "#10b981" : "#475569"}
                  strokeWidth="1.8"
                  strokeDasharray={ok && nodeActive[i + 1] ? undefined : "3 3"}
                  markerEnd={
                    ok && nodeActive[i + 1]
                      ? "url(#omPipeArrowOk)"
                      : "url(#omPipeArrowIdle)"
                  }
                />
                <text
                  x={x + w + 10}
                  y={y + h / 2 - 7}
                  textAnchor="middle"
                  fill="#64748b"
                  fontSize="7.5"
                >
                  {edgeLabels[i]}
                </text>
              </g>
            )}
            <g
              style={{ cursor: onSelectNode ? "pointer" : "default" }}
              onClick={() => onSelectNode?.(nodeHrefs[i])}
            >
              <rect
                x={x}
                y={y}
                width={w}
                height={h}
                rx="10"
                fill={fill}
                stroke={stroke}
                strokeWidth="1.4"
                strokeDasharray={ok ? undefined : "4 3"}
              />
              <circle
                cx={x + 14}
                cy={y + 15}
                r="4"
                fill={ok ? "#10b981" : "#475569"}
              />
              <text
                x={x + 24}
                y={y + 18}
                fill="#94a3b8"
                fontSize="8.5"
                fontFamily="var(--font-mono)"
              >
                0{i + 1}
              </text>
              <text
                x={x + 12}
                y={y + 39}
                fill="#f1f5f9"
                fontSize="10.5"
                fontWeight="650"
              >
                {n.label}
              </text>
              <text x={x + 12} y={y + 53} fill="#8494a9" fontSize="8.5">
                {n.sub}
              </text>
            </g>
          </g>
        );
      })}

      <text x="14" y="112" fill="#64748b" fontSize="9">
        Solid emerald = verified on this server · Dashed = awaiting pipeline stage · Click any stage to open tool
      </text>
    </svg>
  );
}

export function StageIllustration({ stageId }: { stageId: string }) {
  switch (stageId) {
    case "env":
      return (
        <svg viewBox="0 0 360 116" width="100%" height="116">
          <rect x="14" y="16" width="136" height="84" rx="10" fill="#0b111b" stroke="#283548" />
          <text x="82" y="38" textAnchor="middle" fill="#94a3b8" fontSize="9.5" fontWeight="600">
            HOST HARDWARE
          </text>
          <rect x="30" y="48" width="44" height="32" rx="6" fill="none" stroke="#22d3ee" strokeWidth="1.5" />
          <text x="52" y="68" textAnchor="middle" fill="#22d3ee" fontSize="9" fontFamily="var(--font-mono)">
            CPU
          </text>
          <rect x="86" y="48" width="48" height="32" rx="6" fill="none" stroke="#10b981" strokeWidth="1.5" />
          <text x="110" y="68" textAnchor="middle" fill="#10b981" fontSize="9" fontFamily="var(--font-mono)">
            RAM
          </text>
          <path d="M158 58 H204" stroke="#3b82f6" strokeWidth="1.6" strokeDasharray="4 3" />
          <rect x="212" y="16" width="134" height="84" rx="10" fill="#0b111b" stroke="#283548" />
          <text x="230" y="40" fill="#e2e8f0" fontSize="10">✓ PyTorch Runtime</text>
          <text x="230" y="60" fill="#e2e8f0" fontSize="10">✓ Ultralytics YOLO26</text>
          <text x="230" y="80" fill="#e2e8f0" fontSize="10">✓ OpenCV + ReedSolo</text>
        </svg>
      );
    case "project":
      return (
        <svg viewBox="0 0 360 116" width="100%" height="116">
          <rect x="16" y="20" width="144" height="76" rx="10" fill="#0b111b" stroke="#3b82f6" />
          <rect x="16" y="20" width="144" height="20" rx="10" fill="rgba(59,130,246,0.2)" />
          <text x="88" y="34" textAnchor="middle" fill="#93c5fd" fontSize="9" fontWeight="600">
            PROJECT: v0.3-optical
          </text>
          <text x="88" y="58" textAnchor="middle" fill="#f1f5f9" fontSize="10.5" fontWeight="600">
            In Development
          </text>
          <text x="88" y="76" textAnchor="middle" fill="#94a3b8" fontSize="8.5">
            SQLite / Postgres Tracked
          </text>
          <path d="M168 42 H206 M168 58 H206 M168 74 H206" stroke="#64748b" strokeWidth="1.4" />
          <rect x="212" y="22" width="130" height="22" rx="6" fill="#111827" stroke="#283548" />
          <text x="277" y="36" textAnchor="middle" fill="#22d3ee" fontSize="9">Datasets &amp; Splits</text>
          <rect x="212" y="48" width="130" height="22" rx="6" fill="#111827" stroke="#283548" />
          <text x="277" y="62" textAnchor="middle" fill="#10b981" fontSize="9">Training Runs &amp; Cards</text>
          <rect x="212" y="74" width="130" height="22" rx="6" fill="#111827" stroke="#283548" />
          <text x="277" y="88" textAnchor="middle" fill="#c4b5fd" fontSize="9">S2C Transfers</text>
        </svg>
      );
    case "dataset":
      return (
        <svg viewBox="0 0 360 116" width="100%" height="116">
          <rect x="16" y="18" width="90" height="76" rx="9" fill="#0b111b" stroke="#283548" />
          {[0, 1, 2].map((r) =>
            [0, 1, 2].map((c) => (
              <rect
                key={`${r}-${c}`}
                x={31 + c * 21}
                y={28 + r * 18}
                width="16"
                height="13"
                rx="2"
                fill={(r + c) % 2 === 0 ? "#22d3ee" : "#3b82f6"}
                fillOpacity="0.45"
              />
            ))
          )}
          <text x="61" y="86" textAnchor="middle" fill="#94a3b8" fontSize="8.5">Synthetic Sim</text>
          <rect x="118" y="18" width="90" height="76" rx="9" fill="#0b111b" stroke="#283548" />
          <polygon points="138,34 188,30 192,70 134,74" fill="none" stroke="#10b981" strokeWidth="1.6" />
          <circle cx="138" cy="34" r="3" fill="#f59e0b" />
          <circle cx="188" cy="30" r="3" fill="#f59e0b" />
          <circle cx="192" cy="70" r="3" fill="#f59e0b" />
          <circle cx="134" cy="74" r="3" fill="#f59e0b" />
          <text x="163" y="86" textAnchor="middle" fill="#94a3b8" fontSize="8.5">Camera Captures</text>
          <path d="M216 56 H244" stroke="#22d3ee" strokeWidth="1.6" />
          <rect x="250" y="22" width="96" height="68" rx="9" fill="rgba(34,211,238,0.08)" stroke="#22d3ee" />
          <text x="298" y="44" textAnchor="middle" fill="#f1f5f9" fontSize="10" fontWeight="650">YOLO-Pose</text>
          <text x="298" y="60" textAnchor="middle" fill="#94a3b8" fontSize="8.5">4 Corner Keypoints</text>
          <text x="298" y="76" textAnchor="middle" fill="#10b981" fontSize="8.5">data.yaml ✓</text>
        </svg>
      );
    case "train":
      return (
        <svg viewBox="0 0 360 116" width="100%" height="116">
          <rect x="16" y="18" width="104" height="80" rx="9" fill="#0b111b" stroke="#283548" />
          <text x="68" y="42" textAnchor="middle" fill="#22d3ee" fontSize="10" fontWeight="650">
            yolo26n-pose
          </text>
          <text x="68" y="60" textAnchor="middle" fill="#94a3b8" fontSize="8.5">448 px · CPU/GPU</text>
          <text x="68" y="78" textAnchor="middle" fill="#10b981" fontSize="8.5">RAM-Sized Batch</text>
          <path d="M128 58 H158" stroke="#3b82f6" strokeWidth="1.6" strokeDasharray="4 3" />
          <rect x="166" y="16" width="178" height="84" rx="9" fill="#070a10" stroke="#283548" />
          <path d="M182 82 L210 68 L238 52 L266 41 L294 34 L328 29" fill="none" stroke="#10b981" strokeWidth="2.2" />
          <path d="M182 34 L210 50 L238 64 L266 73 L294 79 L328 83" fill="none" stroke="#3b82f6" strokeWidth="1.7" strokeDasharray="3 3" />
          <text x="255" y="28" textAnchor="middle" fill="#10b981" fontSize="8.5">pose mAP50-95 ↑</text>
          <text x="255" y="94" textAnchor="middle" fill="#64748b" fontSize="8">epochs → best.pt</text>
        </svg>
      );
    case "verify":
      return (
        <svg viewBox="0 0 360 116" width="100%" height="116">
          <rect x="16" y="18" width="146" height="80" rx="9" fill="#0b111b" stroke="#283548" />
          <text x="30" y="38" fill="#94a3b8" fontSize="8.5">L0.2 Clean</text>
          <rect x="86" y="30" width="62" height="8" rx="4" fill="#10b981" />
          <text x="30" y="58" fill="#94a3b8" fontSize="8.5">L0.6 Handheld</text>
          <rect x="86" y="50" width="48" height="8" rx="4" fill="#22d3ee" />
          <text x="30" y="78" fill="#94a3b8" fontSize="8.5">Corner Err</text>
          <text x="86" y="78" fill="#34d399" fontSize="9" fontFamily="var(--font-mono)">&lt; 2.0% side</text>
          <path d="M170 58 H206" stroke="#10b981" strokeWidth="1.8" />
          <rect x="214" y="24" width="130" height="68" rx="10" fill="rgba(16,185,129,0.1)" stroke="#10b981" />
          <text x="279" y="50" textAnchor="middle" fill="#f1f5f9" fontSize="10.5" fontWeight="700">
            ACTIVE MODEL
          </text>
          <text x="279" y="68" textAnchor="middle" fill="#34d399" fontSize="8.5">
            Bound to S2C Receiver
          </text>
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 360 116" width="100%" height="116">
          <rect x="16" y="20" width="124" height="76" rx="8" fill="#05070b" stroke="#283548" />
          {[0, 1, 2, 3].map((r) =>
            [0, 1, 2, 3, 4].map((c) => (
              <rect
                key={`${r}-${c}`}
                x={32 + c * 19}
                y={31 + r * 14}
                width="14"
                height="10"
                rx="1.5"
                fill={["#22d3ee", "#10b981", "#3b82f6", "#f59e0b", "#ec4899"][(r * 3 + c) % 5]}
              />
            ))
          )}
          <path d="M148 52 Q176 34 204 52" fill="none" stroke="#22d3ee" strokeWidth="1.6" />
          <path d="M148 64 Q176 82 204 64" fill="none" stroke="#22d3ee" strokeWidth="1.4" strokeDasharray="3 3" />
          <text x="176" y="42" textAnchor="middle" fill="#94a3b8" fontSize="8">photons</text>
          <rect x="212" y="26" width="58" height="64" rx="9" fill="#0b111b" stroke="#10b981" />
          <circle cx="241" cy="54" r="12" fill="none" stroke="#10b981" strokeWidth="1.6" />
          <circle cx="241" cy="54" r="4" fill="#10b981" />
          <path d="M278 58 H302" stroke="#10b981" strokeWidth="1.8" />
          <text x="308" y="54" fill="#f1f5f9" fontSize="10" fontWeight="650">Payload</text>
          <text x="308" y="70" fill="#34d399" fontSize="9" fontFamily="var(--font-mono)">SHA-1 ✓</text>
        </svg>
      );
  }
}

const SERIES_COLORS = [
  "#22d3ee",
  "#10b981",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#3b82f6",
];

export function TrainingCurvesChart({ curve }: { curve: CurveData | null }) {
  const [mode, setMode] = useState<"map" | "loss">("map");
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  if (!curve || !curve.epochs || curve.epochs.length === 0) {
    return (
      <div className="empty-state" style={{ padding: "24px 16px" }}>
        <p className="small" style={{ margin: 0 }}>
          No <code>results.csv</code> metrics recorded for this run yet.
        </p>
      </div>
    );
  }

  const allKeys = Object.keys(curve.series || {});
  const mapKeys = allKeys.filter((k) =>
    /map|precision|recall/i.test(k)
  );
  const lossKeys = allKeys.filter((k) => /loss/i.test(k));
  const activeKeys = (mode === "map" ? mapKeys : lossKeys).slice(0, 5);
  const keys = activeKeys.length > 0 ? activeKeys : allKeys.slice(0, 4);

  const W = 680;
  const H = 230;
  const padL = 46;
  const padR = 18;
  const padT = 18;
  const padB = 34;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  let minV = Infinity;
  let maxV = -Infinity;
  keys.forEach((k) => {
    (curve.series[k] || []).forEach((v) => {
      if (typeof v === "number" && Number.isFinite(v)) {
        if (v < minV) minV = v;
        if (v > maxV) maxV = v;
      }
    });
  });
  if (!Number.isFinite(minV)) {
    minV = 0;
    maxV = 1;
  }
  if (mode === "map") {
    minV = Math.min(0, minV);
    maxV = Math.max(1, maxV);
  } else if (Math.abs(maxV - minV) < 1e-6) {
    maxV = minV + 1;
  }

  const n = curve.epochs.length;
  const xOf = (i: number) =>
    padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yOf = (v: number) =>
    padT + plotH - ((v - minV) / (maxV - minV)) * plotH;

  return (
    <div>
      <div className="row spread mb">
        <div className="row" style={{ gap: 10 }}>
          {keys.map((k, idx) => {
            const vals = curve.series[k] || [];
            const last = vals[vals.length - 1];
            return (
              <span key={k} className="small" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <span
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: 2,
                    background: SERIES_COLORS[idx % SERIES_COLORS.length],
                    display: "inline-block",
                  }}
                />
                <span className="mono" style={{ fontSize: 11, color: "#cbd5e1" }}>
                  {k.replace(/^metrics\//, "")}
                  {typeof last === "number" ? `: ${last.toFixed(3)}` : ""}
                </span>
              </span>
            );
          })}
        </div>
        {mapKeys.length > 0 && lossKeys.length > 0 && (
          <div className="tabs">
            <button
              type="button"
              className={`tab-btn ${mode === "map" ? "active" : ""}`}
              onClick={() => setMode("map")}
            >
              Accuracy / mAP
            </button>
            <button
              type="button"
              className={`tab-btn ${mode === "loss" ? "active" : ""}`}
              onClick={() => setMode("loss")}
            >
              Loss Curves
            </button>
          </div>
        )}
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        onMouseLeave={() => setHoverIdx(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const relX = ((e.clientX - rect.left) / rect.width) * W - padL;
          const idx = Math.round((relX / plotW) * Math.max(1, n - 1));
          if (idx >= 0 && idx < n) setHoverIdx(idx);
        }}
        style={{ background: "#080c13", borderRadius: 10, border: "1px solid var(--border)" }}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const val = minV + t * (maxV - minV);
          const y = yOf(val);
          return (
            <g key={t}>
              <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#192231" strokeWidth="1" />
              <text
                x={padL - 7}
                y={y + 3}
                textAnchor="end"
                fill="#64748b"
                fontSize="9.5"
                fontFamily="var(--font-mono)"
              >
                {val.toFixed(2)}
              </text>
            </g>
          );
        })}

        {keys.map((k, sIdx) => {
          const vals = curve.series[k] || [];
          const pts: string[] = [];
          vals.forEach((v, i) => {
            if (typeof v === "number" && Number.isFinite(v)) {
              pts.push(`${pts.length === 0 ? "M" : "L"}${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`);
            }
          });
          return (
            <path
              key={k}
              d={pts.join(" ")}
              fill="none"
              stroke={SERIES_COLORS[sIdx % SERIES_COLORS.length]}
              strokeWidth="2.1"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          );
        })}

        {curve.epochs.map((ep, i) => {
          const step = Math.max(1, Math.ceil(n / 8));
          if (i % step !== 0 && i !== n - 1) return null;
          return (
            <text
              key={ep}
              x={xOf(i)}
              y={H - 10}
              textAnchor="middle"
              fill="#64748b"
              fontSize="9.5"
              fontFamily="var(--font-mono)"
            >
              ep {ep}
            </text>
          );
        })}

        {hoverIdx !== null && hoverIdx >= 0 && hoverIdx < n && (
          <g>
            <line
              x1={xOf(hoverIdx)}
              y1={padT}
              x2={xOf(hoverIdx)}
              y2={H - padB}
              stroke="#475569"
              strokeDasharray="3 3"
            />
            {keys.map((k, sIdx) => {
              const v = curve.series[k]?.[hoverIdx];
              if (typeof v !== "number") return null;
              return (
                <circle
                  key={k}
                  cx={xOf(hoverIdx)}
                  cy={yOf(v)}
                  r="4"
                  fill={SERIES_COLORS[sIdx % SERIES_COLORS.length]}
                  stroke="#080c13"
                  strokeWidth="1.5"
                />
              );
            })}
            <rect
              x={Math.min(W - 185, Math.max(padL + 4, xOf(hoverIdx) + 8))}
              y={padT + 4}
              width="172"
              height={18 + keys.length * 14}
              rx="6"
              fill="rgba(12, 16, 24, 0.94)"
              stroke="#334155"
            />
            <text
              x={Math.min(W - 185, Math.max(padL + 4, xOf(hoverIdx) + 8)) + 8}
              y={padT + 17}
              fill="#f8fafc"
              fontSize="9.5"
              fontWeight="700"
              fontFamily="var(--font-mono)"
            >
              Epoch {curve.epochs[hoverIdx]}
            </text>
            {keys.map((k, sIdx) => {
              const v = curve.series[k]?.[hoverIdx];
              return (
                <text
                  key={k}
                  x={Math.min(W - 185, Math.max(padL + 4, xOf(hoverIdx) + 8)) + 8}
                  y={padT + 31 + sIdx * 14}
                  fill={SERIES_COLORS[sIdx % SERIES_COLORS.length]}
                  fontSize="9"
                  fontFamily="var(--font-mono)"
                >
                  {k.replace(/^metrics\//, "").slice(0, 16)}:{" "}
                  {typeof v === "number" ? v.toFixed(4) : "—"}
                </text>
              );
            })}
          </g>
        )}
      </svg>
    </div>
  );
}

export function EvalBarsChart({ report }: { report: EvalReport | null }) {
  if (!report || !report.levels || Object.keys(report.levels).length === 0) {
    return null;
  }

  const levelEntries = Object.entries(report.levels).sort(
    ([a], [b]) => parseFloat(a) - parseFloat(b)
  );
  const modes: { key: "classical" | "yolo" | "auto"; label: string; color: string }[] = [
    { key: "classical", label: "Classical Contours", color: "#64748b" },
    { key: "yolo", label: "YOLO26-Pose", color: "#3b82f6" },
    { key: "auto", label: "Hybrid Auto", color: "#10b981" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="row" style={{ gap: 16 }}>
        {modes.map((m) => (
          <span key={m.key} className="small" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: m.color, display: "inline-block" }} />
            {m.label}
          </span>
        ))}
      </div>
      {levelEntries.map(([lvl, data]) => (
        <div key={lvl} className="card-subtle">
          <div className="row spread" style={{ marginBottom: 8 }}>
            <strong className="mono" style={{ fontSize: 13 }}>
              Channel Noise Level {lvl}
            </strong>
            <span className="small">{data.n ? `${data.n} test frames` : ""}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {modes.map((m) => {
              const rate = data[m.key]?.decode_rate;
              const pct = typeof rate === "number" ? Math.round(rate * 100) : 0;
              return (
                <div
                  key={m.key}
                  style={{ display: "grid", gridTemplateColumns: "130px 1fr 54px", alignItems: "center", gap: 10 }}
                >
                  <span className="small">{m.label}</span>
                  <div className="progress-track" style={{ height: 10 }}>
                    <div
                      style={{
                        width: `${pct}%`,
                        height: "100%",
                        background: m.color,
                        borderRadius: 999,
                        transition: "width 0.35s ease",
                      }}
                    />
                  </div>
                  <span className="mono" style={{ fontSize: 12, textAlign: "right", color: "#e2e8f0" }}>
                    {typeof rate === "number" ? `${pct}%` : "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export function SwatchStrip({ hexes, max = 16 }: { hexes: string[]; max?: number }) {
  const shown = (hexes || []).slice(0, max);
  const extra = (hexes || []).length - shown.length;
  return (
    <span className="swatch-strip">
      {shown.map((h, i) => (
        <span
          key={`${h}-${i}`}
          className="swatch-chip"
          style={{ background: h }}
          title={h}
        />
      ))}
      {extra > 0 && <span className="small mono" style={{ marginLeft: 4 }}>+{extra}</span>}
    </span>
  );
}
