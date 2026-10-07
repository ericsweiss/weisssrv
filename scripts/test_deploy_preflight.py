"""The deploy-preflight gate parses each job shape and fails its completeness check."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
import yaml
from conftest import require_tool
from script_loader import load_path

SCRIPTS = Path(__file__).resolve().parent


def _load():
    # deploy-preflight.py imports its sibling ci_playbook_invocations, which
    # resolves off sys.path the way `python3 scripts/...` resolves it.
    sys.path.insert(0, str(SCRIPTS))
    return load_path(SCRIPTS / "deploy-preflight.py", register=True)


preflight = _load()

from ci_playbook_invocations import load_ci, parse_ci  # noqa: E402


class TestDeployJobSelection:
    def test_a_job_naming_several_parents_is_still_inspected(self):
        pipeline = yaml.safe_load(
            "deploy-x:\n"
            "  extends: [.install-1password, .deploy-base]\n"
            "  script: [true]\n"
            "lint-x:\n"
            "  extends: .other-base\n"
            "  script: [true]\n"
        )
        assert set(preflight.deploy_jobs(pipeline)) == {"deploy-x"}

    def test_hidden_jobs_are_not_inspected(self):
        pipeline = yaml.safe_load(".maintenance-base:\n  extends: .deploy-base\n")
        assert preflight.deploy_jobs(pipeline) == {}


class TestCheck:
    def test_an_unparsed_invocation_fails_the_completeness_assertion(self, tmp_path):
        """A job the parser cannot read must fail loudly, not shrink coverage."""
        pipeline = {
            "deploy-opaque": {
                "extends": ".deploy-base",
                # No `.yml` token, so nothing parses out of a written call.
                "script": ["ansible-playbook $PLAYBOOK_FROM_A_VARIABLE"],
            }
        }
        failures = preflight.check(pipeline, tmp_path)
        assert any("parsed 0 of 1" in f for f in failures), failures

    def test_a_candidate_with_an_inherited_script_fails(self, tmp_path):
        """A job whose script lives in the base is invisible to the parser."""
        pipeline = {
            ".deploy-base": {
                "script": ["ansible-playbook -i inventories/prod playbooks/base.yml"],
            },
            "deploy-inherited": {"extends": ".deploy-base"},
        }
        failures = preflight.check(pipeline, tmp_path)
        assert any("no `script` of its own" in f for f in failures), failures

    def test_a_missing_playbook_fails(self, tmp_path):
        pipeline = {
            "deploy-gone": {
                "extends": ".deploy-base",
                "script": ["ansible-playbook -i inventories/prod playbooks/gone.yml"],
            }
        }
        failures = preflight.check(pipeline, tmp_path)
        assert any("does not exist" in f for f in failures), failures

    def test_a_pipeline_with_no_deploy_job_is_not_a_pass(self, tmp_path):
        failures = preflight.check({"lint": {"script": ["true"]}}, tmp_path)
        assert any("0 playbooks" in f for f in failures), failures

    def test_a_playbook_with_no_tag_selection_is_not_a_pass(self, tmp_path):
        """Resolving playbooks but zero `--tags` means the silent-no-op guard
        inspected nothing, which must not read as clean."""
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/dns.yml").write_text("[]\n")
        pipeline = {
            "deploy-dns": {
                "extends": ".deploy-base",
                "script": ["ansible-playbook -i inventories/prod playbooks/dns.yml"],
            }
        }
        failures = preflight.check(pipeline, tmp_path)
        assert failures and all("0 playbooks" not in f for f in failures)
        assert any("0 tag selections" in f for f in failures), failures


class TestTagSelectionReachesARealTask:
    """`--list-tasks` prints `always` tasks for any selection, so only a task's
    own tag list proves a deploy step does something."""

    @pytest.fixture(autouse=True)
    def _ansible_playbook(self):
        require_tool(
            "ansible-playbook",
            "deploy-preflight tag-selection",
            "Install ansible in the deploy-preflight job image.",
        )

    @staticmethod
    def _pipeline(tmp_path, tag: str) -> dict:
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/acme.yml").write_text(
            "- name: fixture\n"
            "  hosts: localhost\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: one task\n"
            "      ansible.builtin.debug:\n"
            "        msg: hi\n"
            f"      tags: [{tag}]\n"
        )
        return {
            "deploy-acme": {
                "extends": ".deploy-base",
                "script": ["ansible-playbook playbooks/acme.yml --tags acme_certs"],
            }
        }

    def test_an_always_only_playbook_is_a_silent_no_op(self, tmp_path):
        failures = preflight.check(self._pipeline(tmp_path, "always"), tmp_path)
        assert any("selects NO task" in f for f in failures), failures

    def test_a_task_carrying_the_tag_passes(self, tmp_path):
        failures = preflight.check(self._pipeline(tmp_path, "acme_certs"), tmp_path)
        assert not failures, failures


class TestTagSelectionWithAStubbedAnsible:
    """The same discriminator against a stub, so CI runs it without ansible."""

    @staticmethod
    def _stub(tmp_path: Path, monkeypatch, tags: str, hosts: str = "1") -> None:
        """A fake ansible-playbook answering both --list-hosts and --list-tasks.

        `hosts` is the count the host banner reports, or "none" for a
        banner-free shape the PLAY_HOSTS regex finds nothing in.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        banner = (
            "" if hosts == "none"
            else f"echo '  play #1 (all): all\tTAGS: []'\necho '    hosts ({hosts}):'\n"
        )
        stub = bin_dir / "ansible-playbook"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'for arg in "$@"; do\n'
            '  if [ "$arg" = "--list-hosts" ]; then\n'
            f"    {banner or 'true'}\n"
            "    exit 0\n"
            "  fi\n"
            "done\n"
            f"echo '      TAGS: [{tags}]'\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    @staticmethod
    def _pipeline(tmp_path: Path) -> dict:
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/acme.yml").write_text("[]\n")
        return {
            "deploy-acme": {
                "extends": ".deploy-base",
                "script": ["ansible-playbook playbooks/acme.yml --tags acme_certs"],
            }
        }

    def test_an_always_only_playbook_is_a_silent_no_op(self, tmp_path, monkeypatch):
        self._stub(tmp_path, monkeypatch, "always")
        failures = preflight.check(self._pipeline(tmp_path), tmp_path)
        assert any("selects NO task" in f for f in failures), failures

    def test_a_task_carrying_the_tag_passes(self, tmp_path, monkeypatch):
        self._stub(tmp_path, monkeypatch, "acme_certs")
        assert preflight.check(self._pipeline(tmp_path), tmp_path) == []


