"""Every script shipped here is exercised by some suite, or exempt with a reason.

weisssrv-lib gates this for its own scripts; the half that stays here had no
equivalent, so a local gate could ship — and be wired into CI — with no test at
all. A gate with a bug does not fail loudly: it PASSES, and the regression it
was written to catch ships behind a green pipeline.

The library's version is deliberately NOT vendored: it resolves suites as
`tests/test_<stem>.py` and requires a `docs/SCRIPTS.md` mention, neither of
which matches this repo's layout (suites live beside the scripts, and the
inventory doc is `scripts/README.md`). This is the same gate ported to those
two facts, with one further difference: a script's coverage may live in a suite
of ANY name, so what is asserted is that some `test_*.py` NAMES it — the
wrapper/library pairs (`collect-state.sh` -> `test_collect_state_lib.py`) are
covered that way by design.

Scripts vendored or forked from the library are skipped by READING the library's
registry, never a hand-copied list: their exhaustive suites live upstream next
to the code, and re-listing them here would drift the moment the registry does.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_vendored_byte_identity import registered_consumer_paths

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
README = SCRIPTS / "README.md"

_SCRIPT_SUFFIXES = {".py", ".sh"}

# Operator-only scripts run by a human at a terminal, each with the reason no
# suite is worth writing. Every entry is a claim that the script has no
# decision logic a unit test could pin — it is a sequence of remote commands
# whose failure is immediately visible to the operator running it.
EXEMPT = {
    "bootstrap-proxmox-host.sh": (
        "one-shot host preparation run by hand before a host joins the "
        "inventory; every step is an apt/ssh command whose failure is visible "
        "at the terminal, and there is no decision logic to pin"
    ),
    "diagnose-network-issues.sh": (
        "interactive cross-host diagnostic — it only prints, changes nothing, "
        "and its output is read by a human, so an assertion would restate the "
        "commands"
    ),
    "vpn-credcheck.sh": (
        "prints which vpn-credentials keys the configured provider lacks; the "
        "provider key map it reads is asserted by the download-clients "
        "manifests, not by this wrapper"
    ),
    "maintenance-all-ops.sh": (
        "ordering wrapper: runs the maintenance ops in the documented order and "
        "aborts at the first failure. The op logic is maintenance-lib.sh, which "
        "test_maintenance_lib.py covers"
    ),
    "maintenance-run-with-verify.sh": (
        "runs one command then post-maintenance-verify.sh; both halves are "
        "covered by their own suites"
    ),
    "maintenance-ha-restart.sh": (
        "restarts the HA-managed Home Assistant VM and waits — pure ssh/pvesh "
        "sequencing against live Proxmox, unreachable from a unit test"
    ),
    "maintenance-rearm-self-reboot.sh": (
        "re-arms a detached self-reboot from a job's after_script; the "
        "behaviour under test is systemd-run's, on a live host"
    ),
}


def _scripts() -> list[Path]:
    return sorted(
        p
        for p in SCRIPTS.iterdir()
        if p.is_file()
        and p.suffix in _SCRIPT_SUFFIXES
        and not p.name.startswith("test_")
    )


@pytest.fixture(scope="module")
def upstream_covered() -> set[str]:
    """Filenames the library registry accounts for (vendored or forked)."""
    names = {Path(p).name for p in registered_consumer_paths()}
    assert names, (
        "the weisssrv-lib registry resolved no entries — every vendored script "
        "would read as untested here. See test_vendored_byte_identity.py for the "
        "checkout requirement."
    )
    return names


@pytest.fixture(scope="module")
def suite_bodies() -> dict[str, str]:
    return {p.name: p.read_text() for p in SCRIPTS.glob("test_*.py")}


def test_every_local_script_is_named_by_a_suite(upstream_covered, suite_bodies):
    uncovered = []
    for script in _scripts():
        if script.name in upstream_covered or script.name in EXEMPT:
            continue
        if not any(script.name in body for body in suite_bodies.values()):
            uncovered.append(script.name)
    assert not uncovered, (
        "local scripts named by no test suite: "
        + ", ".join(sorted(uncovered))
        + "\n\nAdd a scripts/test_*.py that exercises it (naming the file), or "
        "add it to EXEMPT here with the reason a suite is not worth writing."
    )


def test_every_exemption_still_names_a_script(upstream_covered):
    present = {p.name for p in _scripts()}
    stale = sorted(set(EXEMPT) - present)
    assert not stale, f"EXEMPT names scripts that no longer exist: {stale}"
    upstream = sorted(set(EXEMPT) & upstream_covered)
    assert not upstream, (
        f"these are exempt here but vendored from the library: {upstream} — the "
        "registry already accounts for them, drop the EXEMPT entries"
    )


def test_every_exemption_carries_a_reason():
    for name, reason in EXEMPT.items():
        assert len(reason.split()) >= 10, f"{name} needs a real reason, not {reason!r}"


def test_every_script_appears_in_the_scripts_readme():
    """scripts/README.md is the inventory an agent reads before touching this
    directory; a script missing from it is invisible to that reader."""
    body = README.read_text()
    missing = sorted(p.name for p in _scripts() if p.name not in body)
    assert not missing, (
        f"scripts absent from scripts/README.md: {missing} — add a row in the "
        "table for its category, with its Origin"
    )


def test_the_gate_sees_a_realistic_number_of_scripts():
    """A collection rule that stopped matching would exempt everything."""
    assert len(_scripts()) > 20, "the script walk resolved almost nothing"
