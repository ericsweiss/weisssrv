"""The vendored deploy-preflight gate fails each shape that would check nothing."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import require_tool
from script_loader import load_path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-deploy-preflight.py"


def _load():
    # The gate imports its siblings ci_yaml and ci_playbook_invocations, which
    # resolve off sys.path the way `python3 scripts/...` resolves them.
    sys.path.insert(0, str(SCRIPTS))
    return load_path(SCRIPT, register=True)


preflight = _load()

from ci_yaml import load_ci  # noqa: E402

# One `hosts (N):` banner, so a case about tags is not also a case about limits.
HOSTS_BANNER = "      play #1 (all): all\n        hosts (2):"

TAGGED_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-base-role:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod site.yml --tags %s
"""

LIMIT_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-proxmox:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod site.yml --limit proxmox
"""

REFERENCE_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-proxmox:
  extends: .deploy-base
  script:
    - !reference [.absent-template, script]
"""

INHERITED_PIPELINE = """
.deploy-base:
  stage: deploy
  script:
    - ansible-playbook -i inventories/prod site.yml --tags base
deploy-inherited:
  extends: .deploy-base
"""


def _repo(tmp_path: Path, pipeline: str, playbook: str = "site.yml") -> Path:
    (tmp_path / "ansible").mkdir()
    if playbook:
        (tmp_path / "ansible" / playbook).write_text("---\n", encoding="utf-8")
    (tmp_path / ".gitlab-ci.yml").write_text(pipeline, encoding="utf-8")
    return tmp_path


