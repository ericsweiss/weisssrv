"""Coverage for flux-env.sh — the two-ConfigMap entry point.

`task flux:lint` and the library's flux-lint CI job both call a single
`<script> export-versions <configmap>`, while this cluster substitutes from
cluster-versions AND cluster-config. Everything below is about that seam: the
union, the precedence, and the de-duplication that keeps a file named on both
sides from being read twice.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "flux-env.sh"


def configmap(path: Path, name: str, data: dict) -> Path:
    path.write_text(
        yaml.safe_dump({"apiVersion": "v1", "kind": "ConfigMap",
                        "metadata": {"name": name}, "data": data})
    )
    return path


def run(subcommand: str, *args: str, extra: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    if extra is not None:
        env["FLUX_EXTRA_CONFIGMAPS"] = extra
    return subprocess.run(
        ["bash", str(SCRIPT), subcommand, *args],
        capture_output=True, text=True, cwd=REPO, env=env,
    )


@pytest.fixture
def cms(tmp_path: Path) -> tuple[Path, Path]:
    versions = configmap(tmp_path / "versions.yaml", "cluster-versions",
                         {"k3s_version": "v1.34.1+k3s1", "shared_key": "from-versions"})
    config = configmap(tmp_path / "config.yaml", "cluster-config",
                       {"cluster_internal_domain": "example.lan", "shared_key": "from-config"})
    return versions, config


def test_export_unions_both_configmaps(cms) -> None:
    versions, config = cms
    result = run("export-versions", str(versions), extra=str(config))
    assert result.returncode == 0, result.stderr
    assert "export k3s_version=" in result.stdout
    assert "export cluster_internal_domain=example.lan" in result.stdout
    allowlist = [line for line in result.stdout.splitlines()
                 if line.startswith("export FLUX_ENVSUBST_VARS=")]
    assert len(allowlist) == 1, "exactly one merged allowlist is emitted"
    assert "${k3s_version}" in allowlist[0] and "${cluster_internal_domain}" in allowlist[0]


def test_later_file_wins_on_a_key_collision(cms) -> None:
    versions, config = cms
    result = run("export-versions", str(versions), extra=str(config))
    exports = [line for line in result.stdout.splitlines() if line.startswith("export shared_key=")]
    assert exports[-1] == "export shared_key=from-config"


def test_a_file_named_twice_is_read_once(cms) -> None:
    versions, _ = cms
    result = run("export-versions", str(versions), extra=str(versions))
    assert result.stdout.count("export k3s_version=") == 1


def test_no_extras_is_a_single_configmap(cms) -> None:
    versions, _ = cms
    result = run("export-versions", str(versions), extra="")
    assert result.returncode == 0, result.stderr
    assert "cluster_internal_domain" not in result.stdout


def test_merged_configmap_is_one_document_with_both_key_sets(cms) -> None:
    versions, config = cms
    result = run("merged-configmap", str(versions), extra=str(config))
    assert result.returncode == 0, result.stderr
    doc = yaml.safe_load(result.stdout)
    assert doc["kind"] == "ConfigMap"
    assert doc["data"]["cluster_internal_domain"] == "example.lan"
    assert doc["data"]["shared_key"] == "from-config"


def test_k8s_version_reads_only_the_first_configmap(cms) -> None:
    versions, config = cms
    result = run("k8s-version", str(versions), extra=str(config))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1.34.0"


def test_missing_configmap_fails_loudly(tmp_path: Path) -> None:
    result = run("export-versions", str(tmp_path / "absent.yaml"), extra="")
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_unknown_subcommand_fails(cms) -> None:
    result = run("render-everything", extra="")
    assert result.returncode != 0
    assert "unknown subcommand" in result.stderr


def test_repo_defaults_resolve_the_real_configmaps() -> None:
    """The default FLUX_EXTRA_CONFIGMAPS must name this repo's cluster-config."""
    result = subprocess.run(
        ["bash", str(SCRIPT), "export-versions",
         "kubernetes/infrastructure/sources/versions-configmap.yaml"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "export cluster_internal_domain=" in result.stdout
