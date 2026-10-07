"""The shared parser reads every `ansible-playbook` call on a CI script line.

Both deploy gates walk the same argv, so a second call chained onto one line
must not vanish from either of them.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from script_loader import load_path

SCRIPTS = Path(__file__).resolve().parent
COVERAGE = SCRIPTS / "check-deploy-host-coverage.py"

CHAINED = (
    "bash -c 'set -e; op run -- ansible-playbook -i inventories/prod "
    "playbooks/k3s-provision-vms.yml --tags proxmox_vm; op run -- "
    "ansible-playbook -i inventories/prod playbooks/k3s.yml --tags k3s'"
)


def _load():
    return load_path(SCRIPTS / "ci_playbook_invocations.py", register=True)


parser = _load()


class TestParseInvocations:
    def test_two_calls_on_one_line_are_both_returned(self):
        calls = parser.parse_invocations(CHAINED)
        assert [c["playbook"] for c in calls] == [
            "playbooks/k3s-provision-vms.yml",
            "playbooks/k3s.yml",
        ]
        assert [c["tags"] for c in calls] == [{"proxmox_vm"}, {"k3s"}]

    def test_flag_values_are_never_read_as_the_playbook(self):
        (call,) = parser.parse_invocations(
            'ansible-playbook -i inventories/prod -e "ansible_become=false" '
            "playbooks/home-assistant.yml"
        )
        assert call["playbook"] == "playbooks/home-assistant.yml"
        assert call["inventory"] == "inventories/prod"

    def test_limit_and_tags_are_read_in_both_spellings(self):
        (spaced,) = parser.parse_invocations(
            "ansible-playbook playbooks/site.yml --limit dns --tags acme_certs,qol"
        )
        (joined,) = parser.parse_invocations(
            "ansible-playbook playbooks/site.yml --limit=dns --tags=acme_certs,qol"
        )
        assert spaced["limit"] == joined["limit"] == "dns"
        assert spaced["tags"] == joined["tags"] == {"acme_certs", "qol"}

    def test_an_attached_short_flag_value_is_read(self):
        """Mutation case: `-ipath` fell through to the playbook check, leaving
        inventory None, and deploy-preflight ran --list-tasks without one."""
        (call,) = parser.parse_invocations(
            "ansible-playbook -iansible/inventories/prod playbooks/site.yml "
            "-ldns-01 -tqol -eansible_become=false"
        )
        assert call["playbook"] == "playbooks/site.yml"
        assert call["inventory"] == "ansible/inventories/prod"
        assert call["limit"] == "dns-01"
        assert call["tags"] == {"qol"}

    def test_a_call_with_no_playbook_is_dropped(self):
        assert parser.parse_invocations("ansible-playbook $PLAYBOOK_FROM_A_VARIABLE") == []

    def test_flags_past_a_line_continuation_survive(self):
        """Mutation case: cutting the argv at the newline lost --limit/--tags, and
        the call still parsed, so both gates scored it as fleet-wide."""
        (call,) = parser.parse_invocations(
            "ansible-playbook -i inventories/prod playbooks/site.yml \\\n"
            "  --limit proxmox --tags qol"
        )
        assert call["limit"] == "proxmox"
        assert call["tags"] == {"qol"}

    def test_skip_tags_is_captured_separately_from_tags(self):
        (call,) = parser.parse_invocations(
            "ansible-playbook playbooks/site.yml --skip-tags proxmox_firewall,qol"
        )
        assert call["tags"] is None
        assert call["skip_tags"] == {"proxmox_firewall", "qol"}


HOSTS_YML = textwrap.dedent(
    """\
    all:
      children:
        proxmox:
          hosts:
            pve-a:
        dns:
          hosts:
            dns-01:
    """
)

FIRST_YML = textwrap.dedent(
    """\
    - name: First
      hosts: proxmox
      roles:
        - role: first_role
          tags: [first_role]
    """
)

SECOND_YML = textwrap.dedent(
    """\
    - name: Second
      hosts: dns
      roles:
        - role: second_role
          tags: [second_role]
    """
)


def build_repo(tmp_path: Path, second_tags: str, second_extra: str = "") -> Path:
    """A repo whose one deploy job chains two playbooks onto a single line."""
    (tmp_path / "ansible/inventories/prod").mkdir(parents=True)
    (tmp_path / "ansible/playbooks").mkdir(parents=True)
    (tmp_path / "ansible/inventories/prod/hosts.yml").write_text(HOSTS_YML)
    (tmp_path / "ansible/playbooks/first.yml").write_text(FIRST_YML)
    (tmp_path / "ansible/playbooks/second.yml").write_text(SECOND_YML)
    (tmp_path / ".gitlab-ci.yml").write_text(
        textwrap.dedent(
            f"""\
            stages:
              - deploy

            deploy-chain:
              stage: deploy
              script:
                - >-
                  bash -c 'set -e;
                  op run -- ansible-playbook -i inventories/prod playbooks/first.yml --tags first_role;
                  op run -- ansible-playbook -i inventories/prod playbooks/second.yml --tags {second_tags} {second_extra}'
              rules:
                - if: '$CI_COMMIT_BRANCH == "main"'
            """
        )
    )
    return tmp_path


def run_coverage(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COVERAGE), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_second_chained_playbook_is_covered(tmp_path):
    result = run_coverage(build_repo(tmp_path, "second_role"))
    assert result.returncode == 0, result.stderr
    assert "All 2 roles" in result.stdout


def test_the_second_chained_playbook_reports_its_gap(tmp_path):
    """Mutation case: reading one call per line hid second.yml entirely, so its
    unreached role read as full coverage."""
    result = run_coverage(build_repo(tmp_path, "something_else"))
    assert result.returncode == 1
    assert "second_role — unreached hosts: dns-01" in result.stderr


def test_a_skip_tags_job_is_refused_rather_than_scored(tmp_path):
    """Mutation case: --skip-tags read as no tag filter, so every role in the
    play counted as covered."""
    result = run_coverage(build_repo(tmp_path, "second_role", "--skip-tags second_role"))
    assert result.returncode == 2
    assert "--skip-tags second_role" in result.stderr
