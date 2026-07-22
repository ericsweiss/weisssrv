#!/usr/bin/env python3
"""Unit tests for scripts/check-taskfile.sh.

check-taskfile.sh is the taskfile-smoke CI gate: it asserts every
scripts/<name>.{sh,py} referenced by Taskfile.yml exists on disk, plus the
`dotenv:` target scripts/hosts.env (go-task evaluates command templates lazily
and never stats referenced files, so a since-renamed script reference isn't
caught by `task --list` alone). Every sibling gate script has a test suite; this
pins check-taskfile.sh's contract — including the hosts.env multi-line-list
matcher — the same way.

The script derives REPO_ROOT from its own location and resolves references
against it, so each test builds a throwaway repo (a copy of the script under
<root>/scripts/ + a fixture Taskfile) to control which scripts / hosts.env are
present.

Run with pytest:
    pytest scripts/test_check_taskfile.py -v
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-taskfile.sh"


def _make_repo(tmp_path: Path, taskfile_text: str,
               scripts: tuple[str, ...] = (), hosts_env: bool = False) -> Path:
    """Build a throwaway repo: <root>/scripts/check-taskfile.sh (copied) plus
    the named fixture scripts and (optionally) hosts.env, and <root>/Taskfile.yml
    with the given text. Returns the repo root."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "check-taskfile.sh")
    for name in scripts:
        (root / "scripts" / name).write_text("# fixture\n")
    if hosts_env:
        (root / "scripts" / "hosts.env").write_text("FOO=bar\n")
    (root / "Taskfile.yml").write_text(taskfile_text)
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(root / "scripts" / "check-taskfile.sh"),
         str(root / "Taskfile.yml")],
        capture_output=True, text=True,
    )


class TestScriptReferences:
    def test_existing_reference_passes(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n      - bash scripts/foo.sh\n",
            scripts=("foo.sh",),
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr
        assert "OK:" in res.stdout

    def test_missing_reference_fails(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n      - python3 scripts/gone.py\n",
        )
        res = _run(root)
        assert res.returncode == 1
        assert "scripts/gone.py" in res.stderr

    def test_one_missing_among_many_fails(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n"
            "      - bash scripts/foo.sh\n"
            "      - bash scripts/missing.sh\n",
            scripts=("foo.sh",),
        )
        res = _run(root)
        assert res.returncode == 1
        assert "scripts/missing.sh" in res.stderr

    def test_missing_taskfile_fails(self, tmp_path: Path):
        # The `[ -f "$TASKFILE" ]` guard: a Taskfile path that does not exist
        # must exit 1 with a clear message, not silently pass on an empty grep.
        root = tmp_path / "repo"
        (root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, root / "scripts" / "check-taskfile.sh")
        # No Taskfile.yml written — _run points at the non-existent path.
        res = _run(root)
        assert res.returncode == 1
        assert "not found" in res.stderr.lower()


class TestHostsEnvDotenv:
    def test_dotenv_present_passes(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "dotenv: ['scripts/hosts.env']\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=True,
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr

    def test_dotenv_referenced_but_missing_fails(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "dotenv: ['scripts/hosts.env']\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root)
        assert res.returncode == 1
        assert "hosts.env" in res.stderr

    def test_multiline_list_form_is_matched(self, tmp_path: Path):
        # go-task also accepts the YAML multi-line list form; the bare-path
        # matcher (not a same-line `dotenv:` match) must catch it too.
        root = _make_repo(
            tmp_path,
            "dotenv:\n  - scripts/hosts.env\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root)
        assert res.returncode == 1
        assert "hosts.env" in res.stderr

    def test_absent_but_unreferenced_passes(self, tmp_path: Path):
        # hosts.env is only required when the Taskfile references it as a dotenv
        # target. A Taskfile that never mentions it must PASS with hosts.env
        # absent — the check must not demand a file nothing depends on. (Guards
        # the `grep -q ... &&` short-circuit: no reference => no requirement.)
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr
        assert "OK:" in res.stdout


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