def _fake_ansible(tmp_path: Path, stdout: str, rc: int = 0) -> dict:
    """A stub ansible-playbook on PATH: the real one needs the whole collection."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ansible-playbook"
    stub.write_text(
        "#!/bin/sh\ncat <<'OUT'\n%s\nOUT\nexit %d\n" % (stdout, rc), encoding="utf-8"
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = "%s%s%s" % (bin_dir, os.pathsep, env["PATH"])
    return env


def _run(cwd: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


class TestTagSelections:
    def test_a_tag_reaching_a_task_passes(self, tmp_path):
        repo = _repo(tmp_path, TAGGED_PIPELINE % "base")
        env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [base, ssh]")
        proc = _run(repo, env)
        assert proc.returncode == 0, proc.stderr
        assert "1 tag selection(s) checked" in proc.stdout

    def test_an_always_only_playbook_is_a_silent_no_op(self, tmp_path):
        """`always` tasks print for ANY selection, so output alone proves nothing."""
        repo = _repo(tmp_path, TAGGED_PIPELINE % "base")
        env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [always]")
        proc = _run(repo, env)
        assert proc.returncode == 1
        assert "selects NO task" in proc.stderr

    def test_the_jobs_skip_tags_reach_the_list_tasks_run(self, tmp_path):
        """Without them, a tag whose every task the job skips scores as selected."""
        repo = _repo(tmp_path, TAGGED_PIPELINE % "base --skip-tags reboot")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        argv_log = tmp_path / "argv.log"
        stub = bin_dir / "ansible-playbook"
        stub.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> {argv_log}\n'
            f'echo "{HOSTS_BANNER}"\n'
            'case "$*" in *--skip-tags*) ;; *) echo "        TAGS: [base]" ;; esac\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = "%s%s%s" % (bin_dir, os.pathsep, env["PATH"])
        proc = _run(repo, env)
        assert proc.returncode == 1, proc.stdout
        assert "selects NO task" in proc.stderr
        assert "--skip-tags reboot" in argv_log.read_text()

    def test_a_tagless_job_only_fails_under_the_require_flag(self, tmp_path):
        """The flag is what the CI job and `task lint:deploy-preflight` pass."""
        pipeline = (
            ".deploy-base:\n  stage: deploy\n"
            "deploy-all:\n  extends: .deploy-base\n"
            "  script:\n    - ansible-playbook -i inventories/prod site.yml\n"
        )
        repo = _repo(tmp_path, pipeline)
        env = _fake_ansible(tmp_path, HOSTS_BANNER)
        assert _run(repo, env).returncode == 0
        proc = _run(repo, env, "--require-tag-selections")
        assert proc.returncode == 1
        assert "0 tag selections" in proc.stderr


class TestLimitsAndPlays:
    def test_a_limit_intersecting_every_play_to_nothing_fails(self, tmp_path):
        repo = _repo(tmp_path, LIMIT_PIPELINE)
        env = _fake_ansible(tmp_path, "      play #1 (dns): dns\n        hosts (0):")
        proc = _run(repo, env)
        assert proc.returncode == 1
        assert "matches NO host" in proc.stderr

    def test_a_limit_matching_a_play_passes(self, tmp_path):
        repo = _repo(tmp_path, LIMIT_PIPELINE)
        env = _fake_ansible(
            tmp_path, "      play #1 (proxmox): proxmox\n        hosts (2):"
        )
        assert _run(repo, env).returncode == 0, "a matching limit must pass"

    def test_a_banner_free_list_hosts_is_reported_rather_than_skipped(self, tmp_path):
        """A banner shape the parser cannot read must not read as a clean check."""
        repo = _repo(tmp_path, LIMIT_PIPELINE)
        env = _fake_ansible(tmp_path, "some other banner shape")
        proc = _run(repo, env)
        assert proc.returncode == 1
        assert "no `hosts (N):` line" in proc.stderr

    def test_a_failing_list_hosts_is_an_error_not_a_pass(self, tmp_path):
        repo = _repo(tmp_path, LIMIT_PIPELINE)
        env = _fake_ansible(tmp_path, "ERROR! Specified --limit does not match", rc=1)
        proc = _run(repo, env)
        assert proc.returncode == 1
        assert "--list-hosts failed" in proc.stderr


class TestNothingInspectedIsNeverAPass:
    def test_a_missing_playbook_fails(self, tmp_path):
        repo = _repo(tmp_path, TAGGED_PIPELINE % "base", playbook="")
        proc = _run(repo, _fake_ansible(tmp_path, ""))
        assert proc.returncode == 1
        assert "does not exist" in proc.stderr

    def test_a_pipeline_with_no_deploy_job_is_not_a_pass(self, tmp_path):
        repo = _repo(tmp_path, "lint:\n  script:\n    - echo hi\n")
        proc = _run(repo, _fake_ansible(tmp_path, ""))
        assert proc.returncode == 1
        assert "resolved 0 playbooks" in proc.stderr

    def test_a_reference_into_an_included_file_is_reported(self, tmp_path):
        """The job's real call lives where the parse cannot see it."""
        repo = _repo(tmp_path, REFERENCE_PIPELINE)
        proc = _run(repo, _fake_ansible(tmp_path, ""))
        assert proc.returncode == 1
        assert "resolved to nothing" in proc.stderr

    def test_a_job_whose_parent_is_in_an_included_file_fails(self, tmp_path):
        repo = _repo(tmp_path, "deploy-inherited:\n  extends: .deploy-base\n")
        proc = _run(repo, _fake_ansible(tmp_path, ""))
        assert proc.returncode == 1
        assert "no script to inspect" in proc.stderr

    def test_a_job_inheriting_its_invocation_from_a_parent_is_inspected(self, tmp_path):
        repo = _repo(tmp_path, INHERITED_PIPELINE)
        env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [base]")
        assert _run(repo, env).returncode == 0, "the chain resolves in this file"

    def test_an_unparsed_invocation_fails_the_completeness_assertion(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(preflight, "parse_invocations", lambda text: [])
        failures = preflight.check(
            _repo(tmp_path, TAGGED_PIPELINE % "base") / ".gitlab-ci.yml",
            tmp_path / "ansible", [".deploy-base"], False,
        )
        assert any("parsed 0 of 1" in f for f in failures), failures

    def test_a_missing_ansible_playbook_is_an_operator_error(self, tmp_path):
        repo = _repo(tmp_path, TAGGED_PIPELINE % "base")
        env = dict(os.environ)
        env["PATH"] = str(tmp_path / "nonexistent")
        proc = _run(repo, env)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "ansible-playbook" in proc.stderr

    def test_a_missing_pipeline_file_is_an_operator_error(self, tmp_path):
        assert preflight.main(["--ci-file", str(tmp_path / "nope.yml")]) == 2

    def test_a_missing_ansible_dir_is_an_operator_error(self, tmp_path):
        repo = _repo(tmp_path, TAGGED_PIPELINE % "base")
        assert preflight.main([
            "--ci-file", str(repo / ".gitlab-ci.yml"),
            "--ansible-dir", str(tmp_path / "nope"),
        ]) == 2


def test_the_repo_pipeline_still_resolves_deploy_jobs():
    """The default `--extends` pair must still match this repo's deploy jobs."""
    doc = load_ci(SCRIPTS.parent / ".gitlab-ci.yml")
    found = preflight._candidates(doc, [".deploy-base", ".maintenance-base"])
    assert found, "no job extends .deploy-base or .maintenance-base any more"


def test_the_repo_pipeline_passes_the_gate_as_ci_runs_it():
    """The CI job's exact argv, over the real tree."""
    require_tool(
        "ansible-playbook", "deploy-preflight", "pip install ansible"
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--require-tag-selections"],
        cwd=SCRIPTS.parent, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
