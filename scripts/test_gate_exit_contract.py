"""Every local gate exits 2, not 1, when it cannot inspect its subject.

Exit 1 means a finding, so collapsing "I could not look" into it reads a broken
environment as a policy violation. Each gate is driven against an empty tree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent

# Gates that take --repo-root: pointed at an empty directory, every input they
# read is absent, which is the operator error the contract names.
ROOT_ARG_GATES = (
    "check-cluster-literals.py",
    "check-grafana-sidecar-init.py",
    "check-guest-endpoint-parity.py",
    "check-image-gc-threshold.py",
    "check-tailnet-dns-parity.py",
    "check-tenant-wiring.py",
    "check-unifi-doc-parity.py",
    "check-upstream-rule-mirror.py",
)

# Corpus gates: they read the rendered manifests on stdin, so an empty stdin is
# the subject they did not inspect.
STDIN_GATES = (
    "check-dockerconfigjson.py",
    "check-unmanaged-secrets.py",
)


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        check=False,
        input=stdin,
        cwd=SCRIPTS.parent,
    )


@pytest.mark.parametrize("gate", ROOT_ARG_GATES)
def test_an_empty_tree_is_exit_2_not_a_finding(gate, tmp_path):
    path = SCRIPTS / gate
    assert path.is_file(), f"{gate} is missing — update this list"
    result = _run([str(path), "--repo-root", str(tmp_path)])
    assert result.returncode == 2, (
        f"{gate} exited {result.returncode} over an empty tree; 1 would read as a "
        f"finding:\n{result.stdout}{result.stderr}"
    )


@pytest.mark.parametrize("gate", STDIN_GATES)
def test_an_empty_corpus_is_exit_2_not_a_finding(gate):
    path = SCRIPTS / gate
    assert path.is_file(), f"{gate} is missing — update this list"
    result = _run([str(path)], stdin="")
    assert result.returncode == 2, (
        f"{gate} exited {result.returncode} over an empty corpus; 1 would read as "
        f"a finding:\n{result.stdout}{result.stderr}"
    )


def test_a_clean_gate_still_exits_0():
    """The floor: a gate run over the real tree returns 0, so the cases above
    are reading the could-not-inspect arm and not a gate that always exits 2."""
    result = _run([str(SCRIPTS / "check-cluster-literals.py")])
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
