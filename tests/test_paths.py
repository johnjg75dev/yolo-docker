"""Tests for the shared path-validation module (audit L-15).

These are adversarial: every case here is either a live traversal attempt or a
name that used to be accepted by one of the four divergent validators.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optical_modem.suite.paths import (  # noqa: E402
    UnsafePath,
    dataset_child,
    dataset_root,
    resolve_within,
    safe_component,
    safe_image_name,
    safe_split,
)


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "root"
    (d / "images" / "train").mkdir(parents=True)
    (d / "images" / "train" / "a.jpg").write_bytes(b"x")
    return d


# --------------------------------------------------------------------------- #
# safe_component
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [
    "../etc",          # parent traversal
    "..",              # parent itself
    ".",               # current dir
    "a/b",             # nested
    "a\\b",            # windows separator
    "/etc/passwd",     # absolute
    ".hidden",         # hidden
    "",                # empty
    "a\x00b",          # NUL
    "a\x01b",          # control char
    "x" * 121,         # too long
])
def test_rejects_unsafe_components(bad):
    with pytest.raises(UnsafePath):
        safe_component(bad)


@pytest.mark.parametrize("good", ["data", "my-dataset", "run_2024", "a.b.c", "x" * 120])
def test_accepts_safe_components(good):
    assert safe_component(good) == good


def test_rejects_non_string():
    with pytest.raises(UnsafePath):
        safe_component(None)
    with pytest.raises(UnsafePath):
        safe_component(123)


def test_unsafe_path_is_a_value_error():
    """Existing `except ValueError` handlers in the routes must keep working."""
    with pytest.raises(ValueError):
        safe_component("../x")


# --------------------------------------------------------------------------- #
# splits / image names
# --------------------------------------------------------------------------- #
def test_splits():
    assert safe_split("train") == "train"
    assert safe_split("val") == "val"
    assert safe_split("test", editable=False) == "test"
    with pytest.raises(UnsafePath):
        safe_split("test", editable=True)      # read-only split
    with pytest.raises(UnsafePath):
        safe_split("validation")


def test_image_names():
    assert safe_image_name("frame.jpg") == "frame.jpg"
    for bad in ("frame.txt", "frame", "frame.exe", "../frame.jpg"):
        with pytest.raises(UnsafePath):
            safe_image_name(bad)


# --------------------------------------------------------------------------- #
# resolve_within
# --------------------------------------------------------------------------- #
def test_resolve_within_happy_path(root):
    p = resolve_within(root, "images", "train", "a.jpg")
    assert p.is_file() and p.name == "a.jpg"


def test_resolve_within_rejects_escape(root):
    with pytest.raises(UnsafePath):
        resolve_within(root, "..", "..", "etc", "passwd", must_exist=False)


def test_resolve_within_rejects_symlinked_component(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    (root / "link").symlink_to(outside)
    # the leaf is a symlink
    with pytest.raises(UnsafePath):
        resolve_within(root, "link", must_exist=False)
    # and so is a symlinked *file* deeper in the path
    with pytest.raises(UnsafePath):
        resolve_within(root, "link", "secret.txt", must_exist=False)


def test_resolve_within_missing(root):
    with pytest.raises(UnsafePath):
        resolve_within(root, "nope.jpg")
    # ...but allowed when the caller is creating it
    p = resolve_within(root, "new.jpg", must_exist=False)
    assert p.name == "new.jpg"


def test_symlinks_are_always_rejected(root, tmp_path):
    """No caller in this codebase needs a symlink, so there is no opt-out."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    (root / "link").symlink_to(outside)
    for parts in (("link",), ("link", "secret.txt")):
        with pytest.raises(UnsafePath):
            resolve_within(root, *parts, must_exist=False)
    # a symlink that points *inside* the root is still refused: it is the
    # writing-after-validation case that matters, not where it points today
    (root / "inner").mkdir()
    (root / "images" / "train" / "b.jpg").write_bytes(b"x")
    (root / "inner" / "ok").symlink_to(root / "images" / "train" / "b.jpg")
    with pytest.raises(UnsafePath):
        resolve_within(root, "inner", "ok", must_exist=False)


# --------------------------------------------------------------------------- #
# registry-shaped helpers
# --------------------------------------------------------------------------- #
class _FakeRegistry:
    def __init__(self, datasets):
        self.datasets = datasets


def test_dataset_root_and_child(tmp_path):
    (tmp_path / "ds1" / "labels").mkdir(parents=True)
    reg = _FakeRegistry(tmp_path)
    assert dataset_root(reg, "ds1").is_dir()
    assert dataset_child(reg, "ds1", "labels").name == "labels"
    with pytest.raises(UnsafePath):
        dataset_root(reg, "../ds1")
    with pytest.raises(UnsafePath):
        dataset_child(reg, "ds1", "..", "..", "etc", must_exist=False)
