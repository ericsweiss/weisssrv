"""Unit tests for scripts/vpn-credcheck.sh.

A stub kubectl on PATH drives the three Secret-read outcomes, so an unreadable
cluster cannot be reported as a missing credential.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "vpn-credcheck.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def bin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    return d


def _run(bin_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=SCRIPTS.parent,
    )


def test_an_unreachable_cluster_refuses_rather_than_reporting_missing_keys(bin_dir):
    """Mutation case: swallowing the read error names the wrong remedy."""
    _stub(bin_dir, "kubectl", 'echo "Unable to connect to the server" >&2; exit 1')
    proc = _run(bin_dir, "qbittorrent", "privado")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""
    assert "could not read the vpn-credentials Secret" in proc.stderr


def test_an_absent_secret_reports_every_required_key(bin_dir):
    _stub(
        bin_dir,
        "kubectl",
        'echo \'secrets "vpn-credentials" not found\' >&2; exit 1',
    )
    proc = _run(bin_dir, "qbittorrent", "privado")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "privadovpn-user privadovpn-password"


def test_a_fully_wired_secret_reports_nothing_missing(bin_dir):
    _stub(
        bin_dir,
        "kubectl",
        'case "$*" in *"-o name"*) echo secret/vpn-credentials ;; *) echo dmFsdWU= ;; esac',
    )
    proc = _run(bin_dir, "qbittorrent", "privado")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""


def test_an_unknown_provider_still_refuses(bin_dir):
    _stub(bin_dir, "kubectl", "echo secret/vpn-credentials")
    proc = _run(bin_dir, "qbittorrent", "mullvad")
    assert proc.returncode == 2
    assert "unknown VPN provider" in proc.stderr
