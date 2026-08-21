"""Failure-path tests for scripts/check-nfs-tls.py.

The two failures it exists to catch look identical in git — a PV that mounts —
and only show up as a mount error on a scheduled pod, so what needs proving is
that the gate FAILS on each, and that it refuses to pass vacuously.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-nfs-tls.py"
REPO = SCRIPT.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("check_nfs_tls", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load()


def _run(corpus: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=textwrap.dedent(corpus), capture_output=True, text=True, cwd=REPO,
    )


GOOD = """\
    apiVersion: v1
    kind: PersistentVolume
    metadata:
      name: appdata-authentik
    spec:
      mountOptions:
        - nfsvers=4.2
        - hard
        - xprtsec=tls
      nfs:
        server: pve-nas-01.esweiss.com
        path: /appdata/authentik
    """


def test_a_compliant_pv_passes():
    result = _run(GOOD)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 NFS PersistentVolume" in result.stdout


def test_a_plaintext_pv_fails():
    result = _run(GOOD.replace("        - xprtsec=tls\n", ""))
    assert result.returncode == 1
    assert "xprtsec=tls" in result.stderr


def test_an_ip_server_fails():
    result = _run(GOOD.replace("pve-nas-01.esweiss.com", "10.0.10.102"))
    assert result.returncode == 1
    assert "no IP SAN" in result.stderr


def test_a_non_nfs_pv_is_ignored(gate):
    docs = [{
        "kind": "PersistentVolume",
        "metadata": {"name": "zvol-loki"},
        "spec": {"local": {"path": "/mnt/zvol"}},
    }]
    assert gate.nfs_violations(docs) == ([], 0)


def test_an_empty_server_fails():
    # ip_address("") raises ValueError, so an empty server falls out of the IP
    # arm; without the explicit elif it would read as a compliant hostname.
    result = _run(GOOD.replace("pve-nas-01.esweiss.com", '""'))
    assert result.returncode == 1
    assert "spec.nfs.server is empty" in result.stderr


def test_an_empty_corpus_is_an_error_not_a_pass():
    assert _run("").returncode == 2


def test_unparseable_yaml_is_an_error_not_a_pass():
    # A corpus the render step truncated must not read as "nothing to check".
    # Exit 2, never 0 — a swallowed parse error retires the gate silently.
    result = _run("kind: PersistentVolume\nspec: [unclosed\n")
    assert result.returncode == 2
    assert "could not parse the corpus" in result.stderr


def test_a_corpus_with_no_nfs_pv_is_an_error_not_a_pass():
    result = _run("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n")
    assert result.returncode == 2
    assert "inspected 0 NFS PersistentVolumes" in result.stderr


def test_the_repo_manifests_are_clean():
    """The gate over this repo's real PVs, not a fixture.

    `task flux:lint` runs it over the rendered corpus (and the CI job's
    extra_validation input is byte-compared against that block, so both sides
    move in one commit), but that needs kustomize + envsubst. This runs in the
    python-tests job, which triggers on kubernetes/apps + infrastructure, so a
    PV edit meets the gate even when the flux-lint job is not in the pipeline.

    Reads the PV manifests straight from git rather than a rendered corpus:
    `spec.nfs.server` and the mountOptions are literals there — check-cluster-
    literals.py exempts per-guest addresses — so nothing needs substituting.
    """
    import yaml

    corpus = []
    for path in sorted((REPO / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if "kind: PersistentVolume" not in text:
            continue
        try:
            docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        except yaml.YAMLError:
            continue
        corpus += [d for d in docs if d.get("kind") == "PersistentVolume"]
    assert corpus, "no PersistentVolume parsed straight from kubernetes/"
    result = _run(yaml.safe_dump_all(corpus))
    assert result.returncode == 0, result.stdout + result.stderr
