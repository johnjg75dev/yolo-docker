import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Datasets() {
  const [datasets, setDatasets] = useState<any[]>([]);
  const { show } = useToast();

  const load = () => {
    api.listDatasets().then(setDatasets).catch((e) => show(e.message, true));
  };

  useEffect(load, []);

  const handleDelete = async (name: string) => {
    try {
      await api.deleteDataset(name);
      show("deleted");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "delete failed", true);
    }
  };

  return (
    <div className="wrap">
      <h1>Datasets</h1>
      <p className="small">Manage training datasets for the YOLO finder model.</p>

      <div className="card">
        <h2>Existing datasets</h2>
        {datasets.length === 0 ? (
          <p className="small">No datasets yet. Create one via the API or synth endpoint.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Images</th>
                <th>Labels</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((d: any) => (
                <tr key={d.name || d.id}>
                  <td>{d.name}</td>
                  <td>{d.images ?? d.count ?? "—"}</td>
                  <td>{d.labels ?? "—"}</td>
                  <td className="small">{d.created_at}</td>
                  <td>
                    <button className="danger" onClick={() => handleDelete(d.name)}>delete</button>
                    <Link to={`/datasets/edit/${encodeURIComponent(d.name)}`} style={{ marginLeft: 8 }}>
                      <button className="secondary">edit labels</button>
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}