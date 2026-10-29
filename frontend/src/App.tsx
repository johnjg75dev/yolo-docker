import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { Layout } from "./components/Layout";

import { Home } from "./pages/Home";
import { Wizard } from "./pages/Wizard";
import { S2C } from "./pages/S2C";
import { Lab } from "./pages/Lab";
import { Projects } from "./pages/Projects";
import { Datasets } from "./pages/Datasets";
import { DatasetEditor } from "./pages/DatasetEditor";
import { Capture } from "./pages/Capture";
import { Train } from "./pages/Train";
import { Evaluate } from "./pages/Evaluate";
import { Models } from "./pages/Models";
import { Jobs } from "./pages/Jobs";
import { Settings } from "./pages/Settings";
import { Guide } from "./pages/Guide";
import { Login } from "./pages/Login";

function AuthRoute({ children }: { children: React.ReactNode }) {
  const { authed, checking } = useAuth();
  const location = useLocation();
  if (checking) {
    return (
      <div className="wrap" style={{ paddingTop: 48 }}>
        <div
          className="card"
          style={{ maxWidth: 420, margin: "0 auto", textAlign: "center" }}
        >
          <span className="pill run">Optical Modem Studio</span>
          <p className="small mt" style={{ marginBottom: 0 }}>
            Verifying session…
          </p>
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
    <Layout>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <AuthRoute>
              <Home />
            </AuthRoute>
          }
        />
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
          path="/lab"
          element={
            <AuthRoute>
              <Lab />
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
          path="/datasets"
          element={
            <AuthRoute>
              <Datasets />
            </AuthRoute>
          }
        />
        <Route path="/dataset" element={<Navigate to="/datasets" replace />} />
        <Route
          path="/datasets/edit/:name"
          element={
            <AuthRoute>
              <DatasetEditor />
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
          path="/train"
          element={
            <AuthRoute>
              <Train />
            </AuthRoute>
          }
        />
        <Route
          path="/evaluate"
          element={
            <AuthRoute>
              <Evaluate />
            </AuthRoute>
          }
        />
        <Route
          path="/models"
          element={
            <AuthRoute>
              <Models />
            </AuthRoute>
          }
        />
        <Route
          path="/jobs"
          element={
            <AuthRoute>
              <Jobs />
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
        <Route
          path="/guide"
          element={
            <AuthRoute>
              <Guide />
            </AuthRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

export default App;
