"""Smoke tests proving the scripts vendored from weisssrv-lib are runnable here.

Behaviour lives in the library, site config in test_site_configs.py, byte
identity in test_vendored_byte_identity.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from test_vendored_byte_identity import registered_consumer_paths
from script_loader import load_script

SCRIPTS = Path(__file__).resolve().parent

# Derived from this repo's manifest, never re-listed here — a newly vendored
# script is smoke-tested the moment it is registered.
_VENDORED = sorted(
    Path(p).name for p in registered_consumer_paths() if p.startswith("scripts/")
)
PY_SCRIPTS = [n for n in _VENDORED if n.endswith(".py")]
SH_SCRIPTS = [n for n in _VENDORED if n.endswith(".sh")]

# The vendored CLIs whose --help must render, derived the same way: an argparse
# script that raises on import of its own parser is otherwise only caught in CI.
# A vendored `test_` suite names argparse while having no CLI of its own.
HELP_SCRIPTS = [
    n for n in PY_SCRIPTS
    if not n.startswith("test_") and "argparse" in (SCRIPTS / n).read_text(encoding="utf-8")
]


def test_the_manifest_was_readable():
    """An empty parametrisation would silently pass every case below."""
    assert _VENDORED, (
        "no vendored scripts/ entries resolved from scripts/vendored-manifest.yml — see "
        "test_vendored_byte_identity.py for the checkout requirement"
    )
    assert HELP_SCRIPTS, (
        "no vendored script matched the argparse filter — a changed import style "
        "would otherwise empty the --help parametrisation"
    )


@pytest.mark.parametrize("name", PY_SCRIPTS)
def test_python_script_imports(name):
    """Import (never run) each copy: syntax errors and import-time failures are
    the whole failure class a byte-comparison cannot see."""
    load_script(name)


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
