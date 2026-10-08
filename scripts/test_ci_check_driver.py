"""Prove both consolidated gate jobs' driver reports every check.

The driver is inline in .gitlab-ci.yml, so the text is extracted and run under
the same errexit preamble gitlab-runner's bash executor emits.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CI_FILE = REPO / ".gitlab-ci.yml"

DRIVER = re.compile(
    r"^( +)set -euo pipefail\n\s+overall=0; failed=\"\"\n(?P<body>.*?^\1\}\n)",
    re.DOTALL | re.MULTILINE,
)


def drivers() -> list[str]:
    """Every inline run_check driver, dedented and ready to source."""
    found = []
    for match in DRIVER.finditer(CI_FILE.read_text(encoding="utf-8")):
        found.append(textwrap.dedent('set -euo pipefail\noverall=0; failed=""\n' + match.group("body")))
    return found


def run(driver: str) -> subprocess.CompletedProcess[str]:
    script = (
        # gitlab-runner's own preamble, which the driver must neutralise.
        "set -eo pipefail\n"
        + driver
        + textwrap.dedent(
            """
            one() { return 3; }
            two() { echo "check two ran"; }
            run_check one one
            run_check two two
            [ "$overall" -eq 0 ] || echo "Failed checks:$failed"
            exit "$overall"
            """
        )
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=REPO, check=False
    )


def test_both_gate_jobs_carry_the_driver():
    assert len(drivers()) == 2, "expected check-generated-files and check-repo-policies"


@pytest.mark.parametrize("index", [0, 1])
def test_a_failing_check_does_not_abort_the_job(index: int):
    proc = run(drivers()[index])
    out = proc.stdout + proc.stderr
    assert "FAILED: one (rc=3)" in out, out
    assert "check two ran" in out, out
    assert "Failed checks: one" in out, out
    assert proc.returncode == 1, out


def test_the_shape_without_set_plus_e_would_abort():
    """Mutation case: the errexit-clearing line is what makes the driver work."""
    mutated = drivers()[0].replace("        set +e\n", "").replace("set +e\n", "")
    proc = run(mutated)
    out = proc.stdout + proc.stderr
    assert "check two ran" not in out, out


def test_a_clean_run_exits_zero():
    driver = drivers()[0]
    script = (
        "set -eo pipefail\n"
        + driver
        + textwrap.dedent(
            """
            ok() { echo "ran"; }
            run_check ok ok
            exit "$overall"
            """
        )
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=REPO, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
