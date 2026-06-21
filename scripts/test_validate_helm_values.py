#!/usr/bin/env python3
"""Unit tests for scripts/validate-helm-values.py (pure, network-free helpers).

The script's substitute()/load_versions()/extract_helmrelease() helpers do the
placeholder resolution and manifest parsing that gate helm-template validation.
A regression here (e.g. silently dropping an unresolved placeholder, or picking
the wrong document out of a multi-doc manifest) would let a broken release slip
through, so they are unit-tested independently of the network-bound `helm` path.

Run with pytest:
    pytest scripts/test_validate_helm_values.py -v
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# The module filename has a hyphen, so import it by path rather than `import`.
_SRC = Path(__file__).resolve().parent / "validate-helm-values.py"
_spec = importlib.util.spec_from_file_location("validate_helm_values", _SRC)
vhv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vhv)


# --- substitute() ----------------------------------------------------------

class TestSubstitute:
    def test_resolves_known_keys(self):
        text, missing = vhv.substitute("tag: ${foo}", {"foo": "1.2.3"})
        assert text == "tag: 1.2.3"
        assert missing == []

    def test_unknown_key_reported_and_left_literal(self):
        text, missing = vhv.substitute("tag: ${bar}", {"foo": "1.2.3"})
        # Unknown placeholder is left verbatim and surfaced in `missing`.
        assert text == "tag: ${bar}"
        assert missing == ["bar"]

    def test_mixed_known_and_unknown(self):
        text, missing = vhv.substitute("${a}-${b}", {"a": "x"})
        assert text == "x-${b}"
        assert missing == ["b"]

    def test_missing_keys_deduped_and_sorted(self):
        _, missing = vhv.substitute("${z} ${a} ${z}", {})
        assert missing == ["a", "z"]

    def test_noop_on_placeholder_free_text(self):
        text, missing = vhv.substitute("no placeholders here", {"foo": "1"})
        assert text == "no placeholders here"
        assert missing == []


# --- load_versions() -------------------------------------------------------

class TestLoadVersions:
    def _write_cm(self, tmp_path: Path, body: str) -> Path:
        # load_versions() resolves VERSIONS_CONFIGMAP relative to repo_root.
        cm = tmp_path / vhv.VERSIONS_CONFIGMAP
        cm.parent.mkdir(parents=True, exist_ok=True)
        cm.write_text(body)
        return tmp_path

    def test_returns_stringified_data(self, tmp_path):
        root = self._write_cm(
            tmp_path,
            "apiVersion: v1\nkind: ConfigMap\ndata:\n  foo: 1.2.3\n  num: 7\n",
        )
        versions = vhv.load_versions(str(root))
        assert versions == {"foo": "1.2.3", "num": "7"}

    def test_raises_on_empty_data(self, tmp_path):
        root = self._write_cm(tmp_path, "apiVersion: v1\nkind: ConfigMap\ndata: {}\n")
        with pytest.raises(SystemExit):
            vhv.load_versions(str(root))

    def test_raises_on_absent_data(self, tmp_path):
        root = self._write_cm(tmp_path, "apiVersion: v1\nkind: ConfigMap\n")
        with pytest.raises(SystemExit):
            vhv.load_versions(str(root))

    def test_raises_on_non_mapping(self, tmp_path):
        # A non-dict top-level doc must fail cleanly, not AttributeError.
        root = self._write_cm(tmp_path, "- not\n- a\n- mapping\n")
        with pytest.raises(SystemExit):
            vhv.load_versions(str(root))


# --- extract_helmrelease() -------------------------------------------------

class TestExtractHelmRelease:
    def test_returns_first_helmrelease(self, tmp_path):
        manifest = tmp_path / "release.yaml"
        manifest.write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cm\n"
            "---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: first\n"
            "---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: second\n"
        )
        hr = vhv.extract_helmrelease(str(manifest))
        assert hr["metadata"]["name"] == "first"

    def test_raises_when_none_present(self, tmp_path):
        manifest = tmp_path / "release.yaml"
        manifest.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cm\n")
        with pytest.raises(SystemExit):
            vhv.extract_helmrelease(str(manifest))

    def test_skips_non_dict_docs(self, tmp_path):
        # A non-dict doc (e.g. a list) must be skipped, not raise AttributeError.
        manifest = tmp_path / "release.yaml"
        manifest.write_text(
            "- a\n- list\n"
            "---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: only\n"
        )
        hr = vhv.extract_helmrelease(str(manifest))
        assert hr["metadata"]["name"] == "only"

    def test_from_text_returns_first_helmrelease(self):
        hr = vhv.extract_helmrelease_from_text(
            "apiVersion: v1\nkind: ConfigMap\n---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: first\n",
            "inline",
        )
        assert hr["metadata"]["name"] == "first"

    def test_text_substitution_preserves_quoted_string_type(self):
        # Substituting the raw text (like Flux) must keep a quoted placeholder a
        # string even when the value is numeric — not re-parse it as a number.
        text = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            'spec:\n  values:\n    tag: "${v}"\n'
        )
        rendered, missing = vhv.substitute(text, {"v": "1.36"})
        assert missing == []
        hr = vhv.extract_helmrelease_from_text(rendered, "inline")
        assert hr["spec"]["values"]["tag"] == "1.36"
        assert isinstance(hr["spec"]["values"]["tag"], str)


def _deploy(limits: dict) -> list:
    return [{
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "app", "namespace": "ns"},
        "spec": {"template": {"spec": {"containers": [
            {"name": "c", "resources": {"limits": limits}}]}}},
    }]


class TestRenderedCpuLimitPolicy:
    """validate-helm-values reuses check-hpa-vpa-invariant's CPU-limit scanner +
    allowlist (loaded via importlib) so the kustomize-side and helm-rendered-side
    no-CPU-limits checks can never diverge. Smoke-test that wiring."""

    def test_shared_module_loaded(self):
        assert hasattr(vhv, "_hpa")
        assert callable(vhv._hpa._cpu_limit_violations)
        assert isinstance(vhv._hpa.CPU_LIMIT_ALLOWLIST, set)

    def test_flags_rendered_cpu_limit(self):
        v = vhv._hpa._cpu_limit_violations(_deploy({"cpu": "500m"}))
        assert len(v) == 1
        assert "ns/Deployment/app" in v[0] and "limits.cpu=500m" in v[0]

    def test_memory_only_and_null_cpu_are_clean(self):
        assert vhv._hpa._cpu_limit_violations(_deploy({"memory": "256Mi"})) == []
        assert vhv._hpa._cpu_limit_violations(_deploy({"cpu": None})) == []

    def test_allowlist_suppresses_violation(self, monkeypatch):
        monkeypatch.setattr(vhv._hpa, "CPU_LIMIT_ALLOWLIST", {"ns/Deployment/app"})
        assert vhv._hpa._cpu_limit_violations(_deploy({"cpu": "250m"})) == []


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
