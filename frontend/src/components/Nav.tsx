import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export function Nav() {
  const { authed, logout } = useAuth();
  const loc = useLocation();

  const link = (to: string, label: string) => (
    <Link to={to} className={loc.pathname === to ? "active" : ""}>
      {label}
    </Link>
  );

  return (
    <nav>
      {link("/", "Home")}
      {link("/wizard", "Wizard")}
      {link("/s2c", "S2C")}
      {link("/datasets", "Datasets")}
      {link("/train", "Train")}
      {link("/jobs", "Jobs")}
      {link("/projects", "Projects")}
      {link("/capture", "Capture")}
      {link("/settings", "Settings")}
      <span style={{ flex: 1 }} />
      {authed ? (
        <button className="secondary" onClick={logout}>
          Logout
        </button>
      ) : (
        link("/login", "Login")
      )}
    </nav>
  );
}