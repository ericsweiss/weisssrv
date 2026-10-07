"""validate-helm-values.py's post-render policy half, driven with stubbed helm.

`task flux:lint` runs the real gate over this cluster's releases, which needs
helm and the chart repos; these pin the arms that decide pass or fail.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

SCRIPTS = Path(__file__).resolve().parent
GATE = SCRIPTS / "validate-helm-values.py"

vhv = load_script("validate-helm-values.py")

VERSIONS = {"demo_version": "1.2.3", "k3s_version": "v1.36.2+k3s1"}

MANIFEST = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: demo
  namespace: demo-ns
spec:
  chart:
    spec:
      chart: demo
      version: "${demo_version}"
  values:
    replicaCount: 1
"""


def _workload(cpu_limit: str | None = None) -> str:
    container: dict = {"name": "app", "image": "demo:1"}
    if cpu_limit is not None:
        container["resources"] = {"limits": {"cpu": cpu_limit}}
    return yaml.safe_dump({
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "demo", "namespace": "demo-ns"},
        "spec": {"template": {"spec": {"containers": [container]}}},
    })


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "release.yaml").write_text(MANIFEST)
    return tmp_path


REL = {"name": "demo", "manifest": "release.yaml",
       "repo_name": "demo-repo", "chart": "demo"}


def _validate(monkeypatch, repo: Path, rendered: str, *, allowlist=None,
              manifest: str = MANIFEST, helm_rc: int = 0):
    """Run validate_release with `helm template` stubbed out. Returns (ok, cmd)."""
    (repo / "release.yaml").write_text(manifest)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, helm_rc, stdout=rendered, stderr="boom")

    monkeypatch.setattr(vhv.subprocess, "run", fake_run)
    ok = vhv.validate_release(REL, VERSIONS, str(repo), False, "1.36.2", allowlist)
    return ok, (calls[0] if calls else [])


class TestReleaseIdentity:
    """Charts that key off .Release.Name/.Release.Namespace must render the
    same object Flux applies."""

    def test_a_clean_release_passes(self, monkeypatch, repo):
        ok, _ = _validate(monkeypatch, repo, _workload())
        assert ok

    def test_helm_is_given_the_helmrelease_name_and_namespace(self, monkeypatch, repo):
        _, cmd = _validate(monkeypatch, repo, _workload())
        assert cmd[:3] == ["helm", "template", "demo"]
        assert cmd[cmd.index("--namespace") + 1] == "demo-ns"
        assert cmd[cmd.index("--version") + 1] == "1.2.3"

    def test_targetnamespace_wins_over_the_metadata_namespace(self, monkeypatch, repo):
        manifest = MANIFEST.replace(
            "spec:\n  chart:", "spec:\n  targetNamespace: elsewhere\n  chart:")
        _, cmd = _validate(monkeypatch, repo, _workload(), manifest=manifest)
        assert cmd[cmd.index("--namespace") + 1] == "elsewhere"

    def test_a_failed_render_fails(self, monkeypatch, repo):
        ok, _ = _validate(monkeypatch, repo, "", helm_rc=1)
        assert not ok


class TestCpuLimitArm:
    """check-hpa-vpa-invariant.py sees only `.spec.values`, so a chart default
    reaches the cluster unless this arm catches it."""

    def test_a_chart_rendered_cpu_limit_fails(self, monkeypatch, repo):
        ok, _ = _validate(monkeypatch, repo, _workload("500m"))
        assert not ok

    def test_an_allowlisted_workload_passes(self, monkeypatch, repo):
        ok, _ = _validate(monkeypatch, repo, _workload("500m"),
                          allowlist={"demo-ns/Deployment/demo"})
        assert ok

    def test_the_allowlist_is_keyed_by_namespace_kind_name(self, monkeypatch, repo):
        """A bare name would waive the same workload in every namespace."""
        ok, _ = _validate(monkeypatch, repo, _workload("500m"), allowlist={"demo"})
        assert not ok


class TestSubstitutionArm:
    def test_an_unknown_configmap_key_fails_before_helm_runs(self, monkeypatch, repo):
        manifest = MANIFEST.replace("${demo_version}", "${not_a_key}")
        ok, cmd = _validate(monkeypatch, repo, _workload(), manifest=manifest)
        assert not ok
        assert cmd == []

    def test_a_quoted_placeholder_keeps_its_string_type(self):
        text, missing = vhv.substitute('version: "${demo_version}"\n', VERSIONS)
        assert not missing
        assert yaml.safe_load(text)["version"] == "1.2.3"


def test_the_live_release_list_is_what_flux_lint_renders():
    """The arms above are only worth pinning while the real estate uses them."""
    releases = vhv.load_releases(str(SCRIPTS / vhv.DEFAULT_RELEASES_FILE))
    assert releases
    for rel in releases:
        assert (SCRIPTS.parent / rel["manifest"]).is_file(), rel["manifest"]
