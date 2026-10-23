import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Projects() {
  const [projects, setProjects] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const { show } = useToast();

  const load = () => {
    api.listProjects().then(setProjects).catch((e) => show(e.message, true));
  };

  useEffect(load, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { show("name required", true); return; }
    setLoading(true);
    try {
      await api.createProject(name);
      show("project created");
      setName("");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "create failed", true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wrap">
      <h1>Projects</h1>
      <p className="small">Organize work into projects.</p>

      <div className="card">
        <h2>Create project</h2>
        <form onSubmit={handleCreate} className="row">
          <label className="field grow">
            <span>name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="submit" disabled={loading} style={{ alignSelf: "flex-end" }}>
            {loading ? "creating…" : "create"}
          </button>
        </form>
      </div>

      <div className="card">
        <h2>Projects</h2>
        {projects.length === 0 ? (
          <p className="small">No projects yet.</p>
        ) : (
          <div className="grid grid-3">
            {projects.map((p: any) => (
              <div key={p.id} className="card" style={{ margin: 0 }}>
                <h3>{p.name}</h3>
                <p className="small">{p.path}</p>
                <p className="small">{p.created_at}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}