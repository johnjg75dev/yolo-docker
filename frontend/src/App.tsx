import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { Nav } from "./components/Nav";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Wizard } from "./pages/Wizard";
import { S2C } from "./pages/S2C";
import { Datasets } from "./pages/Datasets";
import { Train } from "./pages/Train";
import { Projects } from "./pages/Projects";
import { Capture } from "./pages/Capture";
import { Settings } from "./pages/Settings";

function AuthRoute({ children }: { children: React.ReactNode }) {
  const { authed, checking } = useAuth();
  const location = useLocation();

  if (checking) {
    return (
      <div className="wrap">
        <div className="card center" style={{ paddingTop: "60px" }}>
          <div className="pill run">signing in…</div>
        </div>
      </div>
    );
  }

  if (!authed) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

export function App() {
  return (
    <>
      <Nav />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/wizard"
          element={
            <AuthRoute>
              <Wizard />
            </AuthRoute>
          }
        />
        <Route
          path="/s2c"
          element={
            <AuthRoute>
              <S2C />
            </AuthRoute>
          }
        />
        <Route
          path="/datasets"
          element={
            <AuthRoute>
              <Datasets />
            </AuthRoute>
          }
        />
        <Route
          path="/train"
          element={
            <AuthRoute>
              <Train />
            </AuthRoute>
          }
        />
        <Route
          path="/projects"
          element={
            <AuthRoute>
              <Projects />
            </AuthRoute>
          }
        />
        <Route
          path="/capture"
          element={
            <AuthRoute>
              <Capture />
            </AuthRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <AuthRoute>
              <Settings />
            </AuthRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}