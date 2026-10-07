"""Tests that check-nfs-tls.py fails on a plaintext or IP-mounted NFS PV, and never passes vacuously."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from script_loader import load_path

SCRIPT = Path(__file__).resolve().parent / "check-nfs-tls.py"
REPO = SCRIPT.parent.parent


def _load():
    return load_path(SCRIPT)


@pytest.fixture(scope="module")
def gate():
    return _load()


def _run(corpus: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
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


def test_a_comma_joined_mount_option_passes():
    """Kubernetes comma-joins mountOptions, so one element may carry several."""
    result = _run(GOOD.replace(
        "        - nfsvers=4.2\n        - hard\n        - xprtsec=tls\n",
        "        - nfsvers=4.2,hard,xprtsec=tls\n",
    ))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_comma_joined_element_without_tls_still_fails():
    result = _run(GOOD.replace(
        "        - nfsvers=4.2\n        - hard\n        - xprtsec=tls\n",
        "        - nfsvers=4.2,hard\n",
    ))
    assert result.returncode == 1
    assert "xprtsec=tls" in result.stderr


def test_a_plaintext_pv_fails():
    result = _run(GOOD.replace("        - xprtsec=tls\n", ""))
    assert result.returncode == 1
    assert "xprtsec=tls" in result.stderr


def test_an_ip_server_fails():
    result = _run(GOOD.replace("pve-nas-01.esweiss.com", "10.0.10.102"))
    assert result.returncode == 1
    assert "no IP SAN" in result.stderr


def test_allow_ip_server_passes_an_ip_this_cluster_rejects():
    """The seam exists for a cert with an IP SAN. Off, the same corpus fails, so
    this cluster keeps the hostname invariant."""
    corpus = GOOD.replace("pve-nas-01.esweiss.com", "10.0.10.102")
    allowed = _run(corpus, "--allow-ip-server")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "by hostname" not in allowed.stdout
    assert _run(corpus).returncode == 1


def test_allow_ip_server_still_fails_an_empty_server():
    result = _run(GOOD.replace("pve-nas-01.esweiss.com", '""'), "--allow-ip-server")
    assert result.returncode == 1
    assert "spec.nfs.server is empty" in result.stderr


def test_a_non_nfs_pv_is_ignored(gate):
    docs = [{
        "kind": "PersistentVolume",
        "metadata": {"name": "zvol-loki"},
        "spec": {"local": {"path": "/mnt/zvol"}},
    }]
    assert gate.nfs_violations(docs) == ([], 0)


def test_an_empty_server_fails():
    # ip_address("") raises ValueError, so an empty server needs the explicit
    # elif or it reads as a compliant hostname.
    result = _run(GOOD.replace("pve-nas-01.esweiss.com", '""'))
    assert result.returncode == 1
    assert "spec.nfs.server is empty" in result.stderr


def test_a_pv_inside_a_kind_list_is_still_inspected():
    """A `kind: List` wrapper must not hide the PVs inside it from the gate."""
    listed = textwrap.dedent("""\
        apiVersion: v1
        kind: List
        items:
          - apiVersion: v1
            kind: PersistentVolume
            metadata:
              name: appdata-plaintext
            spec:
              mountOptions:
                - nfsvers=4.2
              nfs:
                server: pve-nas-01.esweiss.com
                path: /appdata/plaintext
        """)
    result = _run(listed)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "appdata-plaintext" in result.stderr


def test_a_bare_list_document_is_flattened(gate):
    nested = {
        "kind": "PersistentVolume",
        "metadata": {"name": "appdata-ip"},
        "spec": {"mountOptions": ["xprtsec=tls"], "nfs": {"server": "10.0.10.102"}},
    }
    assert gate._flatten([nested]) == [nested]
    assert gate._flatten({"kind": "List", "items": [nested]}) == [nested]
    assert gate._flatten("not a document") == []


def test_an_empty_corpus_is_an_error_not_a_pass():
    assert _run("").returncode == 2


def test_unparseable_yaml_is_an_error_not_a_pass():
    # A truncated corpus must exit 2, never 0 - a swallowed parse error retires
    # the gate silently.
    result = _run("kind: PersistentVolume\nspec: [unclosed\n")
    assert result.returncode == 2
    assert "could not parse the corpus" in result.stderr


def test_a_corpus_with_no_nfs_pv_is_an_error_not_a_pass():
    result = _run("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n")
    assert result.returncode == 2
    assert "inspected 0 NFS PersistentVolumes" in result.stderr


def test_the_repo_manifests_are_clean():
    """Run the gate over this repo's real PVs, read straight from git.

    spec.nfs.server and the mountOptions are literals there by design, so
    nothing needs substituting.
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
