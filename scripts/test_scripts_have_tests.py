"""Every script shipped here is exercised by some suite, or exempt with a reason.

The walk covers scripts/, kubernetes/ and terraform/. Coverage means a `test_*.py`
uses the script, not that prose mentions it; vendored ones come from the manifest.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from test_vendored_byte_identity import registered_consumer_paths

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
README = SCRIPTS / "README.md"
# CronJob programs live beside their manifests and terraform helpers beside
# their roots, so a script outside scripts/ would otherwise be invisible here.
SCRIPT_ROOTS = (SCRIPTS, REPO / "kubernetes", REPO / "terraform")
# Provider plugins and modules a `terraform init` downloaded: not shipped code.
_VENDOR_PARTS = {"__pycache__", ".terraform"}

_SCRIPT_SUFFIXES = {".py", ".sh"}
# Site data and fixtures that sit in scripts/ alongside the code. They are
# covered by test_site_configs.py / the promtool suites, not by a script test,
# and some carry the executable bit from a checkout's umask.
_NON_SCRIPT_SUFFIXES = {".yml", ".yaml", ".json", ".conf", ".env", ".toml", ".md"}

# Operator-only scripts run by a human at a terminal, each with the reason no
# suite is worth writing. Keyed by the path from the repo root, so a waiver
# cannot travel to a future same-named script under another root.
EXEMPT = {
    "scripts/bootstrap-proxmox-host.sh": (
        "one-shot host preparation run by hand before a host joins the "
        "inventory; its branching (repo disable/restore, apt rc classification, "
        "the visudo gate) runs inside an ssh heredoc on the target, so pinning "
        "it would mean shipping a second file to an unbootstrapped host, and "
        "each branch prints its own failure at the operator's terminal"
    ),
    "scripts/diagnose-network-issues.sh": (
        "interactive cross-host diagnostic — it only prints, changes nothing, "
        "and its output is read by a human, so an assertion would restate the "
        "commands"
    ),
    "scripts/maintenance-all-ops.sh": (
        "ordering wrapper: runs the maintenance ops in the documented order and "
        "aborts at the first failure. The op logic is maintenance-lib.sh, which "
        "test_maintenance_lib.py covers"
    ),
    "scripts/maintenance-ha-restart.sh": (
        "restarts the HA-managed Home Assistant VM and waits — pure ssh/pvesh "
        "sequencing against live Proxmox, unreachable from a unit test"
    ),
    "scripts/maintenance-rearm-self-reboot.sh": (
        "re-arms a detached self-reboot from a job's after_script; the "
        "behaviour under test is systemd-run's, on a live host"
    ),
}


def _label(script: Path) -> str:
    """The repo-relative path an EXEMPT key is spelled as.

    A path outside the repo returns its absolute form, which matches no key, so
    an off-tree walk cannot inherit a waiver.
    """
    try:
        return script.relative_to(REPO).as_posix()
    except ValueError:
        return script.as_posix()


def _scripts(root: Path = SCRIPTS) -> list[Path]:
    """Every script shipped under scripts/, recursively.

    Collected by executable bit as well as suffix: sourced copies carry mode
    644 and shebang scripts often carry none. The pytest harness is not a script.
    """
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not _VENDOR_PARTS & set(p.parts)
        and p.name != "conftest.py"
        and not p.name.startswith("test_")
        and p.suffix not in _NON_SCRIPT_SUFFIXES
        and (p.suffix in _SCRIPT_SUFFIXES or os.access(p, os.X_OK))
    )


def _all_scripts() -> list[Path]:
    """Every script under the roots this gate claims to cover."""
    return sorted(p for root in SCRIPT_ROOTS if root.is_dir() for p in _scripts(root))


@pytest.fixture(scope="module")
def upstream_covered() -> set[str]:
    """Filenames scripts/vendored-manifest.yml accounts for (vendored or forked)."""
    names = {Path(p).name for p in registered_consumer_paths()}
    assert names, (
        "scripts/vendored-manifest.yml resolved no entries — every vendored script "
        "would read as untested here. See test_vendored_byte_identity.py for the "
        "checkout requirement."
    )
    return names


def _code_strings(source: str) -> list[str]:
    """Every string literal in `source` that is not a docstring.

    Prose mentions therefore do not read as coverage; an argv literal does.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.fixture(scope="module")
