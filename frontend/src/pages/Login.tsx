import { useEffect, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Lock,
  ShieldCheck,
  Unlock,
} from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../components/Toast";
import type { DiagnoseResponse } from "../types";

export function Login() {
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [showDiag, setShowDiag] = useState(false);
  const [diag, setDiag] = useState<DiagnoseResponse | null>(null);
  const { login, passwordRequired } = useAuth();
  const { show } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const next = (location.state as any)?.from?.pathname || "/";

  useEffect(() => {
    api
      .diagnose()
      .then(setDiag)
      .catch(() => {});
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(password);
      show("Signed in to Optical Modem Suite");
      navigate(next, { replace: true });
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Authentication failed", true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "28px 16px",
        background:
          "radial-gradient(ellipse 75% 55% at 50% 0%, rgba(34, 211, 238, 0.11), transparent), var(--bg)",
      }}
    >
      <div style={{ width: "100%", maxWidth: 440 }}>
        <div style={{ textAlign: "center", marginBottom: 22 }}>
          <div
            className="brand-mark"
            style={{ width: 52, height: 52, borderRadius: 14, margin: "0 auto 12px" }}
          >
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="3" width="7" height="7" rx="1.5" fill="#22d3ee" />
              <rect x="14" y="3" width="7" height="7" rx="1.5" fill="#10b981" />
              <rect x="3" y="14" width="7" height="7" rx="1.5" fill="#3b82f6" />
              <rect x="14" y="14" width="7" height="7" rx="1.5" stroke="#22d3ee" strokeWidth="2" />
            </svg>
          </div>
          <h1 style={{ fontSize: "1.5rem" }}>Optical Modem Suite</h1>
          <p className="small" style={{ marginTop: 4 }}>
            Software-defined screen-to-camera transceiver &amp; YOLO26-pose studio
          </p>
        </div>

        <div className="card" style={{ padding: "24px 24px 22px" }}>
          <form onSubmit={handleSubmit}>
            {!passwordRequired ? (
              <div className="callout ok" style={{ marginBottom: 14 }}>
                <strong style={{ color: "#fff" }}>Local / Preview Mode:</strong>{" "}
                <code>APP_PASSWORD</code> is not set — click below to enter the studio directly.
              </div>
            ) : (
              <label className="field">
                <span>Suite Access Password</span>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter shared APP_PASSWORD…"
                  autoFocus
                />
              </label>
            )}

            <div className="mt" style={{ marginTop: 16 }}>
              <button
                type="submit"
                disabled={loading || (passwordRequired && !password)}
                style={{ width: "100%", padding: "10px 16px" }}
              >
                {passwordRequired ? <Lock size={15} /> : <Unlock size={15} />}
                {loading
                  ? "Authenticating…"
                  : passwordRequired
                  ? "Unlock Studio"
                  : "Enter Studio"}
              </button>
            </div>
          </form>

          {diag && (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <button
                type="button"
                className="ghost sm"
                style={{
                  width: "100%",
                  justifyContent: "space-between",
                  border: "none",
                  padding: "4px 2px",
                }}
                onClick={() => setShowDiag((v) => !v)}
              >
                <span className="row" style={{ gap: 7 }}>
                  {diag.ok ? (
                    <ShieldCheck size={15} color="#34d399" />
                  ) : (
                    <AlertTriangle size={15} color="#fbbf24" />
                  )}
                  <span className="small" style={{ color: "var(--fg-soft)" }}>
                    Connection &amp; Proxy Check:{" "}
                    {diag.verdict || (diag.ok ? "Healthy" : "Check headers")}
                  </span>
                </span>
                {showDiag ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
              </button>

              {showDiag && (
                <div className="card-subtle mt" style={{ padding: 12 }}>
                  <div className="row spread small" style={{ marginBottom: 8 }}>
                    <span>
                      Scheme: <code>{diag.scheme}</code>
                    </span>
                    <span>
                      Build: <code>{diag.build || "dev"}</code>
                    </span>
                    <span>
                      DB: <code>{diag.database?.kind || "sqlite"}</code>
                    </span>
                  </div>
                  {(diag.checks || []).slice(0, 5).map((c) => (
                    <div
                      key={c.id}
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 7,
                        fontSize: 12,
                        marginTop: 5,
                        color: c.ok ? "var(--muted)" : "#fca5a5",
                      }}
                    >
                      <CheckCircle2
                        size={13}
                        color={c.ok ? "#10b981" : "#ef4444"}
                        style={{ marginTop: 2, flexShrink: 0 }}
                      />
                      <div>
                        <strong style={{ color: "var(--fg-soft)" }}>{c.id}:</strong>{" "}
                        {c.detail}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
