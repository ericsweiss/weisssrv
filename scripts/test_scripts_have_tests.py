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

import os
import re
from pathlib import Path

import pytest

from test_vendored_byte_identity import (
    registered_consumer_entries,
    registered_consumer_paths,
)

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
README = SCRIPTS / "README.md"

_SCRIPT_SUFFIXES = {".py", ".sh"}
# Site data and fixtures that sit in scripts/ alongside the code. They are
# covered by test_site_configs.py / the promtool suites, not by a script test,
# and some carry the executable bit from a checkout's umask.
_NON_SCRIPT_SUFFIXES = {".yml", ".yaml", ".json", ".conf", ".env", ".toml", ".md"}

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
        "prints which vpn-credentials keys the configured provider lacks. Its "
        "provider key map is restated in the sidecar manifest, the Taskfile "
        "alias map and docs/21, with no lint gate keeping the four in step — "
        "what covers the drift is `task downloads:vpn-provider`, which fails "
        "closed on this script's rc=2 the moment the alias map names a provider "
        "the script does not know"
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


def _scripts(root: Path = SCRIPTS) -> list[Path]:
    """Every script shipped under scripts/, recursively.

    Recursive and NOT extension-keyed, matching the library's collector: an
    executable with no suffix (the normal shape for a shebang script), a
    `.bash`/`.pl`, or a helper under `scripts/lib/` is exactly as shipped — and
    exactly as uncovered — as a `.py`. `.py`/`.sh` stay alongside the executable
    bit because the vendored copies are sourced or invoked through an
    interpreter and carry mode 644.
    """
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and not p.name.startswith("test_")
        and p.suffix not in _NON_SCRIPT_SUFFIXES
        and (p.suffix in _SCRIPT_SUFFIXES or os.access(p, os.X_OK))
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


def test_the_readme_origin_column_matches_the_library_registry():
    """The Origin column is the readable view of the registry, so it must not
    contradict it.

    Six rows said `local` — "the library ships nothing equivalent" — for scripts
    weisssrv-lib registers as vendored, inverting the maintenance rule for
    exactly the files where a local edit is reverted by the next re-vendor.
    Nothing noticed: the README gate above only asserts the filename appears
    somewhere in the body.
    """
    registered = {Path(path).name: kind for kind, path in registered_consumer_entries()}
    assert registered, (
        "the weisssrv-lib registry resolved no entries — every Origin cell would "
        "read as correct. See test_vendored_byte_identity.py for the checkout "
        "requirement."
    )
    row = re.compile(r"^\|\s*`([\w.\-/]+)`\s*\|.*\|\s*(local|vendored|forked)\b[^|]*\|\s*$")
    wrong = []
    for line in README.read_text().splitlines():
        match = row.match(line)
        if not match:
            continue
        name, origin = Path(match.group(1)).name, match.group(2)
        expected = registered.get(name, "local")
        if origin != expected:
            wrong.append(f"{name}: README says {origin}, registry says {expected}")
    assert not wrong, (
        "scripts/README.md Origin cells disagreeing with weisssrv-lib's "
        "vendored-paths.yml:\n  " + "\n  ".join(sorted(wrong))
    )


def test_the_gate_sees_a_realistic_number_of_scripts():
    """A collection rule that stopped matching would exempt everything."""
    assert len(_scripts()) > 20, "the script walk resolved almost nothing"


def test_the_collector_sees_the_shapes_an_extension_filter_missed(tmp_path):
    """The hole this gate used to have: non-recursive and extension-keyed, so an
    extensionless executable and anything in a subdirectory were invisible."""
    (tmp_path / "gate.py").write_text("#!/usr/bin/env python3\n")
    (tmp_path / "test_gate.py").write_text("")
    (tmp_path / "config.yaml").write_text("---\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "gate.pyc").write_text("")
    shebang = tmp_path / "reap"
    shebang.write_text("#!/usr/bin/env bash\n")
    shebang.chmod(0o755)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "helper.sh").write_text("")

    assert [p.name for p in _scripts(tmp_path)] == ["gate.py", "helper.sh", "reap"]
