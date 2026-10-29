"""Where the suite keeps its artefacts, and how a model becomes "the" model.

Layout under ``DATA_DIR`` (a named volume in the container)::

    datasets/<name>/            YOLO-pose dataset (images/ labels/ data.yaml)
    runs/<name>/                one training run (ultralytics project dir)
    models/<name>/weights/best.pt + model_card.json   promoted models
    models/active.json          which model the S2C receiver + labeller use
    jobs/<id>/                  job.json + log.txt
    sessions/<id>/              capture-station sessions (photos)

Everything here is plain files: you can copy a run to another machine, delete a
dataset, or drop a hand-trained ``best.pt`` into ``models/`` and promote it from
the UI.  The registry never imports torch — it only moves files around — so the
web worker stays light even while a training job is running.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
class Registry:
    def __init__(self, root: str | Path, project_root: str | Path | None = None):
        self.root = Path(root)
        #: where the code (and any model that shipped with the image) lives
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self.datasets = self.root / "datasets"
        self.runs = self.root / "runs"
        self.models = self.root / "models"
        self.jobs = self.root / "jobs"
        self.sessions = self.root / "sessions"
        for path in (self.datasets, self.runs, self.models, self.jobs, self.sessions):
            path.mkdir(parents=True, exist_ok=True)

    # ---- misc state ------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def set_state(self, **kw) -> dict:
        state = self.state()
        state.update(kw)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(self.state_path)
        return state

    def disk(self) -> dict:
        try:
            st = os.statvfs(self.root)
            free = st.f_bavail * st.f_frsize
            total = st.f_blocks * st.f_frsize
        except OSError:                       # pragma: no cover - non-POSIX
            free = total = 0
        used = 0
        for path in (self.datasets, self.runs, self.models, self.sessions):
            for dirpath, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        used += os.path.getsize(os.path.join(dirpath, name))
                    except OSError:
                        pass
        return {"free_bytes": free, "total_bytes": total, "used_bytes": used}

    # ------------------------------------------------------------------ #
    # datasets
    # ------------------------------------------------------------------ #
    def dataset_dir(self, name: str) -> Path:
        return self.datasets / name

    @staticmethod
    def yaml_of(path: Path) -> Path | None:
        if path.is_file() and path.suffix in (".yaml", ".yml"):
            return path
        for cand in ("data.yaml", "optical_frames.yaml"):
            if (path / cand).exists():
                return path / cand
        found = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
        return found[0] if found else None

    def dataset_summary(self, path: str | Path, sanity: bool = True) -> dict:
        """Counts, splits and a quick label sanity check for one dataset."""
        p = Path(path)
        counts: dict[str, dict] = {}
        for split in ("train", "val", "test"):
            imgs = p / "images" / split
            labels = p / "labels" / split
            if not imgs.is_dir():
                continue
            names = [f.name for f in imgs.iterdir() if f.suffix.lower() in IMAGE_EXT]
            counts[split] = {
                "images": len(names),
                "labels": len(list(labels.glob("*.txt"))) if labels.is_dir() else 0,
                "bytes": sum(f.stat().st_size for f in imgs.iterdir()
                             if f.suffix.lower() in IMAGE_EXT),
            }
        yaml_path = self.yaml_of(p)
        classes, keypoints, ok_labels, bad_labels = 0, 0, 0, 0
        if sanity and counts:
            checked = 0
            for split, c in counts.items():
                for label in sorted((p / "labels" / split).glob("*.txt")):
                    if checked >= 200:
                        break
                    txt = label.read_text().strip()
                    if not txt:
                        bad_labels += 1
                        continue
                    parts = txt.split("\n")[0].split()
                    if len(parts) != 5 + 4 * 3:
                        bad_labels += 1
                    else:
                        ok_labels += 1
                        keypoints = 4
                        classes = max(classes, 1)
                    checked += 1
        summary_meta: dict = {}
        skipped_total = 0
        if (p / "summary.json").is_file():
            try:
                summary_meta = json.loads((p / "summary.json").read_text())
                for sp in ("train", "val", "test"):
                    if isinstance(summary_meta.get(sp), dict):
                        skipped_total += int(summary_meta[sp].get("skipped", 0) or 0)
            except (OSError, json.JSONDecodeError, ValueError):
                summary_meta = {}
        return {
            "name": p.name,
            "path": str(p),
            "splits": counts,
            "images": sum(c["images"] for c in counts.values()),
            "labels": sum(c["labels"] for c in counts.values()),
            "bytes": sum(c["bytes"] for c in counts.values()),
            "yaml": str(yaml_path) if yaml_path else None,
            "labels_ok": ok_labels,
            "labels_bad": bad_labels,
            "skipped": skipped_total,
            "summary": summary_meta,
            "classes": classes,
            "keypoints": keypoints,
            "modified": max([c.stat().st_mtime for c in p.iterdir()] or [0]) if p.exists() else 0,
        }

    def list_datasets(self) -> list[dict]:
        out = []
        for p in sorted(self.datasets.iterdir()):
            if not p.is_dir() or not (p / "images").is_dir():
                continue
            out.append(self.dataset_summary(p, sanity=False))
        return out

    def drop_dataset(self, name: str) -> bool:
        p = self.dataset_dir(name)
        if not p.is_dir() or p.parent != self.datasets:
            return False
        shutil.rmtree(p)
        return True

    # ------------------------------------------------------------------ #
    # runs + models
    # ------------------------------------------------------------------ #
    def list_runs(self) -> list[dict]:
        out = []
        for p in sorted(self.runs.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            best = p / "weights" / "best.pt"
            last = p / "weights" / "last.pt"
            card = p / "weights" / "model_card.json"
            if not card.exists():
                card = p / "model_card.json"
            csv_path = p / "results.csv"
            completed_epochs = 0
            csv_best_pose = None
            csv_best_box = None
            if csv_path.exists():
                try:
                    import csv as _csv
                    with csv_path.open(newline="") as fh:
                        rows = list(_csv.DictReader(fh))
                    completed_epochs = len(rows)
                    for r in rows:
                        cleaned = {(k or "").strip(): v for k, v in r.items()}
                        for col, attr in (("metrics/mAP50-95(P)", "pose"),
                                          ("metrics/mAP50-95(B)", "box")):
                            if col in cleaned:
                                try:
                                    val = float(cleaned[col])
                                    if attr == "pose" and (csv_best_pose is None or val > csv_best_pose):
                                        csv_best_pose = val
                                    if attr == "box" and (csv_best_box is None or val > csv_best_box):
                                        csv_best_box = val
                                except (TypeError, ValueError):
                                    pass
                except OSError:
                    pass
            info: dict[str, Any] = {
                "name": p.name,
                "path": str(p),
                "has_weights": best.exists(),
                "has_last": last.exists(),
                "has_eval": (p / "eval.json").exists(),
                "weights_bytes": best.stat().st_size if best.exists() else 0,
                "modified": p.stat().st_mtime,
                "epoch": completed_epochs or None,
                "epochs": completed_epochs or None,
                "status": "done" if best.exists() else ("incomplete" if last.exists() else "pending"),
                "metrics": {},
                "mAP": None,
                "dataset": None,
                "train_args": {},
                "promoted": False,
            }
            try:
                card_json = json.loads(card.read_text())
                info["metrics"] = card_json.get("results", {}) or {}
                targs = card_json.get("train_args", {}) or {}
                info["train_args"] = targs
                info["dataset"] = targs.get("data")
                if targs.get("epochs"):
                    info["epochs"] = int(targs["epochs"])
                    if best.exists() and not info["epoch"]:
                        info["epoch"] = int(targs["epochs"])
            except (OSError, json.JSONDecodeError, ValueError):
                pass
            if csv_best_pose is not None and "pose_map50_95" not in info["metrics"]:
                info["metrics"]["pose_map50_95"] = csv_best_pose
            if csv_best_box is not None and "box_map50_95" not in info["metrics"]:
                info["metrics"]["box_map50_95"] = csv_best_box
            info["mAP"] = (info["metrics"].get("pose_map50_95")
                           if info["metrics"].get("pose_map50_95") is not None
                           else info["metrics"].get("box_map50_95"))
            info["promoted"] = (self.models / p.name / "weights" / "best.pt").exists()
            out.append(info)
        return out

    def list_models(self) -> list[dict]:
        active = self.active() or {}
        out = []
        for p in sorted(self.models.iterdir()):
            if not p.is_dir():
                continue
            best = p / "weights" / "best.pt"
            card = p / "model_card.json"
            card_data: dict = {}
            if card.exists():
                try:
                    card_data = json.loads(card.read_text())
                except (OSError, json.JSONDecodeError):
                    card_data = {}
            out.append({
                "name": p.name,
                "weights": str(best),
                "has_weights": best.exists(),
                "bytes": best.stat().st_size if best.exists() else 0,
                "card": card_data,
                "metrics": card_data.get("results", {}),
                "active": active.get("name") == p.name,
                "modified": p.stat().st_mtime,
            })
        return out

    def promote(self, run_name: str, name: str | None = None) -> dict:
        """Copy a training run's best weights into the model registry + activate."""
        run = self.runs / run_name
        best = run / "weights" / "best.pt"
        if not best.exists():
            best = run / "best.pt"
        if not best.exists():
            raise FileNotFoundError(f"no best.pt in run {run_name}")
        target_name = name or run_name
        dest = self.models / target_name
        (dest / "weights").mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, dest / "weights" / "best.pt")
        for card in (run / "weights" / "model_card.json", run / "model_card.json"):
            if card.exists():
                shutil.copy2(card, dest / "model_card.json")
                break
        card = {}
        try:
            card = json.loads((dest / "model_card.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        card.setdefault("promoted", {})
        card["promoted"] = {"at": time.time(), "from_run": run_name,
                            "weights": str(dest / "weights" / "best.pt")}
        (dest / "model_card.json").write_text(json.dumps(card, indent=1))
        active = {"name": target_name, "weights": str(dest / "weights" / "best.pt"),
                  "promoted": time.time(), "run": run_name,
                  "metrics": card.get("results", {})}
        tmp = self.models / "active.tmp"
        tmp.write_text(json.dumps(active, indent=1))
        tmp.replace(self.models / "active.json")
        self.set_state(active_model=target_name)
        return active

    def activate(self, name: str) -> dict:
        """Make an already-registered (or shipped) model the active one."""
        best = self.models / name / "weights" / "best.pt"
        card_path = self.models / name / "model_card.json"
        shipped = False
        if not best.exists():
            ship_dir = self.project_root / "models" / name
            cand = ship_dir / "weights" / "best.pt"
            if cand.exists():
                best = cand
                card_path = (ship_dir / "weights" / "model_card.json"
                             if (ship_dir / "weights" / "model_card.json").exists()
                             else ship_dir / "model_card.json")
                shipped = True
        if not best.exists():
            raise FileNotFoundError(f"model {name} has no weights")
        card = {}
        try:
            card = json.loads(card_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        active = {"name": name, "weights": str(best), "promoted": time.time(),
                  "shipped": shipped,
                  "run": (card.get("promoted") or {}).get("from_run"),
                  "metrics": card.get("results", {})}
        tmp = self.models / "active.tmp"
        tmp.write_text(json.dumps(active, indent=1))
        tmp.replace(self.models / "active.json")
        self.set_state(active_model=name)
        return active

    def shipped_models(self) -> list[dict]:
        """Models shipped inside the repository/image (read-only baselines)."""
        active = self.active() or {}
        out = []
        ship_root = self.project_root / "models"
        if not ship_root.is_dir():
            return out
        for p in sorted(ship_root.iterdir()):
            if not p.is_dir():
                continue
            best = p / "weights" / "best.pt"
            if not best.exists():
                continue
            card_data: dict = {}
            for cp in (p / "weights" / "model_card.json", p / "model_card.json"):
                if cp.exists():
                    try:
                        card_data = json.loads(cp.read_text())
                        break
                    except (OSError, json.JSONDecodeError):
                        pass
            out.append({
                "name": p.name,
                "weights": str(best),
                "has_weights": True,
                "shipped": True,
                "bytes": best.stat().st_size,
                "card": card_data,
                "metrics": card_data.get("results", {}),
                "active": active.get("name") == p.name and bool(active.get("shipped", True)),
                "modified": best.stat().st_mtime,
            })
        return out

    def active(self) -> dict | None:
        """The model the S2C receiver should use, or ``None``."""
        try:
            data = json.loads((self.models / "active.json").read_text())
        except (OSError, json.JSONDecodeError):
            data = None
        if data and Path(data.get("weights", "")).exists():
            return data
        # fall back to a model that shipped with the image, if any
        for base in (self.models, self.project_root / "models"):
            for cand in sorted(base.glob("*/weights/best.pt")):
                card = cand.parent / "model_card.json"
                info = {"name": cand.parents[1].name, "weights": str(cand),
                        "shipped": base != self.models}
                if card.exists():
                    try:
                        info["metrics"] = json.loads(card.read_text()).get("results", {})
                    except (OSError, json.JSONDecodeError):
                        pass
                return info
        return None

    def bundled_bases(self) -> list[dict]:
        """Checkpoints that shipped with the image, usable as a training start."""
        out = []
        for base in (self.models, self.project_root / "models"):
            for cand in sorted(base.glob("*/weights/best.pt")):
                out.append({"path": str(cand), "name": f"{cand.parents[1].name} (bundled)",
                            "bytes": cand.stat().st_size})
        return out

    def active_weights(self) -> str | None:
        act = self.active()
        return act["weights"] if act else None

    def delete_model(self, name: str) -> bool:
        p = self.models / name
        if not p.is_dir() or p.parent != self.models:
            return False
        if (self.active() or {}).get("name") == name:
            try:
                (self.models / "active.json").unlink()
            except OSError:
                pass
        shutil.rmtree(p)
        return True


def _dist_version(name: str) -> str | None:
    """Installed version from package metadata, without importing the package."""
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def describe_env() -> dict:
    """What the container can actually do — shown in the UI and the guide."""
    import platform

    info: dict[str, Any] = {
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "torch": None,
        "cuda": False,
        "ultralytics": None,
        "cpus_available": None,
    }
    # Read versions from the *metadata*, never by importing: importing torch in
    # the web worker costs ~350 MB RSS on a box where training also wants that
    # memory, and the answer never changes.
    info["torch"] = _dist_version("torch")
    info["ultralytics"] = _dist_version("ultralytics")
    info["opencv"] = _dist_version("opencv-python") or _dist_version("opencv-python-headless")
    if shutil.which("nvidia-smi") and os.environ.get("SUITE_CUDA_PROBE", "1") != "0":
        try:                                  # only a GPU box pays for the import
            import torch

            info["cuda"] = bool(torch.cuda.is_available())
            if info["cuda"]:
                info["gpu"] = torch.cuda.get_device_name(0)
        except Exception:
            info["cuda"] = False
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    info["mem_available_bytes"] = int(line.split()[1]) * 1024
                elif line.startswith("MemTotal:"):
                    info["mem_total_bytes"] = int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        info["cpus_available"] = len(os.sched_getaffinity(0))
    except AttributeError:                    # pragma: no cover
        pass
    return info