def suite_bodies() -> dict[str, list[str]]:
    # This file is excluded from its own inputs: every EXEMPT reason below is a
    # string literal, so a script would cover itself by being exempted.
    return {
        p.name: _code_strings(p.read_text())
        for p in SCRIPTS.glob("test_*.py")
        if p.name != Path(__file__).name
    }


def test_every_local_script_is_named_by_a_suite(upstream_covered, suite_bodies):
    uncovered = []
    for script in _all_scripts():
        if script.name in upstream_covered or _label(script) in EXEMPT:
            continue
        used = any(script.name in s for strings in suite_bodies.values() for s in strings)
        if not used:
            uncovered.append(_label(script))
    assert not uncovered, (
        "local scripts no test suite uses: "
        + ", ".join(sorted(uncovered))
        + "\n\nAdd a scripts/test_*.py that exercises it (naming the file in code, "
        "not only in a comment), or add it to EXEMPT here with the reason a suite "
        "is not worth writing."
    )


def test_a_comment_only_mention_does_not_count_as_coverage():
    """Mutation proof for the rule above: prose must not satisfy the gate."""
    prose = '"""deploy-verify.sh is great."""\n# and so is collect-state.sh\n'
    assert not any("deploy-verify.sh" in s for s in _code_strings(prose))
    code = 'subprocess.run(["bash", "scripts/deploy-verify.sh"])\n'
    assert any("deploy-verify.sh" in s for s in _code_strings(code))


def conventional_suite(name: str) -> Path:
    """The suite path a script's own tests land at: `test_<stem>.py`."""
    return SCRIPTS / f"test_{Path(name).stem.replace('-', '_')}.py"


def test_every_exemption_still_names_a_script(upstream_covered):
    present = {_label(p) for p in _all_scripts()}
    stale = sorted(set(EXEMPT) - present)
    assert not stale, f"EXEMPT names scripts that no longer exist: {stale}"
    upstream = sorted(key for key in EXEMPT if Path(key).name in upstream_covered)
    assert not upstream, (
        f"these are exempt here but vendored from the library: {upstream} — the "
        "manifest already accounts for them, drop the EXEMPT entries"
    )


def test_no_exemption_outlived_its_reason():
    """An exemption claiming no suite is worth writing, while the suite exists,
    hides the file from the coverage rule the suite already satisfies."""
    covered = sorted(
        name for name in EXEMPT if conventional_suite(name).is_file()
    )
    assert not covered, (
        f"these are exempt here but now have a suite: {covered} — delete the "
        "EXEMPT entries so the coverage rule reads them again"
    )


def test_the_outlived_exemption_check_can_fail(tmp_path, monkeypatch):
    """Mutation proof: the arm above must see a suite that exists."""
    monkeypatch.setattr("test_scripts_have_tests.SCRIPTS", tmp_path)
    (tmp_path / "test_maintenance_all_ops.py").write_text("")
    assert conventional_suite("maintenance-all-ops.sh").is_file()
    assert not conventional_suite("diagnose-network-issues.sh").is_file()


def test_every_exemption_carries_a_reason():
    for name, reason in EXEMPT.items():
        assert len(reason.split()) >= 10, f"{name} needs a real reason, not {reason!r}"


def test_every_script_appears_in_the_scripts_readme():
    """scripts/README.md is the inventory an agent reads before touching this
    directory; a script missing from it is invisible to that reader. Scoped to
    scripts/: the other roots are inventoried by their own app directories."""
    body = README.read_text()
    missing = sorted(p.name for p in _scripts() if p.name not in body)
    assert not missing, (
        f"scripts absent from scripts/README.md: {missing} — add a row in the "
        "table for its category, with its Origin"
    )


def test_the_gate_sees_a_realistic_number_of_scripts():
    """A collection rule that stopped matching would exempt everything, and a
    root that stops resolving would quietly narrow the claim."""
    assert len(_scripts()) > 20, "the script walk resolved almost nothing"
    assert len(_all_scripts()) > len(_scripts()), (
        "the kubernetes/ and terraform/ roots contributed nothing — a shipped "
        "in-cluster program or terraform helper would go uncovered"
    )


