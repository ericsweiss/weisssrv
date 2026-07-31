#!/usr/bin/env python3
"""Unit tests for scripts/resolve-tool.sh.

resolve-tool.sh is the single source of truth for the 3-tier dev-tool
resolution chain (PATH -> `python3 -m <module>` -> validated pyenv glob) used by
the Taskfile and ansible/test-all-roles.sh. Each test invokes the script in a
bash subprocess under a controlled PATH/HOME/PYTHONPATH so no real tool on the
developer's machine leaks into the result, and asserts the printed invocation
and exit code for one tier.

Run with pytest:
    pytest scripts/test_resolve_tool.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "resolve-tool.sh"
BASH = shutil.which("bash") or "/bin/bash"
PYTHON3 = shutil.which("python3")

# A minimal PATH that can find bash/python3 but none of the fake tool names the
# tests use (faketool/sometool/notonpathtool/notfoundtool), so tier resolution
# is driven entirely by the stubs each test plants.
_python_dir = str(Path(PYTHON3).parent) if PYTHON3 else "/usr/bin"
BASE_PATH = os.pathsep.join(dict.fromkeys([_python_dir, "/usr/bin", "/bin"]))


def _run(*args, path=None, home=None, pythonpath=None) -> subprocess.CompletedProcess:
    """Run resolve-tool.sh with a fully controlled environment.

    Passing `path`/`home`/`pythonpath` overrides PATH/HOME/PYTHONPATH; the env is
    otherwise empty so nothing from the caller's shell leaks into resolution.
    """
    env = {"PATH": path if path is not None else BASE_PATH}
    if home is not None:
        env["HOME"] = str(home)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def _make_stub(path: Path, body: str = "exit 0", mode: int = 0o755) -> Path:
    """Write an executable /bin/sh stub (mode 0755 by default) at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(mode)
    return path


# tier 1: on PATH

class TestTier1OnPath:
    def test_tool_on_path_wins(self, tmp_path):
        # PATH is ONLY the stub dir; `command -v` is a builtin so no other
        # entries are needed. The tool name is echoed verbatim.
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir / "faketool")
        home = tmp_path / "home"
        home.mkdir()
        res = _run("faketool", path=str(bin_dir), home=home)
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == "faketool"


# tier 2: python3 -m <module>

class TestTier2PythonModule:
    def test_python_module_resolves(self, tmp_path):
        # Skip only when the environment itself cannot run the throwaway module
        # (no python3, etc.) — not to mask a resolver bug.
        if PYTHON3 is None:
            pytest.skip("python3 not available")
        mod_dir = tmp_path / "pymods"
        mod_dir.mkdir()
        (mod_dir / "mymod.py").write_text('import sys; print("mymod 1.0")\n')
        home = tmp_path / "home"
        home.mkdir()

        pre = subprocess.run(
            [PYTHON3, "-m", "mymod", "--version"],
            env={"PATH": BASE_PATH, "PYTHONPATH": str(mod_dir)},
            capture_output=True,
            text=True,
        )
        if pre.returncode != 0:
            pytest.skip(f"env cannot run 'python3 -m mymod': {pre.stderr.strip()}")

        # notonpathtool is not on PATH (tier 1 misses) -> tier 2 resolves it.
        res = _run("notonpathtool", "mymod", path=BASE_PATH, home=home, pythonpath=mod_dir)
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == "python3 -m mymod"


# tier 3: validated pyenv glob

class TestTier3PyenvGlob:
    def _candidate(self, home: Path) -> Path:
        return home / ".pyenv" / "versions" / "3.13.5" / "bin" / "sometool"

    def test_pyenv_candidate_resolves(self, tmp_path):
        # Not on PATH, no module arg -> falls through to the pyenv glob. The
        # stub answers --version so it passes the validation gate.
        home = tmp_path / "home"
        candidate = self._candidate(home)
        _make_stub(candidate, body='echo "sometool 1.0"; exit 0')
        res = _run("sometool", path=BASE_PATH, home=home)
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == str(candidate)

    def test_non_executable_candidate_rejected(self, tmp_path):
        # Present but not executable (broken/partial install) -> the -x gate
        # rejects it and nothing else resolves.
        home = tmp_path / "home"
        candidate = self._candidate(home)
        _make_stub(candidate, body='echo "sometool 1.0"', mode=0o644)
        res = _run("sometool", path=BASE_PATH, home=home)
        assert res.returncode == 1
        assert res.stdout.strip() == ""

    def test_failing_version_candidate_rejected(self, tmp_path):
        # Executable but --version fails (broken install) -> the run-check gate
        # rejects it and nothing else resolves.
        home = tmp_path / "home"
        candidate = self._candidate(home)
        _make_stub(candidate, body="exit 3")
        res = _run("sometool", path=BASE_PATH, home=home)
        assert res.returncode == 1
        assert res.stdout.strip() == ""


# nothing found

class TestNothingFound:
    def test_no_tier_matches(self, tmp_path):
        # Not on PATH, no module, empty HOME (no ~/.pyenv) -> rc 1, no output.
        home = tmp_path / "home"
        home.mkdir()
        res = _run("notfoundtool", path=BASE_PATH, home=home)
        assert res.returncode == 1
        assert res.stdout.strip() == ""

    def test_module_that_fails_then_nothing(self, tmp_path):
        # A module that can't be imported must not resolve tier 2; with no pyenv
        # candidate either, the result is rc 1 and no output.
        home = tmp_path / "home"
        home.mkdir()
        res = _run(
            "notfoundtool",
            "definitely_missing_module_xyz",
            path=BASE_PATH,
            home=home,
        )
        assert res.returncode == 1
        assert res.stdout.strip() == ""


# usage

class TestUsage:
    def test_no_args_is_usage_error(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        res = _run(path=BASE_PATH, home=home)
        assert res.returncode == 2
        assert res.stdout.strip() == ""
        assert "Usage" in res.stderr


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
