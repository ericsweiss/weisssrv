"""Smoke tests for the scripts vendored from weisssrv-lib.

The exhaustive behaviour suites live in the library, next to the code they
describe, and re-hosting them here would only test the same file twice. What
this repo still has to prove is that its COPIES are runnable: a Python script
that no longer imports, a shell script with a syntax error, or a CLI that lost
its argparse wiring is broken here whatever the library's suite says.

Site behaviour — the config files these scripts read — is covered by
test_site_configs.py; that the copies are unmodified, by
test_vendored_byte_identity.py.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from test_vendored_byte_identity import registered_consumer_paths

SCRIPTS = Path(__file__).resolve().parent

# Derived from the library's registry, never re-listed here — a newly vendored
# script is smoke-tested the moment it is registered.
_VENDORED = sorted(
    Path(p).name for p in registered_consumer_paths() if p.startswith("scripts/")
)
PY_SCRIPTS = [n for n in _VENDORED if n.endswith(".py")]
SH_SCRIPTS = [n for n in _VENDORED if n.endswith(".sh")]

# The vendored CLIs whose --help must render: an argparse script that raises on
# import of its own parser is otherwise only caught in CI.
HELP_SCRIPTS = [
    "b2-bucket-drift.py",
    "check-hpa-vpa-invariant.py",
    "check-kubectl-version-pin.py",
    "check-lib-pins.py",
    "check-versions.py",
    "generate-hosts-env.py",
    "generate-versions-configmap.py",
    "validate-helm-values.py",
    "version-bump-mr.py",
    "version-check-ci.py",
]


def test_the_registry_was_readable():
    """An empty parametrisation would silently pass every case below."""
    assert _VENDORED, (
        "no vendored scripts/ entries resolved from the weisssrv-lib registry — see "
        "test_vendored_byte_identity.py for the checkout requirement"
    )


@pytest.mark.parametrize("name", PY_SCRIPTS)
def test_python_script_imports(name):
    """Import (never run) each copy: syntax errors and import-time failures are
    the whole failure class a byte-comparison cannot see."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3], SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


@pytest.mark.parametrize("name", SH_SCRIPTS)
def test_shell_script_parses(name):
    run = subprocess.run(["bash", "-n", str(SCRIPTS / name)], capture_output=True, text=True)
    assert run.returncode == 0, f"{name}: {run.stderr}"


@pytest.mark.parametrize("name", HELP_SCRIPTS)
def test_cli_help_renders(name):
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / name), "--help"], capture_output=True, text=True
    )
    assert run.returncode == 0, f"{name} --help exited {run.returncode}: {run.stderr}"
    assert "usage:" in run.stdout.lower()