class TestLimitIntersectsAPlay:
    """A `--limit` that matches no host makes the whole deploy step a no-op, and
    the job still exits 0, so the pipeline stays green."""

    @staticmethod
    def _pipeline(limit: str | None) -> dict:
        limit_argv = f" --limit {limit}" if limit else ""
        return {
            "deploy-acme": {
                "extends": ".deploy-base",
                "script": [
                    "ansible-playbook -i inventories/prod playbooks/acme.yml"
                    f"{limit_argv} --tags acme_certs"
                ],
            }
        }

    @staticmethod
    def _tree(tmp_path: Path, play_hosts: str = "web") -> None:
        inventory = tmp_path / "inventories/prod"
        inventory.mkdir(parents=True)
        (inventory / "hosts.yml").write_text(
            "all:\n"
            "  children:\n"
            "    web:\n"
            "      hosts:\n"
            "        web-01:\n"
            "          ansible_host: 10.0.10.11\n"
            "    db:\n"
            "      hosts:\n"
            "        db-01:\n"
            "          ansible_host: 10.0.10.12\n"
        )
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/acme.yml").write_text(
            "- name: fixture\n"
            f"  hosts: {play_hosts}\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: one task\n"
            "      ansible.builtin.debug:\n"
            "        msg: hi\n"
            "      tags: [acme_certs]\n"
        )

    def test_a_limit_that_intersects_the_play_to_nothing_fails(self, tmp_path):
        require_tool(
            "ansible-playbook",
            "deploy-preflight --limit intersection",
            "Install ansible in the deploy-preflight job image.",
        )
        self._tree(tmp_path)
        failures = preflight.check(self._pipeline("db"), tmp_path)
        assert any("matches NO host in any play" in f for f in failures), failures

    def test_a_limit_inside_the_play_passes(self, tmp_path):
        require_tool(
            "ansible-playbook",
            "deploy-preflight --limit intersection",
            "Install ansible in the deploy-preflight job image.",
        )
        self._tree(tmp_path)
        assert preflight.check(self._pipeline("web-01"), tmp_path) == []

    def test_a_limitless_job_whose_play_resolves_no_host_fails(self, tmp_path):
        """With no --limit the play's own `hosts:` pattern is the only scope, so
        a renamed or emptied group makes the step a no-op the job still passes."""
        require_tool(
            "ansible-playbook",
            "deploy-preflight limitless host resolution",
            "Install ansible in the deploy-preflight job image.",
        )
        self._tree(tmp_path, play_hosts="storage")
        failures = preflight.check(self._pipeline(None), tmp_path)
        assert any("its own hosts: patterns" in f for f in failures), failures

    def test_a_limitless_job_whose_play_resolves_hosts_passes(self, tmp_path):
        require_tool(
            "ansible-playbook",
            "deploy-preflight limitless host resolution",
            "Install ansible in the deploy-preflight job image.",
        )
        self._tree(tmp_path)
        assert preflight.check(self._pipeline(None), tmp_path) == []

    def test_a_banner_free_list_hosts_is_reported_rather_than_skipped(
        self, tmp_path, monkeypatch
    ):
        """No `hosts (N):` match means the gate read nothing, not that the limit
        is fine; a silent pass here is the hole this arm closes."""
        TestTagSelectionWithAStubbedAnsible._stub(
            tmp_path, monkeypatch, "acme_certs", hosts="none"
        )
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/acme.yml").write_text("[]\n")
        failures = preflight.check(self._pipeline("web"), tmp_path)
        assert any("printed no `hosts (N):` line" in f for f in failures), failures

    def test_an_all_zero_host_count_is_reported(self, tmp_path, monkeypatch):
        TestTagSelectionWithAStubbedAnsible._stub(
            tmp_path, monkeypatch, "acme_certs", hosts="0"
        )
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/acme.yml").write_text("[]\n")
        failures = preflight.check(self._pipeline("web"), tmp_path)
        assert any("matches NO host in any play" in f for f in failures), failures


