"""Persistent state for the suite: projects, artefacts and job history.

The filesystem stays the source of truth for *bytes* (images, weights, logs —
they are big and they belong on a volume).  Everything else — which project a
dataset belongs to, whether it is "in development" or "finished", what a run
scored, which model is active, what each job did — lives in a database so it
survives a container rebuild and can be queried:

    DATABASE_URL=postgresql+psycopg://suite:suite@db:5432/suite

``docker-compose.yml`` ships a Postgres service for exactly this.  Without
``DATABASE_URL`` the suite falls back to SQLite in ``$DATA_DIR/suite.db``, which
keeps local runs and the test-suite dependency-free (and is still a real
database: same schema, same code path, only the driver differs).

Why SQLAlchemy instead of raw SQL: the schema has to work on both engines, the
web worker is multi-threaded (session-per-request semantics matter), and
``create_all`` on boot keeps the "just deploy it" story intact.

Every write goes through :class:`Store`, which is deliberately tiny and
synchronous — the app calls it from threadpool code, never from an ``async def``
without ``run_in_threadpool``.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

try:
    from .logging_config import configure as _configure_logging, get_logger as _get_logger
    _configure_logging()
    log = _get_logger("suite.db")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("suite.db")
from pathlib import Path
from typing import Any, Iterable, Iterator

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, func, select)
from sqlalchemy import case, func, update as _sa_update
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, sessionmaker)

# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #


class Base(DeclarativeBase):
    pass


def _now() -> float:
    return time.time()


class Project(Base):
    """A body of work: one dataset → one model → one payload format."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    #: development | finished | archived  (free text on purpose: the UI offers
    #: these three, but a deployment may add its own states)
    status: Mapped[str] = mapped_column(String(32), default="development", index=True)
    tags: Mapped[str] = mapped_column(String(400), default="")     # comma separated
    settings: Mapped[str] = mapped_column(Text, default="{}")       # JSON
    created: Mapped[float] = mapped_column(Float, default=_now)
    updated: Mapped[float] = mapped_column(Float, default=_now, onupdate=_now)

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="project",
                                                     cascade="all, delete-orphan")
    runs: Mapped[list["Run"]] = relationship(back_populates="project")
    jobs: Mapped[list["JobRecord"]] = relationship(back_populates="project")

    def as_dict(self, counts: bool = False) -> dict:
        d = {
            "id": self.id, "slug": self.slug, "title": self.title,
            "description": self.description, "status": self.status,
            "tags": [t for t in (self.tags or "").split(",") if t],
            "settings": _loads(self.settings, {}),
            "created": self.created, "updated": self.updated,
        }
        if counts:
            d["n_datasets"] = len(self.datasets)
            d["n_runs"] = len(self.runs)
        return d


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("name", name="uq_dataset_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"),
                                                   nullable=True, index=True)
    #: synth | captures | merge
    source: Mapped[str] = mapped_column(String(32), default="synth")
    path: Mapped[str] = mapped_column(String(500), default="")
    yaml: Mapped[str] = mapped_column(String(500), default="")
    images: Mapped[int] = mapped_column(Integer, default=0)
    splits: Mapped[str] = mapped_column(Text, default="{}")        # JSON
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[str] = mapped_column(Text, default="{}")        # JSON
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    created: Mapped[float] = mapped_column(Float, default=_now)
    updated: Mapped[float] = mapped_column(Float, default=_now, onupdate=_now)

    project: Mapped[Project | None] = relationship(back_populates="datasets")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "project_id": self.project_id,
            "source": self.source, "path": self.path, "yaml": self.yaml,
            "images": self.images, "splits": _loads(self.splits, {}),
            "bytes": self.bytes, "params": _loads(self.params, {}),
            "status": self.status, "created": self.created, "updated": self.updated,
        }


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("name", name="uq_run_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"),
                                                   nullable=True, index=True)
    dataset: Mapped[str] = mapped_column(String(120), default="")
    epochs: Mapped[int] = mapped_column(Integer, default=0)
    imgsz: Mapped[int] = mapped_column(Integer, default=0)
    batch: Mapped[int] = mapped_column(Integer, default=0)
    device: Mapped[str] = mapped_column(String(24), default="cpu")
    base_model: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    metrics: Mapped[str] = mapped_column(Text, default="{}")
    wall_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    weights: Mapped[str] = mapped_column(String(500), default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    created: Mapped[float] = mapped_column(Float, default=_now)
    updated: Mapped[float] = mapped_column(Float, default=_now, onupdate=_now)

    project: Mapped[Project | None] = relationship(back_populates="runs")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "project_id": self.project_id,
            "dataset": self.dataset, "epochs": self.epochs, "imgsz": self.imgsz,
            "batch": self.batch, "device": self.device, "base_model": self.base_model,
            "status": self.status, "metrics": _loads(self.metrics, {}),
            "wall_seconds": self.wall_seconds, "weights": self.weights,
            "path": self.path, "created": self.created, "updated": self.updated,
        }


