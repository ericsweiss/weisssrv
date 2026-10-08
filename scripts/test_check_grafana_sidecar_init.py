"""Failure-path tests for scripts/check-grafana-sidecar-init.py.

A k8s-sidecar init container that watches instead of listing never exits, so the
gate must report it; helm is stubbed, so these cases need no chart pull.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from script_loader import load_path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
GATE = SCRIPTS / "check-grafana-sidecar-init.py"


@pytest.fixture(scope="module")
def gate():
    return load_path(GATE)


def _deployment(method: str | None, container: str = "grafana-init-sc-datasources") -> dict:
    env = [{"name": "RESOURCE", "value": "configmap"}]
    if method is not None:
        env.append({"name": "METHOD", "value": method})
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "kube-prometheus-stack-grafana"},
        "spec": {"template": {"spec": {
            "initContainers": [{"name": container, "env": env}],
            "containers": [{"name": "grafana"}],
        }}},
    }


class TestInitSidecarScan:
    def test_a_listing_init_container_is_clean(self, gate):
        found = gate.init_sidecars([_deployment("LIST")])
        assert found == [("kube-prometheus-stack-grafana",
                          "grafana-init-sc-datasources", "LIST")]

    def test_a_watching_init_container_is_reported(self, gate):
        (_, _, method), = gate.init_sidecars([_deployment("WATCH")])
        assert method == "WATCH"

    def test_a_container_without_the_sidecar_marker_is_not_scanned(self, gate):
        """Mirrors the chart: only `*-sc-*` containers are k8s-sidecar."""
        assert gate.init_sidecars([_deployment("WATCH", container="init-chown-data")]) == []

    def test_a_long_running_sidecar_is_not_scanned(self, gate):
        """The dashboards sidecar is meant to WATCH; only INIT mode must list."""
        doc = {
            "kind": "Deployment",
            "metadata": {"name": "grafana"},
            "spec": {"template": {"spec": {"containers": [
                {"name": "grafana-sc-dashboard",
                 "env": [{"name": "METHOD", "value": "WATCH"}]},
            ]}}},
        }
        assert gate.init_sidecars([doc]) == []


# A values block small enough to read, carrying the one knob under test.
VALUES = {
    "grafana": {"sidecar": {"datasources": {
        "resource": "configmap",
        "initDatasources": True,
    }}},
}


def _fixture_repo(tmp_path: Path, watch_method: str | None) -> Path:
    """A tree with just the three files the gate reads."""
    values = yaml.safe_load(yaml.safe_dump(VALUES))
    if watch_method is not None:
        values["grafana"]["sidecar"]["datasources"]["watchMethod"] = watch_method
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "kube-prometheus-stack", "namespace": "observability"},
        "spec": {
            "targetNamespace": "observability",
            "chart": {"spec": {"chart": "kube-prometheus-stack",
                               "version": "${helm_chart_versions_kube_prometheus_stack}"}},
            "values": values,
        },
    }
    manifest = tmp_path / "kubernetes/infrastructure/observability/kube-prometheus-stack"
    manifest.mkdir(parents=True)
    (manifest / "release.yaml").write_text(yaml.safe_dump(release, sort_keys=False))
    sources = tmp_path / "kubernetes/infrastructure/sources"
    sources.mkdir(parents=True)
    (sources / "versions-configmap.yaml").write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "cluster-versions"},
        "data": {"helm_chart_versions_kube_prometheus_stack": "92.1.1",
                 "k3s_version": "v1.37.1+k3s1"},
    }))
    (sources / "cluster-config.yaml").write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "cluster-config"},
        "data": {"cluster_internal_domain": "esweiss.test"},
    }))
    return tmp_path


def _stub_helm(tmp_path: Path) -> Path:
    """A `helm` that renders the init container METHOD straight from the values.

    Stands in for the chart's own template, so these cases exercise the gate
    rather than the network.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "helm"
    stub.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import sys, yaml
        argv = sys.argv[1:]
        values = yaml.safe_load(open(argv[argv.index("-f") + 1]).read()) or {}
        sidecar = values["grafana"]["sidecar"]["datasources"]
        env = [{"name": "RESOURCE", "value": sidecar["resource"]}]
        # The chart's own default for an unset watchMethod.
        env.append({"name": "METHOD", "value": sidecar.get("watchMethod", "WATCH")})
        print(yaml.safe_dump({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "kube-prometheus-stack-grafana"},
            "spec": {"template": {"spec": {
                "initContainers": [{"name": "grafana-init-sc-datasources", "env": env}],
                "containers": [{"name": "grafana"}],
            }}},
        }))
        '''))
    stub.chmod(0o755)
    return bin_dir


def _run(root: Path, bin_dir: Path, env_extra=None) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(GATE), "--repo-root", str(root)],
        capture_output=True, text=True, check=False, cwd=REPO, env=env,
    )


def test_an_unset_watch_method_is_a_finding(tmp_path):
    """Mutation case: the shape that wedged the grafana pod on 92.1.1."""
    result = _run(_fixture_repo(tmp_path / "tree", None), _stub_helm(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "METHOD=WATCH, not LIST" in result.stdout


def test_an_explicit_watch_is_a_finding(tmp_path):
    result = _run(_fixture_repo(tmp_path / "tree", "WATCH"), _stub_helm(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr


def test_list_passes(tmp_path):
    result = _run(_fixture_repo(tmp_path / "tree", "LIST"), _stub_helm(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_missing_manifest_is_exit_2_not_a_finding(tmp_path):
    """The could-not-inspect arm: 1 would read as a policy violation."""
    (tmp_path / "tree").mkdir()
    result = _run(tmp_path / "tree", _stub_helm(tmp_path))
    assert result.returncode == 2, result.stdout + result.stderr


def test_a_render_with_no_init_sidecar_is_exit_2_not_a_pass(tmp_path):
    """A gate that inspects nothing is not a gate: a renamed container is an
    operator error, not silent coverage loss."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "helm"
    stub.write_text("#!/bin/sh\necho 'apiVersion: v1'\necho 'kind: ConfigMap'\n"
                    "echo 'metadata:'\necho '  name: nothing'\n")
    stub.chmod(0o755)
    result = _run(_fixture_repo(tmp_path / "tree", "LIST"), bin_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "checked nothing" in result.stderr


def test_the_real_release_pins_list_for_every_init_sidecar():
    """The values-level invariant, with no chart pull: an init sidecar the real
    HelmRelease enables must carry watchMethod: LIST."""
    text = (REPO / "kubernetes/infrastructure/observability/kube-prometheus-stack"
            / "release.yaml").read_text(encoding="utf-8")
    sidecar = yaml.safe_load(text)["spec"]["values"]["grafana"]["sidecar"]
    enabled = {
        kind: block
        for kind, block in sidecar.items()
        if isinstance(block, dict) and block.get(f"init{kind.capitalize()}") is True
    }
    assert enabled, "no grafana sidecar runs in init mode — drop this assertion with the knob"
    wrong = {kind: block.get("watchMethod") for kind, block in enabled.items()
             if block.get("watchMethod") != "LIST"}
    assert not wrong, (
        "a grafana sidecar runs in init mode without watchMethod: LIST, so its init "
        f"container watches forever and the pod never leaves PodInitializing: {wrong}"
    )
