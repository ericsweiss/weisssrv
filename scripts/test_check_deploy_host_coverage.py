"""Failure-path tests for check-deploy-host-coverage.py.

A role a CI deploy job runs must reach every host its playbook declares it for.
Fixtures are throwaway repos driven through --repo.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-deploy-host-coverage.py"

HOSTS_YML = textwrap.dedent(
    """\
    all:
      children:
        proxmox:
          hosts:
            pve-a:
            pve-b:
        dns:
          hosts:
            dns-01:
        # References groups defined above with an empty body — the shape
        # base_managed uses in the real inventory.
        base_managed:
          children:
            proxmox:
            dns:
    """
)

SITE_YML = textwrap.dedent(
    """\
    - name: Base
      hosts: base_managed
      roles:
        - role: base
          tags: [base]

    - name: Proxmox hosts
      hosts: proxmox
      roles:
        - role: nfs_tls
          tags: [nfs_tls]
    """
)


def ci_yml(tags: str, changes: list[str]) -> str:
    changes_block = "\n".join(f"            - {c}" for c in changes)
    return textwrap.dedent(
        f"""\
        stages:
          - lint
          - deploy

        # Wrong stage: must not be credited.
        deploy-coverage-check:
          stage: lint
          script:
            - ansible-playbook playbooks/site.yml
          rules:
            - changes:
                - ansible/roles/nfs_tls/**/*

        deploy-ansible-proxmox:
          stage: deploy
          script:
            - op run -- ansible-playbook -i inventories/prod playbooks/site.yml --limit proxmox --tags {tags}
          rules:
            - if: '$CI_COMMIT_BRANCH == "main"'
              changes:
        {changes_block}

        deploy-ansible-base:
          stage: deploy
          script:
            - op run -- ansible-playbook -i inventories/prod playbooks/site.yml --tags base
          rules:
            - if: '$CI_COMMIT_BRANCH == "main"'
              changes:
                - ansible/roles/base/**/*
        """
    )


def build_repo(tmp_path: Path, ci: str, site: str = SITE_YML, hosts: str = HOSTS_YML) -> Path:
    (tmp_path / "ansible/inventories/prod").mkdir(parents=True)
    (tmp_path / "ansible/playbooks").mkdir(parents=True)
    (tmp_path / "ansible/inventories/prod/hosts.yml").write_text(hosts)
    (tmp_path / "ansible/playbooks/site.yml").write_text(site)
    (tmp_path / ".gitlab-ci.yml").write_text(ci)
    return tmp_path


def run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fully_covered_passes(tmp_path):
    repo = build_repo(tmp_path, ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]))
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "reach every host" in result.stdout


def test_role_missing_from_tags_fails(tmp_path):
    repo = build_repo(tmp_path, ci_yml("qol", ["ansible/roles/nfs_tls/**/*"]))
    result = run(repo)
    assert result.returncode == 1
    assert "nfs_tls — unreached hosts" in result.stderr
    assert "pve-a" in result.stderr and "pve-b" in result.stderr


def test_changes_list_no_longer_gates_coverage(tmp_path):
    """Roles ship from the collection, so a job that RUNS the role covers it
    whatever its `changes:` list says — that half is check-deploy-coverage.sh's
    view of ansible/requirements.yml now."""
    repo = build_repo(tmp_path, ci_yml("qol,nfs_tls", ["ansible/roles/qol/**/*"]))
    result = run(repo)
    assert result.returncode == 0, result.stderr


def test_limit_narrows_coverage(tmp_path):
    # `base` is declared on base_managed (pve-a, pve-b, dns-01) but the only job
    # selecting its tag here is --limit proxmox.
    ci = ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]).replace(
        "playbooks/site.yml --tags base", "playbooks/site.yml --limit proxmox --tags base"
    )
    repo = build_repo(tmp_path, ci)
    result = run(repo)
    assert result.returncode == 1
    assert "base — unreached hosts" in result.stderr
    assert "dns-01" in result.stderr


def test_transitive_group_reference_expands(tmp_path):
    # If base_managed's empty-bodied children resolved to nothing, `base` would
    # have an empty declared set and this gate would pass for the wrong reason.
    ci = ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]).replace(
        "playbooks/site.yml --tags base", "playbooks/site.yml --limit dns --tags base"
    )
    repo = build_repo(tmp_path, ci)
    result = run(repo)
    assert result.returncode == 1
    # dns-01 IS reached; the two proxmox hosts base_managed pulls in are not.
    assert "pve-a, pve-b" in result.stderr
    assert "dns-01" not in result.stderr.split("unreached hosts:")[1].split("\n")[0]


def test_unknown_host_pattern_is_a_hard_error(tmp_path):
    site = SITE_YML.replace("hosts: proxmox", "hosts: not_a_group")
    repo = build_repo(tmp_path, ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]), site=site)
    result = run(repo)
    assert result.returncode == 2
    assert "unknown group/host" in result.stderr


def test_ledger_exclusion_does_not_shrink_the_declared_set(tmp_path):
    # site.yml's deploy plays subtract the probe's ledger. If the gate honoured
    # that exclusion it would under-report `base`'s declared hosts; instead the
    # token is ignored and dns-01 still shows up as unreached.
    site = SITE_YML.replace("hosts: base_managed", "hosts: base_managed:!deploy_skipped")
    ci = ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]).replace(
        "playbooks/site.yml --tags base", "playbooks/site.yml --limit proxmox --tags base"
    )
    repo = build_repo(tmp_path, ci, site=site)
    result = run(repo)
    assert result.returncode == 1
    assert "base — unreached hosts" in result.stderr
    assert "dns-01" in result.stderr


def test_other_exclusions_are_still_a_hard_error(tmp_path):
    site = SITE_YML.replace("hosts: proxmox", "hosts: base_managed:!dns")
    repo = build_repo(tmp_path, ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]), site=site)
    result = run(repo)
    assert result.returncode == 2
    assert "exclusion/intersection" in result.stderr


def test_missing_playbook_is_a_hard_error(tmp_path):
    repo = build_repo(tmp_path, ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]))
    (repo / "ansible/playbooks/site.yml").unlink()
    result = run(repo)
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_an_acknowledged_gap_is_skipped_by_its_short_name(tmp_path):
    """Playbooks declare `weisssrv.infra.<role>`; ACKNOWLEDGED_GAPS is keyed short."""
    site = SITE_YML.replace("- role: nfs_tls", "- role: weisssrv.infra.proxmox_vm").replace(
        "tags: [nfs_tls]", "tags: [proxmox_vm]"
    )
    repo = build_repo(tmp_path, ci_yml("qol", ["ansible/requirements.yml"]), site=site)
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "(skipped proxmox_vm:" in result.stdout


def test_a_referenced_script_block_still_counts_as_coverage(tmp_path):
    """A `!reference [.anchor, script]` block must be expanded, not read as an
    empty script that silently drops the job's invocation."""
    invocation = (
        "op run -- ansible-playbook -i inventories/prod playbooks/site.yml "
        "--limit proxmox --tags qol,nfs_tls"
    )
    ci = ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"])
    referenced = ci.replace(
        f"    - {invocation}", "    - !reference [.proxmox-script, script]"
    ) + f".proxmox-script:\n  script:\n    - {invocation}\n"
    assert "!reference" in referenced and referenced.count(invocation) == 1
    result = run(build_repo(tmp_path, referenced))
    assert result.returncode == 0, result.stdout + result.stderr
