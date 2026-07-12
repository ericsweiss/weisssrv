"""Tests for scripts/flux-render.sh (the shared versions-extraction helper).

Invokes the script as a subprocess (the same way Taskfile + CI call it) and
checks the eval-able export output and the derived kubeconform version.

Run via `task scripts:test` (pytest).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "flux-render.sh"

SAMPLE_CM = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-versions
  namespace: flux-system
data:
  k3s_version: v1.36.2+k3s1
  traefik_version: "41.0.2"
  gluetun_version: v3.40.0
"""


def _run(args, **kw):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        **kw,
    )


@pytest.fixture()
def cm(tmp_path: Path) -> Path:
    p = tmp_path / "versions-configmap.yaml"
    p.write_text(SAMPLE_CM)
    return p


class TestExportVersions:
    def test_emits_exports_for_each_key(self, cm: Path):
        r = _run(["export-versions", str(cm)])
        assert r.returncode == 0, r.stderr
        assert "export k3s_version=" in r.stdout
        assert "export traefik_version=" in r.stdout
        assert "export gluetun_version=" in r.stdout

    def test_emits_envsubst_allowlist(self, cm: Path):
        r = _run(["export-versions", str(cm)])
        assert "export FLUX_ENVSUBST_VARS=" in r.stdout
        assert "${traefik_version}" in r.stdout

    def test_output_is_evalable(self, cm: Path):
        # Pipe the exports through a fresh shell and echo a resolved value.
        r = _run(["export-versions", str(cm)])
        check = subprocess.run(
            ["bash", "-c", f'eval "$1"; echo "$traefik_version"', "_", r.stdout],
            capture_output=True,
            text=True,
        )
        assert check.stdout.strip() == "41.0.2"

    def test_missing_configmap_fails(self, tmp_path: Path):
        r = _run(["export-versions", str(tmp_path / "nope.yaml")])
        assert r.returncode != 0
        assert "not found" in r.stderr

    def test_empty_data_fails(self, tmp_path: Path):
        p = tmp_path / "empty.yaml"
        p.write_text("apiVersion: v1\nkind: ConfigMap\ndata: {}\n")
        r = _run(["export-versions", str(p)])
        assert r.returncode != 0


class TestK8sVersion:
    def test_derives_major_minor_zero(self, cm: Path):
        r = _run(["k8s-version", str(cm)])
        assert r.returncode == 0
        assert r.stdout.strip() == "1.36.0"

    def test_defaults_when_no_k3s_version(self, tmp_path: Path):
        p = tmp_path / "cm.yaml"
        p.write_text("data:\n  traefik_version: '1.0.0'\n")
        r = _run(["k8s-version", str(p)])
        assert r.stdout.strip() == "1.36.0"


class TestCli:
    def test_unknown_subcommand_fails(self, cm: Path):
        r = _run(["bogus", str(cm)])
        assert r.returncode != 0
        assert "unknown subcommand" in r.stderr
