import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

const PAGE = 24;
type Report = Awaited<ReturnType<typeof api.datasetQuality>>;

export function DatasetReview({ name, onClose }: { name: string; onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const check = useCallback(() => {
    let active = true;
    setLoading(true); setError(""); setReport(null);
    api.datasetQuality(name, offset, PAGE).then(r => { if (active) setReport(r); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : "Check failed"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [name, offset, refresh]);
  useEffect(check, [check]);
  return <section className="card-subtle mt" aria-label={`Label review for ${name}`}>
    <div className="row spread"><h3>Label review: {name}</h3><div className="row"><button type="button" className="secondary sm" disabled={loading} onClick={() => { setOffset(0); setRefresh(x => x + 1); }}>Recheck</button><button type="button" className="secondary sm" onClick={onClose}>Close review</button></div></div>
    <p className="small">Checks label structure and coordinates, not whether annotations match the image. Missing or empty labels may be intentional backgrounds. Results are a snapshot; recheck after edits.</p>
    {loading && <p role="status">Checking all images and labels…</p>}
    {error && <p role="alert">Check failed: {error}</p>}
    {report && <>
      <p role={report.issue_count ? "alert" : "status"}><strong>{report.issue_count ? `${report.issue_count} findings need review` : "No structural issues found"}</strong> · {report.counts.train || 0} train / {report.counts.val || 0} val images checked</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: 10 }}>
        {report.issues.map((issue, i) => <div key={`${issue.file}-${issue.kind}-${i}`} className="card-subtle" style={{ border: "2px solid #e59e0b", padding: 8 }}>
          {issue.split && issue.image && <img loading="lazy" crossOrigin="use-credentials" src={api.datasetImageUrl(name, issue.split, issue.image, 180)} alt={`Review ${issue.file}`} style={{ width: "100%", height: 110, objectFit: "contain" }} />}
          <strong style={{ overflowWrap: "anywhere" }}>{issue.file}</strong><div className="small"><strong>{issue.kind.replace(/_/g, " ")}</strong>: {issue.detail}</div>
          {issue.split && issue.image && issue.index !== undefined && <Link className="btn secondary sm mt" to={`/datasets/edit/${encodeURIComponent(name)}?${new URLSearchParams({ split: issue.split, index: String(issue.index), image: issue.image })}`}>Review in Pose Studio</Link>}
        </div>)}
      </div>
      <div className="row mt"><button type="button" className="secondary sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous findings</button><span className="small">{report.issue_count ? `${offset + 1}–${Math.min(offset + PAGE, report.issue_count)} of ${report.issue_count}` : "No findings"}</span><button type="button" className="secondary sm" disabled={!report.truncated} onClick={() => setOffset(offset + PAGE)}>Next findings</button></div>
    </>}
  </section>;
}
