"""Every `needs: optional: true` resolves to a job something creates.

GitLab ignores an optional need naming no job, so a moved `job_name` default
turns a dependency off silently. Drives the gate at the declared ref.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_vendored_byte_identity import _lib_root, _pinned_ref, _require_ref

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "scripts" / "check-ci-include-job-names.py"
CI_FILE = ".gitlab-ci.yml"
# GitLab ships Secret-Detection with the instance, so neither checkout holds it.
SECRET_DETECTION_ALLOWANCE = (
    "Jobs/Secret-Detection.gitlab-ci.yml=GitLab ships this template with the "
    "instance, so neither checkout holds it; the secret_detection job it "
    "creates is needed non-optionally"
)


@pytest.fixture(scope="module")
def lib_tree(tmp_path_factory) -> Path:
    """The library's `ci/` tree at the declared ref, extracted once."""
    lib = _lib_root()
    ref = _require_ref(lib, _pinned_ref())
    out = tmp_path_factory.mktemp("lib-ci-at-pin")
    archive = subprocess.run(
        ["git", "-C", str(lib), "archive", ref, "ci"], capture_output=True
    )
    assert archive.returncode == 0, (
        f"could not archive ci/ at {ref} from {lib}: {archive.stderr.decode()}"
    )
    extract = subprocess.run(
        ["tar", "-x", "-C", str(out)], input=archive.stdout, capture_output=True
    )
    assert extract.returncode == 0, f"could not extract ci/ at {ref}: {extract.stderr.decode()}"
    return out



def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True
    )


def test_every_optional_need_resolves_against_the_included_jobs(lib_tree):
    """The same argv `task lint:ci-include-job-names` runs."""
    run = _run(
        "--repo-root", str(REPO),
        "--ci-file", CI_FILE,
        "--lib-path", str(lib_tree),
        "--require-optional-needs",
        "--allow-unreadable-include", SECRET_DETECTION_ALLOWANCE,
    )
    assert run.returncode == 0, (
        f"{CI_FILE} declares an optional need no job creates at {_pinned_ref()}:\n"
        f"{run.stdout}{run.stderr}"
    )
    assert "optional need(s) resolve" in run.stdout, (
        f"the gate resolved no optional need at all:\n{run.stdout}{run.stderr}"
    )


def test_the_secret_detection_blind_spot_is_still_undeclarable_silently(lib_tree):
    """Without the allowance the gate stops: a need hidden inside an unreadable
    include would otherwise pass as resolved."""
    run = _run(
        "--repo-root", str(REPO),
        "--ci-file", CI_FILE,
        "--lib-path", str(lib_tree),
    )
    assert run.returncode == 2, (
        f"an include naming a file outside both checkouts must be operator error:\n"
        f"{run.stdout}{run.stderr}"
    )
    assert "Secret-Detection" in run.stdout + run.stderr


def test_an_unresolvable_optional_need_is_reported(tmp_path):
    """Mutation case: the live tree is clean, so the finding path is proven on a
    fixture."""
    (tmp_path / CI_FILE).write_text(
        "real-job:\n  script:\n    - true\n"
        "gate:\n  script:\n    - true\n"
        "  needs:\n    - job: real-job\n      optional: true\n"
        "    - job: renamed-job\n      optional: true\n"
    )
    run = _run("--repo-root", str(tmp_path), "--ci-file", CI_FILE)
    assert run.returncode == 1, f"{run.stdout}{run.stderr}"
    assert "renamed-job" in run.stdout + run.stderr
