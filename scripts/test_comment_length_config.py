"""scripts/comment-length.yaml excludes exactly the weisssrv-lib copies.

A vendored file's comments are the library's, so the gate skips those copies.
Every other exclude must sit under `pending_vendor`, awaiting its pin bump.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "scripts" / "comment-length.yaml"
MANIFEST = REPO / "scripts" / "vendored-manifest.yml"


def _config() -> dict:
    doc = yaml.safe_load(CONFIG.read_text())
    assert isinstance(doc, dict), f"{CONFIG} must be a mapping"
    return doc


def _excludes() -> set[str]:
    excluded = set(_config().get("exclude") or [])
    assert excluded, f"{CONFIG} excludes nothing — the vendored copies would be scanned"
    return excluded


def _vendored_paths() -> set[str]:
    doc = yaml.safe_load(MANIFEST.read_text())
    paths = {
        entry if isinstance(entry, str) else (entry.get("consumer") or entry["lib"])
        for section in ("vendored", "forked")
        for entry in doc.get(section) or []
    }
    assert paths, f"{MANIFEST} lists no vendored copies"
    return paths


def _pending_vendor() -> set[str]:
    return set(_config().get("pending_vendor") or [])


def _unaccounted(excluded: set[str], accounted: set[str]) -> list[str]:
    """Excludes that neither the manifest nor `pending_vendor` explains."""
    return sorted(excluded - accounted)


def test_every_vendored_copy_is_excluded() -> None:
    missing = sorted(_vendored_paths() - _excludes())
    assert not missing, (
        f"{CONFIG.name} must exclude every path {MANIFEST.name} declares, or the gate "
        f"fails on comments only a re-vendor can change: {missing}"
    )


def test_every_exclude_names_a_real_file() -> None:
    absent = sorted(path for path in _excludes() if not (REPO / path).exists())
    assert not absent, (
        f"{CONFIG.name} excludes paths that do not exist — a stale exclude drops a file "
        f"from the scan silently: {absent}"
    )


def test_every_exclude_is_a_library_copy_or_pending() -> None:
    unaccounted = _unaccounted(_excludes(), _vendored_paths() | _pending_vendor())
    assert not unaccounted, (
        f"{CONFIG.name} exempts paths that are neither in {MANIFEST.name} nor under "
        f"pending_vendor — a local file cannot be exempted from the comment rule: "
        f"{unaccounted}"
    )


def test_an_unexplained_exclude_is_rejected() -> None:
    assert _unaccounted(
        {"scripts/b2-bucket-drift.py", "scripts/local-only.py"},
        {"scripts/b2-bucket-drift.py"},
    ) == ["scripts/local-only.py"]


def test_pending_vendor_entries_are_also_excluded() -> None:
    missing = sorted(_pending_vendor() - _excludes())
    assert not missing, (
        f"{CONFIG.name} lists pending_vendor paths it does not exclude, so the gate "
        f"scans comments the next re-vendor overwrites: {missing}"
    )


def test_the_whole_tree_is_in_scope() -> None:
    assert _config().get("paths") == ["."], (
        f"{CONFIG.name} must scan the repository root: the comment rule applies to every "
        f"file, and a narrowed path list lets an over-long block land outside it"
    )
