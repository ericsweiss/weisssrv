#!/usr/bin/env python3
"""Unit tests for scripts/check-env-secret-coverage.py.

The gate exists because `task infra:deploy` / `infra:check` hard-failed for
weeks: site.yml runs restic_offsite on pve-nas-01, whose secrets resolve from
B2_KEY_ID / B2_APPLICATION_KEY / RESTIC_REPO_PASSWORD, and the shared
&infra_env anchor exported none of them. These tests pin both directions —
the repo passes as it stands, and the check fails when the export is removed.

Run with pytest:
    pytest scripts/test_check_env_secret_coverage.py -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-env-secret-coverage.py"
REPO = SCRIPT.parent.parent
TASKFILE = REPO / "Taskfile.yml"


def _load():
    spec = importlib.util.spec_from_file_location("check_env_secret_coverage", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(taskfile: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT)]
    if taskfile is not None:
        cmd += ["--taskfile", str(taskfile)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)


class TestRepoState:
    def test_repo_passes(self):
        res = _run()
        assert res.returncode == 0, res.stdout + res.stderr

    def test_site_yml_tasks_are_actually_checked(self):
        # A gate that silently checks nothing passes forever. Pin that the
        # infra:deploy -> site.yml -> restic_offsite path is in scope.
        mod = _load()
        tasks = dict(
            (name, (stem, env)) for name, stem, env in mod.taskfile_playbook_tasks()
        )
        assert "infra:deploy" in tasks
        assert tasks["infra:deploy"][0] == "site"
        assert "restic_offsite" in mod.playbook_roles(mod.PLAYBOOKS / "site.yml")

    def test_infra_env_exports_the_b2_secrets(self):
        mod = _load()
        for name, stem, env in mod.taskfile_playbook_tasks():
            if stem != "site":
                continue
            for required in (
                "B2_KEY_ID",
                "B2_APPLICATION_KEY",
                "RESTIC_REPO_PASSWORD",
            ):
                assert required in env, f"{name} does not export {required}"


class TestDetectsDrift:
    @pytest.mark.parametrize(
        "dropped",
        ["B2_KEY_ID", "B2_APPLICATION_KEY", "RESTIC_REPO_PASSWORD"],
    )
    def test_removing_an_export_fails_the_check(self, dropped, tmp_path):
        """Mutation proof: drop one export from &infra_env, the check must red."""
        original = TASKFILE.read_text()
        mutated = "\n".join(
            line
            for line in original.splitlines()
            if not line.strip().startswith(f"{dropped}: op://")
        )
        assert mutated != original, f"{dropped} not found in Taskfile.yml"
        copy = tmp_path / "Taskfile.yml"
        copy.write_text(mutated + "\n")
        res = _run(copy)
        assert res.returncode == 1, (
            f"dropping {dropped} did not fail the check:\n{res.stdout}"
        )
        assert dropped in res.stdout
        assert "site.yml" in res.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