class TestReferencedScriptBlocks:
    """A `!reference` script block must reach the parser, not read as empty."""

    PIPELINE = (
        ".acme-script:\n"
        "  script:\n"
        "    - ansible-playbook playbooks/acme.yml --tags acme_certs\n"
        "deploy-acme:\n"
        "  extends: .deploy-base\n"
        "  script:\n"
        "    - !reference [.acme-script, script]\n"
    )

    def test_a_referenced_invocation_is_inspected(self, tmp_path, monkeypatch):
        TestTagSelectionWithAStubbedAnsible._stub(tmp_path, monkeypatch, "acme_certs")
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks/acme.yml").write_text("[]\n")
        pipeline = parse_ci(self.PIPELINE)
        assert preflight.check(pipeline, tmp_path) == []

    def test_a_reference_into_an_included_file_is_reported(self, tmp_path):
        pipeline = parse_ci(
            self.PIPELINE.replace(".acme-script, script", ".included, script")
        )
        failures = preflight.check(pipeline, tmp_path)
        assert any("points outside" in f for f in failures), failures


def test_the_repo_pipeline_still_resolves_deploy_jobs():
    """The gate's own subject: if this drops to zero, the parser stopped seeing
    this repo's deploy jobs and every check above became vacuous in CI."""
    pipeline = load_ci(SCRIPTS.parent / ".gitlab-ci.yml")
    jobs = preflight.deploy_jobs(pipeline)
    assert len(jobs) >= 10, sorted(jobs)


class TestArgvSeams:
    """--pipeline and --ansible-dir are the only way a caller retargets the gate."""

    PIPELINE = (
        "deploy-acme:\n"
        "  extends: .deploy-base\n"
        "  script: [ansible-playbook playbooks/acme.yml --tags acme_certs]\n"
    )

    def _tree(self, tmp_path: Path, with_playbook: bool) -> tuple[Path, Path]:
        pipeline = tmp_path / "pipeline.yml"
        pipeline.write_text(self.PIPELINE)
        ansible = tmp_path / "ansible"
        (ansible / "playbooks").mkdir(parents=True)
        if with_playbook:
            (ansible / "playbooks/acme.yml").write_text("[]\n")
        return pipeline, ansible

    def test_a_playbook_missing_under_the_given_root_fails(self, tmp_path):
        pipeline, ansible = self._tree(tmp_path, with_playbook=False)
        rc = preflight.main([
            "deploy-preflight.py", "--pipeline", str(pipeline), "--ansible-dir", str(ansible),
        ])
        assert rc == 1

    def test_a_resolvable_playbook_and_tag_passes(self, tmp_path, monkeypatch):
        TestTagSelectionWithAStubbedAnsible._stub(tmp_path, monkeypatch, "acme_certs")
        pipeline, ansible = self._tree(tmp_path, with_playbook=True)
        rc = preflight.main([
            "deploy-preflight.py", "--pipeline", str(pipeline), "--ansible-dir", str(ansible),
        ])
        assert rc == 0


def test_main_reports_an_unreadable_pipeline_as_an_operator_error(tmp_path):
    rc = preflight.main(
        ["deploy-preflight.py", "--pipeline", str(tmp_path / "nope.yml")]
    )
    assert rc == 2


def test_main_reports_a_missing_ansible_playbook_as_an_operator_error(
    tmp_path, monkeypatch, capsys
):
    """A runner image without ansible must not read as a silent-no-op finding."""
    pipeline = tmp_path / "ci.yml"
    pipeline.write_text(
        "deploy-acme:\n"
        "  extends: .deploy-base\n"
        "  script: [ansible-playbook playbooks/acme.yml --tags acme_certs]\n"
    )
    (tmp_path / "playbooks").mkdir()
    (tmp_path / "playbooks/acme.yml").write_text("[]\n")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    rc = preflight.main([
        "deploy-preflight.py", "--pipeline", str(pipeline),
        "--ansible-dir", str(tmp_path),
    ])
    assert rc == 2, "a missing ansible-playbook must be an operator error, not a finding"
    assert "not on PATH" in capsys.readouterr().err


def test_only_the_selection_flag_is_checked():
    """--skip-tags is an exclusion: it selects nothing, so it is not a subject."""
    (call,) = preflight.parse_invocations(
        "ansible-playbook -i inventories/prod playbooks/site.yml --tags a --skip-tags b"
    )
    assert call["tags"] == {"a"}


def test_a_skip_tags_only_invocation_selects_nothing_to_check():
    (call,) = preflight.parse_invocations(
        "ansible-playbook -i inventories/prod playbooks/site.yml --skip-tags b"
    )
    assert not call["tags"]
