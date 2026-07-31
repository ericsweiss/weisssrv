#!/usr/bin/env python3
"""
Unit tests for check-deploy-host-coverage.py.

That gate fails when a role a CI-deployed playbook declares does not reach every
host the playbook declares it for — the gap that let nfs_tls go unmanaged on
five of six Proxmox hosts while the path-level deploy-coverage gate stayed
green. These tests drive it via subprocess against fixture repos built with
--repo, pinning the behaviours the gate hinges on:

  (a) a role whose deploy job selects its tag AND lists its path passes
  (b) a role dropped from the job's --tags fails, naming the unreached hosts
  (c) a role the job runs but does not list in `changes:` fails (it would never
      be triggered by an edit to itself)
  (d) --limit is honoured: a job limited to one group leaves the other group's
      hosts uncovered
  (e) transitive group references (`children:` naming a group defined elsewhere,
      with an empty body) expand to their real hosts — a resolver bug here would
      UNDER-report the declared set and silently pass
  (f) an unknown host pattern is a hard error (exit 2), never a quiet pass
  (g) `!deploy_skipped` (the runtime reachability ledger the deploy plays
      subtract) does NOT shrink the declared set — a host one run skipped still
      needs a deploy job covering it
  (h) any OTHER exclusion/intersection is still a hard error (exit 2), so
      teaching the gate one form did not make it permissive

Run with pytest:
    pytest scripts/test_check_deploy_host_coverage.py -v
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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
    assert "ansible/roles/nfs_tls/" in result.stderr
    assert "pve-a" in result.stderr and "pve-b" in result.stderr


def test_role_missing_from_changes_fails(tmp_path):
    repo = build_repo(tmp_path, ci_yml("qol,nfs_tls", ["ansible/roles/qol/**/*"]))
    result = run(repo)
    assert result.returncode == 1
    assert "does not list ansible/roles/nfs_tls/**/* in its changes:" in result.stderr


def test_limit_narrows_coverage(tmp_path):
    # `base` is declared on base_managed (pve-a, pve-b, dns-01) but the only job
    # selecting its tag here is --limit proxmox.
    ci = ci_yml("qol,nfs_tls", ["ansible/roles/nfs_tls/**/*"]).replace(
        "playbooks/site.yml --tags base", "playbooks/site.yml --limit proxmox --tags base"
    )
    repo = build_repo(tmp_path, ci)
    result = run(repo)
    assert result.returncode == 1
    assert "ansible/roles/base/" in result.stderr
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
    assert "ansible/roles/base/" in result.stderr
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
