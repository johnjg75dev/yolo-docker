import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";

interface AuthContextValue {
  authed: boolean;
  checking: boolean;
  passwordRequired: boolean;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const MANUAL_LOGOUT_KEY = "om_manual_logout";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(true);
  const [passwordRequired, setPasswordRequired] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await api.health().catch(() => ({ password_set: true }));
        const pwReq = Boolean(h?.password_set);
        if (!cancelled) setPasswordRequired(pwReq);

        const st = await api.authStatus();
        if (st.authed) {
          if (!cancelled) setAuthed(true);
          return;
        }

        // In local/preview mode (APP_PASSWORD unset), auto-issue a session unless user signed out manually
        const manualLogout = sessionStorage.getItem(MANUAL_LOGOUT_KEY) === "1";
        if (!pwReq && !manualLogout) {
          await api.login("");
          if (!cancelled) setAuthed(true);
        }
      } catch {
        if (!cancelled) setAuthed(false);
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = async (password: string) => {
    sessionStorage.removeItem(MANUAL_LOGOUT_KEY);
    await api.login(password);
    setAuthed(true);
  };

  const logout = async () => {
    sessionStorage.setItem(MANUAL_LOGOUT_KEY, "1");
    await api.logout();
    setAuthed(false);
  };

  return (
    <AuthContext.Provider
      value={{ authed, checking, passwordRequired, login, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
