"""One implementation of "is this name safe, and does it stay inside the root?".

Before this module the same rule was written four times with slightly different
constants -- ``app._safe_name``, ``app._dataset_path``, ``crop.paths`` and
``curate.curated_copy`` -- and they had drifted (``_safe_name`` capped names at
120 characters while ``curate_dataset`` re-implemented the check with its own
inline expression).  Every handler now calls :func:`safe_component` and
:func:`resolve_within`, so the containment rule exists in exactly one place and
is tested once.

The threat model is a logged-in user who controls a dataset name, a split, or an
image filename and wants to reach a path outside ``DATA_DIR/datasets``.  Three
properties are enforced together, because each alone is insufficient:

* **no separators** -- the component must equal ``Path(x).name``, which rejects
  ``../``, ``a/b`` and ``\\``;
* **no symlink traversal** -- the resolved path must stay under the root, and
  symlinked components are rejected outright rather than resolved;
* **no hidden/absolute/control characters** -- leading dots, absolute paths,
  NUL and other control bytes are rejected.
"""

from __future__ import annotations

import os
from pathlib import Path

#: splits the dataset tooling understands
SPLITS = ("train", "val", "test")
#: splits that may be edited (``test`` is read-only: it has no ground truth to lose)
EDITABLE_SPLITS = ("train", "val")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

MAX_COMPONENT_LEN = 120


class UnsafePath(ValueError):
    """A user-supplied path component was rejected.

    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers in
    the route layer keep working unchanged.
    """


def safe_component(name: object, *, max_len: int = MAX_COMPONENT_LEN) -> str:
    """Validate one path component (a dataset name or an image filename).

    Returns the name unchanged, or raises :class:`UnsafePath`.  Note that
    ``max_len`` counts *characters*, and the caller is responsible for the
    filesystem's own byte limit.
    """
    if not isinstance(name, str):
        raise UnsafePath("path component must be a string")
    if not name or name in (".", ".."):
        raise UnsafePath("empty or relative path component")
    if len(name) > max_len:
        raise UnsafePath(f"path component is longer than {max_len} characters")
    if name.startswith("."):
        raise UnsafePath("hidden path components are not allowed")
    if "\x00" in name or any(ord(ch) < 32 for ch in name):
        raise UnsafePath("control characters are not allowed in path components")
    if "\\" in name or "/" in name or os.path.isabs(name):
        raise UnsafePath("path separators are not allowed in path components")
    if Path(name).name != name:
        raise UnsafePath("path component is not a single name")
    return name


def safe_split(split: object, *, editable: bool = False) -> str:
    """Validate a dataset split name."""
    if not isinstance(split, str) or split not in SPLITS:
        raise UnsafePath(f"split must be one of {', '.join(SPLITS)}")
    if editable and split not in EDITABLE_SPLITS:
        raise UnsafePath(f"split {split!r} is read-only")
    return split


def safe_image_name(name: object) -> str:
    """Validate an image filename (component rules plus a known image suffix)."""
    safe = safe_component(name)
    if Path(safe).suffix.lower() not in IMAGE_SUFFIXES:
        raise UnsafePath("file is not a supported image type")
    return safe


def resolve_within(root: Path, *parts: str, must_exist: bool = True) -> Path:
    """Join ``parts`` onto ``root`` and prove the result stays inside it.

    Symlinks are always rejected, and every component below ``root`` is checked
    rather than just the leaf: a symlinked *directory* part-way along the path is
    the interesting case, and ``Path.resolve()`` would happily follow it.  A
    dataset is a directory users drop files into, so tolerating a symlink is a
    standing invitation to write outside the root later, after validation passed.
    """
    root = Path(root).resolve()
    target = root.joinpath(*parts)
    probe = root
    for part in parts:
        probe = probe / part
        if probe.is_symlink():
            raise UnsafePath(f"symbolic links are not allowed: {part}")
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise UnsafePath("path escapes its root directory")
    if must_exist and not resolved.exists():
        raise UnsafePath("path does not exist")
    return resolved


def dataset_root(registry, name: str) -> Path:
    """The validated directory of a dataset that must already exist."""
    safe_component(name)
    root = Path(registry.datasets).resolve()
    return resolve_within(root, name)


def dataset_child(registry, name: str, *parts: str, **kwargs) -> Path:
    """A validated path inside an existing dataset directory."""
    base = dataset_root(registry, name)
    return resolve_within(base, *parts, **kwargs)