class ModelVersion(Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("name", name="uq_model_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"),
                                                   nullable=True, index=True)
    run: Mapped[str] = mapped_column(String(120), default="")
    weights: Mapped[str] = mapped_column(String(500), default="")
    card: Mapped[str] = mapped_column(Text, default="{}")
    metrics: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    promoted: Mapped[float] = mapped_column(Float, default=_now)
    created: Mapped[float] = mapped_column(Float, default=_now)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "project_id": self.project_id,
                "run": self.run, "weights": self.weights,
                "card": _loads(self.card, {}), "metrics": _loads(self.metrics, {}),
                "active": bool(self.active), "promoted": self.promoted,
                "created": self.created}


class JobRecord(Base):
    """One row per background job — the history the filesystem forgets."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"),
                                                   nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[str] = mapped_column(Text, default="{}")
    metrics: Mapped[str] = mapped_column(Text, default="{}")
    created: Mapped[float] = mapped_column(Float, default=_now)
    started: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished: Mapped[float | None] = mapped_column(Float, nullable=True)
    returncode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: written by the worker's heartbeat; a stale value means "the container died"
    heartbeat: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: --- job claiming (separate worker containers) ------------------------
    #: which worker owns this job right now; NULL means unclaimed
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    #: when the current claim was taken (compared against heartbeat staleness)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: how many times this job has been started; a job that keeps dying is
    #: marked interrupted rather than requeued forever
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    #: JSON argv. Without this a job adopted from the database has no command to
    #: run -- argv lived only in ``<DATA_DIR>/jobs/<id>/job.json``, which is fine
    #: for a single process but fatal when the process that adopts the job is not
    #: the one that enqueued it.
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="jobs")

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "project_id": self.project_id, "status": self.status,
                "progress": self.progress, "message": self.message,
                "error": self.error, "meta": _loads(self.meta, {}),
                "metrics": _loads(self.metrics, {}), "created": self.created,
                "started": self.started, "finished": self.finished,
                "returncode": self.returncode, "heartbeat": self.heartbeat,
                "owner": self.owner, "claimed_at": self.claimed_at,
                "attempts": self.attempts,
                "arguments": self.arguments}


class TransferRecord(Base):
    """Metadata of an S2C transfer (the frames and bytes live in the engine)."""

    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"),
                                                   nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha1: Mapped[str] = mapped_column(String(40), default="")
    frames: Mapped[int] = mapped_column(Integer, default=0)
    modulation: Mapped[str] = mapped_column(String(32), default="")
    ecc: Mapped[str] = mapped_column(String(32), default="")
    resolution: Mapped[str] = mapped_column(String(32), default="")
    finder: Mapped[str] = mapped_column(String(32), default="none")
    params: Mapped[str] = mapped_column(Text, default="{}")
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    received: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=_now)
    updated: Mapped[float] = mapped_column(Float, default=_now, onupdate=_now)

    def as_dict(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "name": self.name,
                "bytes": self.bytes, "sha1": self.sha1, "frames": self.frames,
                "modulation": self.modulation, "ecc": self.ecc,
                "resolution": self.resolution, "finder": self.finder,
                "params": _loads(self.params, {}), "complete": bool(self.complete),
                "received": self.received, "duplicates": self.duplicates,
                "failed": self.failed, "created": self.created,
                "updated": self.updated}


class Event(Base):
    """Append-only audit trail: who did what to which artefact, when."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float, default=_now, index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    subject: Mapped[str] = mapped_column(String(200), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[str] = mapped_column(Text, default="{}")

    def as_dict(self) -> dict:
        return {"id": self.id, "ts": self.ts, "kind": self.kind,
                "subject": self.subject, "project_id": self.project_id,
                "detail": _loads(self.detail, {})}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
#: job statuses used by the claim protocol (mirrors suite.jobs without importing
#: it, so the store has no dependency on the process-level job machinery)
QUEUED, RUNNING = "queued", "running"

