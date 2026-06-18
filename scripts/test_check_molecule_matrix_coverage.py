#!/usr/bin/env python3
"""
Unit tests for check-molecule-matrix-coverage.sh.

The script fails when a molecule scenario dir (ansible/roles/*/molecule/*/) or
an integration-test dir (ansible/integration-tests/*/) exists with no matching
entry in the molecule-tests / integration-tests parallel:matrix in
.gitlab-ci.yml. These tests drive it via subprocess inside a throwaway repo
layout, covering:

  - the real repo is in sync (smoke test against the actual tree)
  - a molecule scenario dir missing from the matrix fails + names it
  - an integration-test dir missing from the matrix fails + names it
  - a matrix entry with no on-disk scenario does NOT fail (one-way check)

The script resolves the repo root from its own location, so each fixture test
runs against a copy of the script placed inside the fixture tree.

Run with pytest:
    pytest scripts/test_check_molecule_matrix_coverage.py -v
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-molecule-matrix-coverage.sh"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Minimal matrix: one molecule scenario (alpha/default) and one integration
# test (stack-a). A fixture that adds an on-disk scenario beyond these must
# fail; one that matches must pass.
FIXTURE_CI = textwrap.dedent(
    """\
    molecule-tests:
      stage: test
      parallel:
        matrix:
          - ROLE: alpha
            SCENARIO: default

    integration-tests:
      stage: test
      parallel:
        matrix:
          - TEST:
              - stack-a
    """
)

MOLECULE_YML = "driver:\n  name: default\n"


def _scenario(repo: Path, role: str, scenario: str):
    d = repo / "ansible" / "roles" / role / "molecule" / scenario
    d.mkdir(parents=True, exist_ok=True)
    (d / "molecule.yml").write_text(MOLECULE_YML)


def _integration(repo: Path, name: str, scenario: str = "default"):
    d = repo / "ansible" / "integration-tests" / name / "molecule" / scenario
    d.mkdir(parents=True, exist_ok=True)
    (d / "molecule.yml").write_text(MOLECULE_YML)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts" / "check-molecule-matrix-coverage.sh")
    (r / ".gitlab-ci.yml").write_text(FIXTURE_CI)
    # Baseline in-sync tree.
    _scenario(r, "alpha", "default")
    _integration(r, "stack-a")
    return r


def _run(repo: Path):
    return subprocess.run(
        ["bash", "scripts/check-molecule-matrix-coverage.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_real_repo_is_in_sync():
    """Smoke test: the live .gitlab-ci.yml matrix matches the real scenario
    dirs. If this fails, a scenario was added without a matrix entry (or vice
    versa) — exactly what the gate is for."""
    res = subprocess.run(
        ["bash", "scripts/check-molecule-matrix-coverage.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_in_sync_fixture_passes(repo: Path):
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_unlisted_molecule_scenario_fails(repo: Path):
    _scenario(repo, "beta", "default")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/roles/beta/molecule/default/" in res.stderr


def test_unlisted_scenario_of_listed_role_fails(repo: Path):
    """A second scenario of an already-listed role still needs its own entry."""
    _scenario(repo, "alpha", "extra")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/roles/alpha/molecule/extra/" in res.stderr


def test_unlisted_integration_test_fails(repo: Path):
    _integration(repo, "stack-b")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/integration-tests/stack-b/" in res.stderr


def test_matrix_entry_without_disk_scenario_does_not_fail(repo: Path):
    """One-way check: a matrix entry pointing at a non-existent scenario is the
    runtime-caught case (molecule errors), so this script must NOT fail on it."""
    ci = (repo / ".gitlab-ci.yml").read_text().replace(
        "          - ROLE: alpha\n            SCENARIO: default\n",
        "          - ROLE: alpha\n            SCENARIO: default\n"
        "          - ROLE: ghost\n            SCENARIO: default\n",
    )
    (repo / ".gitlab-ci.yml").write_text(ci)
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_scenario_dir_without_molecule_yml_ignored(repo: Path):
    """A molecule/<dir> without a molecule.yml isn't a runnable scenario and
    must not trigger a failure (e.g. a stray shared dir)."""
    stray = repo / "ansible/roles/alpha/molecule/shared"
    stray.mkdir(parents=True)
    (stray / "README.md").write_text("not a scenario\n")
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