def test_the_collector_sees_the_shapes_an_extension_filter_missed(tmp_path):
    """The collector is recursive and not extension-keyed: extensionless
    executables and subdirectory helpers must be seen, and the pytest harness
    must not."""
    (tmp_path / "gate.py").write_text("#!/usr/bin/env python3\n")
    (tmp_path / "test_gate.py").write_text("")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "config.yaml").write_text("---\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "gate.pyc").write_text("")
    shebang = tmp_path / "reap"
    shebang.write_text("#!/usr/bin/env bash\n")
    shebang.chmod(0o755)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "helper.sh").write_text("")

    assert [p.name for p in _scripts(tmp_path)] == ["gate.py", "helper.sh", "reap"]


def test_the_collector_walks_the_roots_outside_scripts(monkeypatch, tmp_path):
    """A CronJob program or terraform helper must be collected, while a
    `terraform init` provider tree must not."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gate.py").write_text("#!/usr/bin/env python3\n")
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "cronjob.yaml").write_text("---\n")
    (app / "demo-reaper.py").write_text("#!/usr/bin/env python3\n")
    root = tmp_path / "terraform" / "demo"
    (root / ".terraform" / "providers").mkdir(parents=True)
    (root / ".terraform" / "providers" / "vendored.sh").write_text("")
    (root / "import.sh").write_text("")

    monkeypatch.setattr(
        "test_scripts_have_tests.SCRIPT_ROOTS",
        (tmp_path / "scripts", tmp_path / "kubernetes", tmp_path / "terraform"),
    )
    assert [p.name for p in _all_scripts()] == [
        "demo-reaper.py",
        "gate.py",
        "import.sh",
    ]


def test_every_script_outside_scripts_is_held_to_the_coverage_rule(suite_bodies):
    """The live tree ships programs outside scripts/; each must be named by a
    suite, which is the claim the extra roots exist to make."""
    outside = [p for p in _all_scripts() if SCRIPTS not in p.parents]
    assert outside, "no shipped script outside scripts/ resolved — the roots moved"
    for script in outside:
        assert any(
            script.name in s for strings in suite_bodies.values() for s in strings
        ), f"{script.relative_to(REPO)} is named by no suite"


def test_exemptions_are_keyed_by_repo_relative_path():
    """A bare basename would waive every same-named script under every root."""
    for key in EXEMPT:
        assert "/" in key, f"{key} must be keyed by its path from the repo root"
        assert (REPO / key).is_file(), f"{key} does not resolve from the repo root"


def test_a_same_named_script_under_another_root_is_not_exempt(monkeypatch, tmp_path):
    """Mutation proof: the waiver must not travel with the basename."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "reaper.py").write_text("#!/usr/bin/env python3\n")
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "reaper.py").write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr("test_scripts_have_tests.REPO", tmp_path)
    monkeypatch.setattr(
        "test_scripts_have_tests.SCRIPT_ROOTS",
        (tmp_path / "scripts", tmp_path / "kubernetes"),
    )
    monkeypatch.setattr(
        "test_scripts_have_tests.EXEMPT",
        {"scripts/reaper.py": "operator-only, kept here to pin the keying rule only"},
    )
    unwaived = [_label(s) for s in _all_scripts() if _label(s) not in EXEMPT]
    assert unwaived == ["kubernetes/apps/demo/reaper.py"]


def test_no_two_scripts_claim_the_same_suite():
    """Two scripts sharing a basename collapse onto one suite and one upstream
    waiver, so a single suite satisfies the coverage rule for both."""
    claims: dict[str, list[str]] = {}
    for script in _all_scripts():
        claims.setdefault(conventional_suite(script.name).name, []).append(_label(script))
    collisions = {
        suite: sorted(scripts)
        for suite, scripts in sorted(claims.items())
        if len(scripts) > 1
    }
    assert not collisions, (
        "scripts sharing one conventional suite: "
        + "; ".join(f"{suite} <- {', '.join(s)}" for suite, s in collisions.items())
        + "\n\nRename one, or give each its own suite and key the coverage rule "
        "on the repo-relative path rather than the basename."
    )
