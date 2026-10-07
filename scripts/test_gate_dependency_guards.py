"""A missing Python dependency is an operator error, not a policy finding.

scripts/flux-corpus-gates.sh and the CI `run_check` wrappers read rc 1 as a
finding and rc 2 as a gate that could not run, so the import guards must exit 2.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
MANIFEST = SCRIPTS / "vendored-manifest.yml"

# The exit-1 form: `sys.exit("...")` prints the message and exits 1, which the
# runners score as a NetworkPolicy-style violation of whatever the gate checks.
_EXIT_ONE_GUARD = re.compile(r'sys\.exit\(\s*"(?:ERROR: )?PyYAML required')

# A gate with no required arguments, so the guard is reached before argparse.
_PROBE_GATE = "check-cluster-literals.py"

# The third-party imports whose absence must read as an operator error.
_GUARDED_MODULES = {"yaml", "jinja2"}


def _vendored_paths() -> set[str]:
    """Consumer paths scripts/vendored-manifest.yml accounts for.

    A vendored copy is byte-identical to the library, so its guard is fixed
    upstream and arrives with the next re-vendor.
    """
    manifest = yaml.safe_load(MANIFEST.read_text())
    entries = (manifest.get("vendored") or []) + (manifest.get("forked") or [])
    paths = {
        entry if isinstance(entry, str) else (entry.get("consumer") or entry["lib"])
        for entry in entries
    }
    assert paths, f"{MANIFEST} resolved no entries"
    return paths


def _local_scripts() -> list[Path]:
    vendored = _vendored_paths()
    return sorted(
        p for p in SCRIPTS.glob("*.py")
        if not p.name.startswith("test_")
        and p.name != "conftest.py"
        and p.relative_to(REPO).as_posix() not in vendored
    )


def test_no_local_script_exits_one_on_a_missing_dependency():
    offenders = [
        p.name for p in _local_scripts() if _EXIT_ONE_GUARD.search(p.read_text())
    ]
    assert not offenders, (
        "these import guards exit 1, which the runners read as a policy finding: "
        + ", ".join(offenders)
        + "\n\nPrint to stderr and `raise SystemExit(2) from None` instead."
    )


def test_the_pattern_the_rule_above_rejects_is_really_matched():
    """Mutation proof: the rule must fire on the form it exists to ban."""
    assert _EXIT_ONE_GUARD.search('    sys.exit("PyYAML required: pip install pyyaml")')
    assert not _EXIT_ONE_GUARD.search(
        '    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)'
    )


def _module_scope_imports(tree: ast.Module):
    """Top-level imports, including the ones inside a top-level try/except."""
    for node in tree.body:
        yield node
        if isinstance(node, ast.Try):
            yield from node.body


def _imports_a_guarded_module(tree: ast.Module) -> bool:
    for node in _module_scope_imports(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in _GUARDED_MODULES for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _GUARDED_MODULES:
                return True
    return False


def _is_runnable_gate(tree: ast.Module) -> bool:
    """A `__main__` block, which the importable helper modules do not have."""
    return any(
        isinstance(node, ast.If) and "__name__" in ast.dump(node.test)
        for node in tree.body
    )


def _runnable_guarded_gates() -> list[Path]:
    """Every repo-owned runnable gate importing PyYAML or Jinja2 at module scope.

    Derived, so the next such gate cannot be left out of the parametrisation.
    """
    out = []
    for path in _local_scripts():
        tree = ast.parse(path.read_text())
        if _imports_a_guarded_module(tree) and _is_runnable_gate(tree):
            out.append(path)
    return out


GUARDED_GATES = _runnable_guarded_gates()


def test_the_derived_gate_list_is_not_empty():
    assert GUARDED_GATES, "the AST walk found no gates — the derivation is broken"


@pytest.mark.parametrize("gate", GUARDED_GATES, ids=lambda p: p.name)
def test_every_derived_gate_exits_two_without_its_import(gate, tmp_path):
    """Run each gate with both modules shadowed by one that will not import."""
    for name in sorted(_GUARDED_MODULES):
        (tmp_path / f"{name}.py").write_text('raise ImportError("blocked by the test")\n')
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop("PYTHONSAFEPATH", None)
    run = subprocess.run(
        [sys.executable, str(gate), "--help"],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )
    assert run.returncode == 2, (
        f"{gate.name} exited {run.returncode} with no PyYAML/Jinja2 — exit 1 "
        "reads as a policy finding:\n" + run.stdout + run.stderr
    )
    assert "required" in run.stderr


def test_a_gate_without_pyyaml_exits_two(tmp_path):
    """The live guard, with PyYAML shadowed by a module that will not import."""
    gate = SCRIPTS / _PROBE_GATE
    if not gate.is_file():  # pragma: no cover - the probe moved
        pytest.skip(f"{_PROBE_GATE} is gone; point _PROBE_GATE at another gate")
    (tmp_path / "yaml.py").write_text('raise ImportError("blocked by the test")\n')
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop("PYTHONSAFEPATH", None)
    run = subprocess.run(
        [sys.executable, str(gate)],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )
    assert run.returncode == 2, (
        "a missing PyYAML must exit 2 (operator error), not 1 (a finding):\n"
        + run.stdout + run.stderr
    )
    assert "PyYAML" in run.stderr
