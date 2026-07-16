"""Tests for scripts/kubeconform-skipped.py (flux-lint's unvalidated-kind tracker).

Run via `pytest scripts/` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "kubeconform-skipped.py"

_spec = importlib.util.spec_from_file_location("kubeconform_skipped", _SCRIPT)
assert _spec and _spec.loader
ks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ks)


def test_lists_distinct_skipped_kinds():
    payload = {
        "resources": [
            {"version": "v1", "kind": "ConfigMap", "status": "statusValid"},
            {"version": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "status": "statusSkipped"},
            {"version": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "status": "statusSkipped"},
            {"version": "example.com/v1", "kind": "Widget", "status": "statusSkipped"},
        ]
    }
    assert ks.skipped_kinds(payload) == [
        "example.com/v1/Widget",
        "helm.toolkit.fluxcd.io/v2/HelmRelease",
    ]


def test_no_skips_returns_empty():
    payload = {"resources": [{"version": "v1", "kind": "Service", "status": "statusValid"}]}
    assert ks.skipped_kinds(payload) == []


def test_missing_resources_key_is_safe():
    assert ks.skipped_kinds({}) == []


def test_unknown_fields_fall_back_to_placeholder():
    payload = {"resources": [{"status": "statusSkipped"}]}
    assert ks.skipped_kinds(payload) == ["?/?"]