#: the only project statuses the suite understands.  "development" and
#: "finished" are the two the user asked for; "archived" is for a project that
#: was finished and then parked (kept for its artefacts, hidden from the list).
PROJECT_STATUSES = ("development", "finished", "archived")


def _check_status(status: str) -> str:
    status = str(status).strip().lower()
    if status not in PROJECT_STATUSES:
        raise ValueError(f"unknown project status {status!r}; "
                         f"choose from {', '.join(PROJECT_STATUSES)}")
    return status


def _loads(text: str | None, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _dumps(obj) -> str:
    return json.dumps(obj, default=str)


def default_url(data_dir: str | Path | None = None) -> str:
    """``DATABASE_URL`` if set, else SQLite beside the other state."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # accept the bare postgres:// form that hosting panels hand out
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    root = Path(data_dir or os.environ.get("DATA_DIR", ".") or ".")
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{root / 'suite.db'}"


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
class Store:
    """Thin, thread-safe facade over the schema above.

    All methods are synchronous and short.  ``session_scope`` gives one
    transaction; the suite's request handlers run these in the threadpool.
    """

    def __init__(self, url: str | None = None, data_dir: str | Path | None = None,
                 echo: bool = False, create: bool = True):
        self.url = url or default_url(data_dir)
        kwargs: dict[str, Any] = {"echo": echo, "future": True}
        if self.url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            kwargs["pool_pre_ping"] = True
        else:
            kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)
        self.engine = create_engine(self.url, **kwargs)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False,
                                     future=True)
        self.kind = self.engine.url.get_backend_name()      # sqlite | postgresql
        if create:
            self.create_all()
            # SQLite needs WAL for the concurrent reader/writer pattern the
            # job threads produce; Postgres does not care
            if self.kind == "sqlite":
                with self.engine.begin() as conn:
                    conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                    conn.exec_driver_sql("PRAGMA synchronous=NORMAL")

    # ---- plumbing --------------------------------------------------------
    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._ensure_columns()

    # Columns added after a deployment already ran must be ALTERed in:
    # ``create_all`` only creates missing *tables*. Without this, a volume
    # created before job claiming existed would raise OperationalError on the
    # first claim -- at runtime, under load, in production.
    _EXTRA_COLUMNS: tuple[tuple[str, str, str], ...] = (
        ("jobs", "owner", "VARCHAR(120)"),
        ("jobs", "claimed_at", "FLOAT"),
        ("jobs", "attempts", "INTEGER DEFAULT 0"),
        ("jobs", "arguments", "TEXT"),
    )

    def _ensure_columns(self) -> None:
        with self.engine.begin() as conn:
            for table, column, ddl in self._EXTRA_COLUMNS:
                try:
                    existing = {row[1] for row in
                                conn.exec_driver_sql(f"PRAGMA table_info({table})")} \
                        if self.kind == "sqlite" else {
                            row[0] for row in conn.exec_driver_sql(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = :t", {"t": table})}
                except Exception:
                    continue
                if column in existing:
                    continue
                try:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                except Exception as exc:      # pragma: no cover - concurrent boot
                    log.warning("could not add %s.%s: %s", table, column, exc)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def info(self) -> dict:
        return {"url": self.redacted_url(), "kind": self.kind}

    def redacted_url(self) -> str:
        """The URL without the password — safe for logs and the status page."""
        u = self.engine.url
        return str(u.set(password="***")) if u.password else str(u)

    # ---- projects --------------------------------------------------------
    def create_project(self, slug: str, title: str = "", description: str = "",
                       status: str = "development", tags: Iterable[str] = (),
                       settings: dict | None = None) -> dict:
        slug = _slugify(slug)
        status = _check_status(status)
        with self.session() as s:
            if s.scalar(select(Project).where(Project.slug == slug)):
                raise ValueError(f"project {slug!r} already exists")
            p = Project(slug=slug, title=title or slug, description=description,
                        status=status, tags=",".join(sorted(set(tags))),
                        settings=_dumps(settings or {}))
            s.add(p)
            s.flush()
            out = p.as_dict(counts=True)
            self._event(s, "project.created", slug, p.id, {"status": status})
            return out

    def get_project(self, slug_or_id: str | int) -> dict | None:
        with self.session() as s:
            p = self._project(s, slug_or_id)
            return p.as_dict(counts=True) if p else None

    def list_projects(self, status: str | None = None) -> list[dict]:
        with self.session() as s:
            q = select(Project).order_by(Project.updated.desc())
            if status:
                q = q.where(Project.status == status)
            return [p.as_dict(counts=True) for p in s.scalars(q)]

    def update_project(self, slug_or_id: str | int, **fields) -> dict:
        allowed = {"title", "description", "status", "tags", "settings", "slug"}
        if "status" in fields and fields["status"] is not None:
            fields["status"] = _check_status(fields["status"])
        with self.session() as s:
            p = self._project(s, slug_or_id)
            if p is None:
                raise KeyError(f"no project {slug_or_id!r}")
            for k, v in fields.items():
                if k not in allowed or v is None:
                    continue
                if k == "tags":
                    v = ",".join(sorted(set(v if not isinstance(v, str) else
                                           [t.strip() for t in v.split(",") if t.strip()])))
                elif k == "settings":
                    v = _dumps(v)
                elif k == "slug":
                    v = _slugify(v)
                setattr(p, k, v)
            s.flush()
            out = p.as_dict(counts=True)
            self._event(s, "project.updated", out["slug"], p.id,
                        {k: v for k, v in fields.items() if k in allowed})
            return out

    def delete_project(self, slug_or_id: str | int, reassign_to: str | int | None = None,
                       delete_datasets: bool = False) -> bool:
        """Remove a project; its artefacts are detached (or deleted on request).

        Deleting a project must never orphan rows silently, so callers either
        pass ``reassign_to`` or accept that datasets/runs/models/transfers keep
        their metadata but point at no project (``project_id = NULL``).
        """
        with self.session() as s:
            p = self._project(s, slug_or_id)
            if p is None:
                return False
            target = self._project(s, reassign_to) if reassign_to is not None else None
            for ds in s.scalars(select(Dataset).where(Dataset.project_id == p.id)):
                if delete_datasets:
                    s.delete(ds)
                else:
                    ds.project_id = target.id if target else None
            for model in (Run, ModelVersion, TransferRecord, JobRecord):
                for row in s.scalars(select(model).where(model.project_id == p.id)):
                    row.project_id = target.id if target else None
            s.delete(p)
            self._event(s, "project.deleted", str(slug_or_id), None, {})
            return True

    def _project(self, s: Session, slug_or_id) -> Project | None:
        if isinstance(slug_or_id, int) or str(slug_or_id).isdigit():
            hit = s.get(Project, int(slug_or_id))
            if hit:
                return hit
        return s.scalar(select(Project).where(Project.slug == str(slug_or_id)))

    # ---- datasets --------------------------------------------------------
    def upsert_dataset(self, name: str, **fields) -> dict:
        with self.session() as s:
            row = s.scalar(select(Dataset).where(Dataset.name == name))
            if row is None:
                row = Dataset(name=name)
                s.add(row)
            self._apply_project(s, row, fields)
            for k in ("source", "path", "yaml", "status"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, str(fields[k]))
            for k in ("images", "bytes"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, int(fields[k]))
            for k in ("splits", "params"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, _dumps(fields[k]))
            s.flush()
            out = row.as_dict()
            self._event(s, "dataset.upserted", name, row.project_id, {"images": row.images})
            return out

    def get_dataset(self, name: str) -> dict | None:
        with self.session() as s:
            row = s.scalar(select(Dataset).where(Dataset.name == name))
            return row.as_dict() if row else None

    def list_datasets(self, project_id: int | None = None) -> list[dict]:
        with self.session() as s:
            q = select(Dataset).order_by(Dataset.created.desc())
            if project_id is not None:
                q = q.where(Dataset.project_id == project_id)
            return [r.as_dict() for r in s.scalars(q)]

    def delete_dataset(self, name: str) -> bool:
        with self.session() as s:
            row = s.scalar(select(Dataset).where(Dataset.name == name))
            if row is None:
                return False
            s.delete(row)
            self._event(s, "dataset.deleted", name, row.project_id, {})
            return True

    # ---- runs / models ---------------------------------------------------
    def upsert_run(self, name: str, **fields) -> dict:
        log.debug("db upsert_run name=%s fields=%s", name, list(fields.keys()))
        with self.session() as s:
            row = s.scalar(select(Run).where(Run.name == name))
            if row is None:
                row = Run(name=name)
                s.add(row)
            self._apply_project(s, row, fields)
            for k in ("dataset", "device", "base_model", "status", "weights", "path"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, str(fields[k]))
            for k in ("epochs", "imgsz", "batch"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, int(fields[k]))
            for k in ("metrics", "wall_seconds"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, _dumps(fields[k]) if k == "metrics"
                            else float(fields[k]))
            s.flush()
            out = row.as_dict()
            self._event(s, "run.upserted", name, row.project_id,
                        {"status": row.status, "metrics": row.metrics})
            return out

    def get_run(self, name: str) -> dict | None:
        with self.session() as s:
            row = s.scalar(select(Run).where(Run.name == name))
            return row.as_dict() if row else None

    def list_runs(self, project_id: int | None = None) -> list[dict]:
        with self.session() as s:
            q = select(Run).order_by(Run.created.desc())
            if project_id is not None:
                q = q.where(Run.project_id == project_id)
            return [r.as_dict() for r in s.scalars(q)]

    def promote_model(self, name: str, **fields) -> dict:
        """Record a promotion and make ``name`` the single active model."""
        with self.session() as s:
            row = s.scalar(select(ModelVersion).where(ModelVersion.name == name))
            if row is None:
                row = ModelVersion(name=name)
                s.add(row)
            self._apply_project(s, row, fields)
            for k in ("run", "weights"):
                if fields.get(k) is not None:
                    setattr(row, k, str(fields[k]))
            for k in ("card", "metrics"):
                if fields.get(k) is not None:
                    setattr(row, k, _dumps(fields[k]))
            row.active = True
            row.promoted = time.time()
            for other in s.scalars(select(ModelVersion).where(ModelVersion.name != name,
                                                              ModelVersion.active.is_(True))):
                other.active = False
            s.flush()
            out = row.as_dict()
            self._event(s, "model.promoted", name, row.project_id,
                        {"run": row.run})
            return out

    def activate_model(self, name: str) -> dict | None:
        with self.session() as s:
            row = s.scalar(select(ModelVersion).where(ModelVersion.name == name))
            if row is None:
                return None
            for other in s.scalars(select(ModelVersion).where(ModelVersion.active.is_(True))):
                other.active = False
            row.active = True
            row.promoted = time.time()
            s.flush()
            self._event(s, "model.activated", name, row.project_id, {})
            return row.as_dict()

    def active_model(self) -> dict | None:
        with self.session() as s:
            row = s.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)))
            return row.as_dict() if row else None

    def list_models(self) -> list[dict]:
        with self.session() as s:
            return [r.as_dict() for r in
                    s.scalars(select(ModelVersion).order_by(ModelVersion.promoted.desc()))]

    def delete_model(self, name: str) -> bool:
        with self.session() as s:
            row = s.scalar(select(ModelVersion).where(ModelVersion.name == name))
            if row is None:
                return False
            s.delete(row)
            self._event(s, "model.deleted", name, row.project_id, {})
            return True

    # ---- jobs ------------------------------------------------------------
    def record_job(self, job: dict, project_id: int | None = None) -> dict:
        log.debug("db record_job id=%s status=%s", job.get("id"), job.get("status"))
        """Insert or update the row for a job snapshot."""
        with self.session() as s:
            jid = str(job["id"])
            row = s.get(JobRecord, jid)
            if row is None:
                row = JobRecord(id=jid, kind=str(job.get("kind", "")))
                s.add(row)
            meta = job.get("meta") or {}
            if project_id is None and isinstance(meta, dict):
                pid = meta.get("project_id")
                if pid not in (None, ""):
                    project_id = int(pid)
                elif meta.get("project"):
                    # jobs carry the *slug* (that is what the pages send)
                    proj = self._project(s, meta["project"])
                    project_id = proj.id if proj else None
            row.kind = str(job.get("kind", row.kind))
            row.title = str(job.get("title", row.title))[:300]
            row.status = str(job.get("status", row.status))
            row.progress = float(job.get("progress") or 0.0)
            # snapshots are full state, but the *last* interesting message and
            # the metric dicts are history: merging keeps "epoch 7/10" visible on
            # the finished row instead of blanking it when the job ends
            row.message = str(job.get("message") or row.message or "")[:500]
            row.error = str(job.get("error") or row.error or "")
            if meta:
                row.meta = _dumps({**_loads(row.meta, {}), **meta})
            metrics = job.get("metrics") or {}
            if metrics:
                row.metrics = _dumps({**_loads(row.metrics, {}), **metrics})
            row.created = float(job.get("created") or row.created or _now())
            row.started = job.get("started", row.started)
            row.finished = job.get("finished", row.finished)
            rc = job.get("returncode", row.returncode)
            row.returncode = int(rc) if rc is not None else None
            row.heartbeat = _now()
            # Persist argv so another process can pick this job up and actually
            # run it. Guarded on presence: a progress snapshot carries no argv,
            # and we must not blank a command that is already recorded.
            argv = job.get("argv")
            if argv:
                row.arguments = _dumps([str(a) for a in argv])
            if project_id is not None:
                row.project_id = project_id
            s.flush()
            return row.as_dict()

    # ---- job claiming (separate worker containers, audit S5) -------------
    def claim_job(self, jid: str, worker_id: str, ttl: float = 0.0) -> bool:
        """Atomically take ownership of a queued job. ``True`` if we won.

        This must be ONE conditional UPDATE, not a SELECT followed by an UPDATE.
        A read-then-write lets two workers both observe ``status='queued'`` and
        both conclude they won, which is exactly the double-execution the claim
        exists to prevent -- and it only manifests under load, so it would ship.

        Both engines guarantee that of two concurrent
        ``UPDATE ... WHERE status='queued'`` statements exactly one reports
        rowcount 1, so the loser sees 0 and backs off.
        """
        now = _now()
        t = JobRecord.__table__
        values = {"owner": worker_id, "claimed_at": now, "heartbeat": now,
                  "status": RUNNING,
                  "attempts": func.coalesce(t.c.attempts, 0) + 1,
                  "started": case((t.c.started.is_(None), now), else_=t.c.started)}
        with self.engine.begin() as conn:
            res = conn.execute(
                t.update().where(t.c.id == str(jid), t.c.status == QUEUED).values(**values))
            if res.rowcount == 1:
                return True
            # A job whose owner stopped heartbeating is recoverable: steal it.
            # Same reasoning -- one statement, so only one thief wins.
            if ttl > 0:
                res = conn.execute(
                    t.update().where(t.c.id == str(jid),
                                     t.c.owner.is_not(None),
                                     t.c.heartbeat.is_not(None),
                                     t.c.heartbeat < (now - ttl)).values(**values))
                if res.rowcount == 1:
                    return True
        return False

    def release_job(self, jid: str, worker_id: str | None = None,
                    status: str | None = None) -> bool:
        """Drop a claim. ``worker_id`` restricts release to the current owner."""
        with self.session() as s:
            row = s.get(JobRecord, str(jid))
            if row is None:
                return False
            if worker_id is not None and row.owner != worker_id:
                return False
            row.owner = None
            row.claimed_at = None
            if status:
                row.status = status
            s.flush()
            return True

    def heartbeat_job(self, jid: str, worker_id: str) -> bool:
        """Refresh liveness. ``False`` if we no longer own the job."""
        with self.session() as s:
            row = s.get(JobRecord, str(jid))
            if row is None or (row.owner is not None and row.owner != worker_id):
                return False
            row.heartbeat = _now()
            s.flush()
            return True

    def reap_stale_jobs(self, stale_after: float, max_retries: int = 3) -> dict:
        """Requeue jobs whose owning worker stopped heartbeating.

        A worker that was OOM-killed or rescheduled leaves a job stuck in
        ``running`` forever -- invisible, because nothing is writing to it. This
        is the recovery path, and it is the reason the separate-worker mode was
        marked experimental: without a reaper a single lost worker silently
        wedges the queue.
        """
        now = _now()
        cutoff = now - max(1.0, stale_after)
        requeued: list[str] = []
        dead: list[str] = []
        with self.session() as s:
            rows = s.scalars(select(JobRecord).where(
                JobRecord.status.in_(("running", "queued")),
                JobRecord.owner.is_not(None),
            ))
            for row in list(rows):
                last = row.heartbeat or row.claimed_at or 0.0
                if last > cutoff:
                    continue
                if int(row.attempts or 0) >= max(1, max_retries):
                    row.status = "interrupted"
                    row.owner = None
                    row.claimed_at = None
                    row.finished = row.finished or now
                    row.error = row.error or (
                        f"gave up after {row.attempts} attempts; last owned by "
                        f"{row.owner!r}")
                    dead.append(row.id)
                else:
                    row.status = QUEUED
                    row.owner = None
                    row.claimed_at = None
                    row.started = None
                    requeued.append(row.id)
            s.flush()
        return {"requeued": requeued, "interrupted": dead, "stale_after": stale_after}

    def queue_stats(self) -> dict:
        """Depth and ownership of the queue -- drives /healthz and the UI."""
        with self.session() as s:
            def n(*conds):
                return s.scalar(select(func.count()).select_from(JobRecord).where(*conds)) or 0
            owners = {}
            for row in s.scalars(select(JobRecord).where(
                    JobRecord.status == RUNNING, JobRecord.owner.is_not(None))):
                owners.setdefault(row.owner, 0)
                owners[row.owner] += 1
            return {"queued": n(JobRecord.status == QUEUED),
                    "running": n(JobRecord.status == RUNNING),
                    "interrupted": n(JobRecord.status == "interrupted"),
                    "failed": n(JobRecord.status == "failed"),
                    "ok": n(JobRecord.status == "ok"),
                    "by_owner": owners}

    def get_job(self, jid: str) -> dict | None:
        with self.session() as s:
            row = s.get(JobRecord, jid)
            return row.as_dict() if row else None

    def delete_job(self, jid: str) -> bool:
        log.debug("db delete_job id=%s", jid)
        with self.session() as s:
            row = s.get(JobRecord, jid)
            if row is None:
                return False
            s.delete(row)
            return True

    def list_jobs(self, kind: str | None = None, limit: int = 50,
                  project_id: int | None = None) -> list[dict]:
        with self.session() as s:
            q = select(JobRecord).order_by(JobRecord.created.desc()).limit(limit)
            if kind:
                q = q.where(JobRecord.kind == kind)
            if project_id is not None:
                q = q.where(JobRecord.project_id == project_id)
            return [r.as_dict() for r in s.scalars(q)]

    def mark_interrupted(self, older_than: float = 0.0) -> int:
        """Jobs left ``running``/``queued`` (e.g. after a container restart)."""
        n = 0
        with self.session() as s:
            for row in s.scalars(select(JobRecord).where(
                    JobRecord.status.in_(("running", "queued")))):
                if older_than and (row.heartbeat or row.created) > (_now() - older_than):
                    continue
                row.status = "interrupted"
                row.error = row.error or "interrupted by a restart"
                row.finished = row.finished or _now()
                n += 1
        return n

    # ---- transfers -------------------------------------------------------
    def record_transfer(self, tr: dict, project_id: int | None = None) -> dict:
        with self.session() as s:
            tid = str(tr["id"])
            row = s.get(TransferRecord, tid)
            if row is None:
                row = TransferRecord(id=tid)
                s.add(row)
            params = tr.get("params") or {}
            # a snapshot may be partial (the receive path only knows progress),
            # so an absent key must never blank out what the transmit path stored
            def _s(key: str, default: str = "") -> str:
                val = tr.get(key)
                return str(val) if val else (getattr(row, key, "") or default)

            row.name = _s("name")
            row.sha1 = _s("sha1")
            row.modulation = _s("modulation")
            row.ecc = _s("ecc")
            row.resolution = _s("resolution")
            finder = tr.get("finder")
            if isinstance(finder, dict):
                finder = finder.get("shape")
            row.finder = (str(finder) if finder
                          else (row.finder or "none"))
            for key, default in (("bytes", 0), ("frames", 0), ("received", 0),
                                 ("duplicates", 0), ("failed", 0)):
                val = tr.get(key)
                if val is not None:
                    setattr(row, key, int(val))
                elif getattr(row, key) is None:
                    setattr(row, key, default)
            if params:
                row.params = _dumps({**_loads(row.params, {}), **params})
            if "complete" in tr and tr["complete"] is not None:
                row.complete = bool(tr["complete"])
            if project_id is not None:
                row.project_id = project_id
            s.flush()
            return row.as_dict()

    def list_transfers(self, limit: int = 50, project_id: int | None = None) -> list[dict]:
        with self.session() as s:
            q = select(TransferRecord).order_by(
                TransferRecord.created.desc()).limit(limit)
            if project_id is not None:
                q = q.where(TransferRecord.project_id == project_id)
            return [r.as_dict() for r in s.scalars(q)]

    # ---- events ----------------------------------------------------------
    def _event(self, s: Session, kind: str, subject: str, project_id: int | None,
               detail: dict) -> None:
        s.add(Event(kind=kind, subject=subject[:200], project_id=project_id,
                    detail=_dumps(detail)))

    def log_event(self, kind: str, subject: str = "", project_id: int | None = None,
                  detail: dict | None = None) -> None:
        with self.session() as s:
            self._event(s, kind, subject, project_id, detail or {})

    def list_events(self, limit: int = 100, kind: str | None = None) -> list[dict]:
        with self.session() as s:
            q = select(Event).order_by(Event.ts.desc()).limit(limit)
            if kind:
                q = q.where(Event.kind == kind)
            return [e.as_dict() for e in s.scalars(q)]

    # ---- helpers ---------------------------------------------------------
    def _apply_project(self, s: Session, row, fields: dict) -> None:
        """Resolve ``project``/``project_id`` given either a slug or an int."""
        key = fields.get("project", fields.get("project_id"))
        if key is None:
            return
        p = self._project(s, key)
        if p is None:
            raise KeyError(f"no project {key!r}")
        row.project_id = p.id

    def counts(self) -> dict:
        with self.session() as s:
            return {name: s.scalar(select(func.count()).select_from(model)) or 0
                    for name, model in (("projects", Project), ("datasets", Dataset),
                                        ("runs", Run), ("models", ModelVersion),
                                        ("jobs", JobRecord), ("transfers", TransferRecord),
                                        ("events", Event))}

    # ---- bootstrap -------------------------------------------------------
    def sync_from_disk(self, registry) -> dict:
        """Import artefacts that already exist on disk into the database.

        A deployment can be handed a ``/data`` volume full of datasets, runs and
        models that predate the database (every deployment before this change).
        Rather than making the user re-create anything, boot imports what it
        finds — idempotently, so a restart is a no-op.
        """
        added = {"datasets": 0, "runs": 0, "models": 0}
        for ds in registry.list_datasets():
            if self.get_dataset(ds["name"]):
                continue
            self.upsert_dataset(ds["name"], source="unknown", images=ds.get("images", 0),
                                path=ds.get("path", ""), yaml=ds.get("yaml", ""),
                                splits=ds.get("splits", {}), bytes=ds.get("bytes", 0),
                                status="ready")
            added["datasets"] += 1
        for run in registry.list_runs():
            if self.get_run(run["name"]):
                continue
            self.upsert_run(run["name"], dataset=str(run.get("dataset") or ""),
                            status="ok" if run.get("has_weights") else "failed",
                            metrics=run.get("metrics") or {},
                            weights=str(Path(run.get("path", "")) / "weights" / "best.pt"),
                            path=run.get("path", ""))
            added["runs"] += 1
        for model in registry.list_models():
            if model["name"] in {m["name"] for m in self.list_models()}:
                if model.get("active"):
                    self.activate_model(model["name"])
                continue
            self.promote_model(model["name"], run=(model.get("card") or {}).get("run", ""),
                               weights=model.get("weights", ""),
                               card=model.get("card") or {},
                               metrics=model.get("metrics") or {})
            if not model.get("active"):
                with self.session() as s:
                    row = s.scalar(select(ModelVersion).where(
                        ModelVersion.name == model["name"]))
                    if row is not None:
                        row.active = False
            added["models"] += 1
        active = registry.active()
        if active and self.active_model() is None:
            self.activate_model(active["name"])
        interrupted = self.mark_interrupted()
        if interrupted:
            added["interrupted"] = interrupted
        return added


def _slugify(text: str) -> str:
    """A URL/file-safe project key: ``v0.3 — HD frames`` -> ``v0.3-hd-frames``."""
    keep = []
    for ch in str(text).strip().lower():
        if ch.isalnum() or ch == ".":
            keep.append(ch)
        elif ch in "-_ ":
            keep.append("-")
    slug = "".join(keep).strip("-.")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if not slug:
        raise ValueError("project name must contain letters or digits")
    return slug[:80]


# --------------------------------------------------------------------------- #
# process-wide singleton (the app, the CLI and the tests all want one)
# --------------------------------------------------------------------------- #
_STORE: Store | None = None


def get_store(create: bool = True) -> Store | None:
    """The suite's store, or ``None`` when databases are disabled.

    ``SUITE_DB=0`` turns persistence off entirely (useful for a throwaway demo
    or for a test that wants the pure-filesystem behaviour).
    """
    global _STORE
    if os.environ.get("SUITE_DB", "1") in ("0", "false", "off"):
        return None
    if _STORE is None:
        _STORE = Store(create=create)
    return _STORE


def reset_store(url: str | None = None) -> Store | None:
    """Point the process at a different database (tests, one-off scripts)."""
    global _STORE
    _STORE = None if url == "none" else Store(url=url)
    return _STORE
