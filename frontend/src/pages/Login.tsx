import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../components/Toast";

export function Login() {
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const { show } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const next = (location.state as any)?.from?.pathname || "/";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(password);
      navigate(next, { replace: true });
    } catch (e: any) {
      show(e instanceof Error ? e.message : "login failed", true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wrap">
      <div className="card" style={{ maxWidth: 400, margin: "60px auto" }}>
        <h1>Login</h1>
        <p className="small">Enter the shared password to access the suite.</p>
        <form onSubmit={handleSubmit}>
          <label className="field">
            <span>password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
          </label>
          <div className="mt">
            <button type="submit" disabled={loading || !password}>
              {loading ? "signing in…" : "sign in"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}