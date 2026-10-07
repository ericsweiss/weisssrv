"""Every supervised mutation path keeps its confirmation ceremony.

The Taskfile wiring that puts supervised-apply-guard.sh before any `terraform
apply`, plus b2-bucket-drift.py's refusals on the offsite bucket.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
TASKFILE = REPO / "Taskfile.yml"
TASKFILES_DIR = REPO / "taskfiles"
GUARD_REF = "scripts/supervised-apply-guard.sh"
ROOT_ANCHOR = "{{.ROOT_DIR}}/"
APPLY_CMD = "terraform apply"
# The Cloudflare DNS root is the one apply CI runs on merge, so it carries no
# guard; `terraform:apply` is its alias and runs no apply of its own.
UNGUARDED_APPLY = {"terraform:cloudflare-apply"}


def _taskfiles() -> list[Path]:
    files = [TASKFILE] if TASKFILE.is_file() else []
    if TASKFILES_DIR.is_dir():
        files.extend(sorted(TASKFILES_DIR.glob("*.yml")))
    return files


def _tasks() -> dict[str, dict]:
    """Fully qualified task name -> task body, across the Taskfile tree.

    A task in taskfiles/<ns>.yml is addressed as `<ns>:<task>`, so the file stem
    is the namespace prefix.
    """
    tasks: dict[str, dict] = {}
    for path in _taskfiles():
        doc = yaml.safe_load(path.read_text()) or {}
        prefix = "" if path == TASKFILE else f"{path.stem}:"
        for name, body in (doc.get("tasks") or {}).items():
            if isinstance(body, dict):
                tasks[f"{prefix}{name}"] = body
    return tasks


def _cmd_strings(task: dict) -> list[str]:
    """Each `cmds` entry as text, so a `task:`/`cmd:` mapping is searchable too."""
    return [
        cmd if isinstance(cmd, str) else yaml.safe_dump(cmd, default_flow_style=True)
        for cmd in (task.get("cmds") or [])
    ]


def _apply_tasks(tasks: dict[str, dict]) -> dict[str, list[str]]:
    """Task name -> command strings, for every task running `terraform apply`."""
    found: dict[str, list[str]] = {}
    for name, body in tasks.items():
        cmds = _cmd_strings(body)
        if any(APPLY_CMD in cmd for cmd in cmds):
            found[name] = cmds
    return found


def _supervised_descs(tasks: dict[str, dict]) -> set[str]:
    return {
        name
        for name, body in tasks.items()
        if str(body.get("desc") or "").startswith("SUPERVISED")
    }


def _guard_violations(tasks: dict[str, dict]) -> list[str]:
    """Apply tasks outside UNGUARDED_APPLY whose first command is not the guard."""
    violations = []
    for name, cmds in sorted(_apply_tasks(tasks).items()):
        if name in UNGUARDED_APPLY:
            continue
        guarded = [i for i, cmd in enumerate(cmds) if GUARD_REF in cmd]
        applied = [i for i, cmd in enumerate(cmds) if APPLY_CMD in cmd]
        if not guarded:
            violations.append(f"{name} applies without calling {GUARD_REF}")
        elif guarded[0] != 0:
            violations.append(f"{name} runs {cmds[0]!r} before the guard")
        elif applied and guarded[0] > applied[0]:
            violations.append(f"{name} applies before the guard runs")
    return violations


def _root_anchor_violations(tasks: dict[str, dict]) -> list[str]:
    """Guard calls in a task that sets `dir:` and does not anchor the path.

    go-task runs every cmd with `dir:` as cwd, so a repo-relative guard path
    exits 127 and the apply task dies before it can ask anything.
    """
    violations = []
    for name, body in sorted(tasks.items()):
        if not body.get("dir"):
            continue
        for cmd in _cmd_strings(body):
            if GUARD_REF in cmd and ROOT_ANCHOR + GUARD_REF not in cmd:
                violations.append(f"{name} calls {GUARD_REF} relative to its dir:")
    return violations


class TestGuardRefusals:
    """The guard itself: a non-tty, an -auto-approve spelling, or an empty
    confirm word must all refuse before anything is applied."""

    GUARD = SCRIPTS / "supervised-apply-guard.sh"

    def _run(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            ["bash", str(self.GUARD), *args],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
            env={**os.environ, **(env or {})},
        )

    def test_too_few_arguments_exits_2(self):
        assert self._run("terraform:demo-apply").returncode == 2

    def test_a_non_tty_refuses(self):
        result = self._run("terraform:demo-apply", "the live tailnet")
        assert result.returncode == 2
        assert "run it from a terminal" in result.stderr

    def _auto_approve_arm(self) -> str:
        """The case block alone: the tty refusal above it fires first in tests."""
        lines = self.GUARD.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("case "))
        end = next(i for i in range(start, len(lines)) if lines[i] == "esac")
        return "\n".join(lines[start:end + 1])

    @pytest.mark.parametrize("flag", ["-auto-approve", "-auto-approve=true",
                                      "--auto-approve"])
    def test_every_auto_approve_spelling_refuses(self, flag):
        result = subprocess.run(
            ["bash", "-c", f'task_name=demo\nset -- {flag}\n{self._auto_approve_arm()}'],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 2
        assert "auto-approve is refused" in result.stderr

    def test_a_plain_argument_clears_the_auto_approve_arm(self):
        result = subprocess.run(
            ["bash", "-c", f'task_name=demo\nset -- -no-color\n{self._auto_approve_arm()}'],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_an_empty_confirm_word_refuses(self):
        """Mutation case: with `:-` instead of `-` a bare Enter would approve."""
        result = self._run("terraform:demo-apply", "the live tailnet",
                           env={"SUPERVISED_APPLY_CONFIRM_WORD": ""})
        assert result.returncode == 2
        assert "SUPERVISED_APPLY_CONFIRM_WORD is empty" in result.stderr


class TestTaskfileWiring:
    def test_every_supervised_apply_runs_the_guard_first(self):
        tasks = _tasks()
        assert tasks, "no Taskfile tasks parsed — this gate is examining nothing"
        applies = _apply_tasks(tasks)
        assert applies, "no task runs `terraform apply` — this gate examines nothing"
        assert set(applies) - UNGUARDED_APPLY, (
            "every `terraform apply` task is exempt — this gate examines nothing"
        )
        violations = _guard_violations(tasks)
        assert not violations, (
            "a Terraform apply can run unsupervised:\n  " + "\n  ".join(violations)
        )

    def test_every_guard_call_is_anchored_to_the_repo_root(self):
        tasks = _tasks()
        callers = [
            name
            for name, body in tasks.items()
            if any(GUARD_REF in cmd for cmd in _cmd_strings(body))
        ]
        assert callers, "no task calls the guard — this gate is examining nothing"
        violations = _root_anchor_violations(tasks)
        assert not violations, (
            "a supervised apply cannot reach its guard:\n  " + "\n  ".join(violations)
        )

    def test_the_supervised_descriptions_match_the_guarded_tasks(self):
        """A new apply task is either marked SUPERVISED and guarded, or listed in
        UNGUARDED_APPLY on purpose."""
        tasks = _tasks()
        applies = set(_apply_tasks(tasks))
        expected = applies - UNGUARDED_APPLY
        assert expected, "no supervised apply task — this gate is examining nothing"
        assert _supervised_descs(tasks) & applies == expected, (
            "the apply tasks whose desc starts with SUPERVISED are "
            f"{sorted(_supervised_descs(tasks) & applies)} but the guarded set is "
            f"{sorted(expected)}"
        )

    @pytest.mark.parametrize(
        "cmds",
        [
            pytest.param(["op run -- terraform apply"], id="guard-dropped"),
            pytest.param(
                ["op run -- terraform apply", f"{GUARD_REF} terraform:unifi-apply x"],
                id="guard-after-apply",
            ),
        ],
    )
    def test_the_wiring_check_can_fail(self, cmds):
        """Mutation case: a guard that is absent or too late is reported."""
        assert _guard_violations({"terraform:unifi-apply": {"cmds": cmds}})

    def test_the_root_anchor_check_can_fail(self):
        """Mutation case: the anchor matters only for a task that changes dir."""
        relative = {"cmds": [f"{GUARD_REF} terraform:x y"], "dir": "terraform/x"}
        anchored = {"cmds": [f"{ROOT_ANCHOR}{GUARD_REF} terraform:x y"],
                    "dir": "terraform/x"}
        assert _root_anchor_violations({"terraform:x-apply": relative})
        assert not _root_anchor_violations({"terraform:x-apply": anchored})
        assert not _root_anchor_violations({"terraform:x-apply": {"cmds": relative["cmds"]}})


# --- b2-bucket-drift.py: the offsite bucket's own ceremony --------------------
# The guard script's own refusals are driven on a pty in test_taskfile_shell.py.

b2 = load_script("b2-bucket-drift.py")

CONFIG = SCRIPTS / "b2-bucket.json"


@pytest.fixture()
def b2_api(monkeypatch):
    """Stub every B2 call. Returns the recorded mutation payloads."""
    cfg = json.loads(CONFIG.read_text())
    mutations: list[dict] = []
    # A live bucket that differs from the codified settings, so every test
    # reaches the apply ceremony rather than exiting clean.
    bucket = {"bucketName": cfg["bucket_name"], "bucketType": "allPublic",
              "lifecycleRules": []}

    def fake_api(url, token=None, body=None, basic=None):
        if "b2_authorize_account" in url:
            return {"apiInfo": {"storageApi": {"apiUrl": "https://api.invalid"}},
                    "authorizationToken": "t"}
        if "b2_list_buckets" in url:
            return {"buckets": [bucket]}
        if "b2_update_bucket" in url:
            mutations.append(body)
            return {}
        raise AssertionError(f"unexpected B2 call: {url}")

    monkeypatch.setattr(b2, "_api", fake_api)
    monkeypatch.setenv("B2_APPLICATION_KEY_ID", "id")
    monkeypatch.setenv("B2_APPLICATION_KEY", "key")
    return mutations, bucket


def _main(*argv: str) -> int:
    return b2.main(["--config", str(CONFIG), *argv])


def test_a_non_interactive_apply_refuses_and_mutates_nothing(b2_api, monkeypatch, capsys):
    mutations, _ = b2_api
    monkeypatch.setattr(b2.sys.stdin, "isatty", lambda: False, raising=False)
    assert _main("--apply") == 2
    assert "interactive terminal" in capsys.readouterr().out
    assert mutations == []


def test_an_answer_other_than_yes_aborts(b2_api, monkeypatch, capsys):
    mutations, _ = b2_api
    monkeypatch.setattr(b2.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert _main("--apply") == 1
    assert "ABORTED" in capsys.readouterr().out
    assert mutations == []


def test_a_bucket_name_mismatch_refuses_before_the_prompt(b2_api, monkeypatch):
    mutations, bucket = b2_api
    bucket["bucketName"] = "some-other-bucket"
    monkeypatch.setattr(b2.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert _main("--apply") == 2
    assert mutations == []


def test_a_drift_report_without_apply_never_mutates(b2_api):
    mutations, _ = b2_api
    assert _main() == 1
    assert mutations == []


def test_the_typed_confirmation_does_apply(b2_api, monkeypatch):
    """The refusals above are only meaningful if the yes path still works."""
    mutations, bucket = b2_api

    def accept(_prompt):
        cfg = json.loads(CONFIG.read_text())
        desired = cfg["desired"]
        bucket.clear()
        bucket.update({
            "bucketName": cfg["bucket_name"],
            "bucketType": desired["bucketType"],
            "defaultServerSideEncryption": {
                "isClientAuthorizedToRead": True,
                "value": desired["defaultServerSideEncryption"],
            },
            "lifecycleRules": desired["lifecycleRules"],
            "fileLockConfiguration": {
                "isClientAuthorizedToRead": True,
                "value": {"defaultRetention": desired["defaultRetention"]},
            },
        })
        return "yes"

    monkeypatch.setattr(b2.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", accept)
    assert _main("--apply") == 0
    assert len(mutations) == 1
