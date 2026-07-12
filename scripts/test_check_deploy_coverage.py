#!/usr/bin/env python3
"""
Unit tests for check-deploy-coverage.sh.

check-deploy-coverage.sh is the MR gate that fails when an Ansible
role/playbook/inventory file is changed but no deploy-* CI job will pick the
change up. It had zero coverage. These tests pin the contract by driving the
script via subprocess inside a throwaway git repo (the script reads
.gitlab-ci.yml from CWD and diffs against a base ref), exercising the four
behaviors the gate hinges on:

  (a) a role mapped to a deploy-* job's `changes:` list passes
  (b) a changed role mapped to NO deploy job fails (nonzero exit)
  (c) a role on INTENTIONALLY_UNMAPPED_ROLES is honored (passes despite no
      deploy mapping) — verified by editing the script's allowlist in the
      test repo
  (d) a DELETED role is not flagged (the `--diff-filter=d` exclusion)

The script's deploy-path extraction parses .gitlab-ci.yml as YAML and only
credits jobs whose name starts with "deploy-" AND whose stage is "deploy", so
the fixture .gitlab-ci.yml below mirrors that shape minimally.

Run with pytest:
    pytest scripts/test_check_deploy_coverage.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-deploy-coverage.sh"

# A deploy-* job (stage: deploy) that maps ansible/roles/base/**/* — the only
# CI-mapped role in the fixture. Everything else is "unmapped" unless it's on
# the INTENTIONALLY_UNMAPPED_ROLES allowlist baked into the script.
FIXTURE_CI = textwrap.dedent(
    """\
    stages:
      - lint
      - deploy

    # A lint-stage job that ALSO mentions a role path — the script must NOT
    # credit this as deploy coverage (it filters on stage: deploy).
    deploy-coverage-check:
      stage: lint
      script:
        - bash scripts/check-deploy-coverage.sh
      rules:
        - changes:
            - ansible/roles/widget/**/*

    deploy-ansible-base:
      stage: deploy
      script:
        - echo deploy
      rules:
        - if: '$CI_COMMIT_BRANCH == "main"'
          changes:
            - ansible/roles/base/**/*
    """
)


def _run(cmd, cwd, **kw):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, **kw
    )


def _git(args, cwd):
    env = dict(os.environ)
    # Deterministic, no-config-dependent commits.
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
    )
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env
    )
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res


def _write(repo: Path, rel: str, content: str = "x\n"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with the fixture .gitlab-ci.yml and a copy of the script,
    committed as the base. Tests add a feature commit and diff against base."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q", "-b", "main"], r)
    # Copy the real script under test into scripts/ so the path the fixture CI
    # references and the script's own self-reference both resolve.
    (r / "scripts").mkdir()
    shutil.copy(SCRIPT, r / "scripts" / "check-deploy-coverage.sh")
    (r / ".gitlab-ci.yml").write_text(FIXTURE_CI)
    # Seed a role dir so the base commit has the tree the tests mutate.
    _write(r, "ansible/roles/base/tasks/main.yml")
    _git(["add", "-A"], r)
    _git(["commit", "-q", "-m", "base"], r)
    return r


def _base_sha(repo: Path) -> str:
    return _git(["rev-parse", "HEAD"], repo).stdout.strip()


def _run_check(repo: Path, base_sha: str):
    """Invoke the script with an explicit positional BASE_REF, env scrubbed of
    the CI_* overrides so $1 is the diff base."""
    env = dict(os.environ)
    for k in (
        "CI_MERGE_REQUEST_DIFF_BASE_SHA",
        "CI_COMMIT_BEFORE_SHA",
    ):
        env.pop(k, None)
    return subprocess.run(
        ["bash", "scripts/check-deploy-coverage.sh", base_sha],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_mapped_role_passes(repo: Path):
    """(a) A change to ansible/roles/base/ (mapped to deploy-ansible-base)
    passes."""
    base = _base_sha(repo)
    _write(repo, "ansible/roles/base/tasks/main.yml", "changed\n")
    _git(["commit", "-q", "-am", "edit base"], repo)
    res = _run_check(repo, base)
    assert res.returncode == 0, f"expected pass, got: {res.stdout}\n{res.stderr}"
    assert "covered by at least one deploy-* job" in res.stdout


def test_unmapped_role_fails(repo: Path):
    """(b) A change to a role mapped to no deploy job fails nonzero and names
    the role."""
    base = _base_sha(repo)
    _write(repo, "ansible/roles/widget/tasks/main.yml", "new role\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add widget"], repo)
    res = _run_check(repo, base)
    assert res.returncode == 1, f"expected failure, got rc={res.returncode}"
    # widget is mentioned in the LINT job's rules but not a deploy job — the
    # stage-scoped parse must NOT credit that as coverage.
    assert "not mapped to any CI deploy job" in res.stderr
    assert "ansible/roles/widget/" in res.stderr


def test_intentionally_unmapped_role_honored(repo: Path):
    """(c) A role listed in INTENTIONALLY_UNMAPPED_ROLES passes even with no
    deploy mapping. We add 'widget' to the script's allowlist in the repo copy
    to prove the allowlist path is honored without depending on the real
    repo's specific entries."""
    # Inject 'widget' into the allowlist array in the script copy.
    script = repo / "scripts" / "check-deploy-coverage.sh"
    text = script.read_text()
    assert "INTENTIONALLY_UNMAPPED_ROLES=(" in text
    text = text.replace(
        "INTENTIONALLY_UNMAPPED_ROLES=(\n",
        "INTENTIONALLY_UNMAPPED_ROLES=(\n    widget\n",
        1,
    )
    script.write_text(text)
    _git(["commit", "-q", "-am", "allowlist widget"], repo)

    base = _base_sha(repo)
    _write(repo, "ansible/roles/widget/tasks/main.yml", "new role\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add widget"], repo)
    res = _run_check(repo, base)
    assert res.returncode == 0, (
        f"allowlisted role should pass, got rc={res.returncode}: "
        f"{res.stdout}\n{res.stderr}"
    )


def test_deleted_role_not_flagged(repo: Path):
    """(d) Deleting an unmapped role must NOT be flagged — --diff-filter=d
    excludes deletions (nothing left to roll out)."""
    # First add an unmapped role and commit it as part of the base so the
    # deletion is a pure removal in the measured diff.
    _write(repo, "ansible/roles/widget/tasks/main.yml", "doomed\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add widget"], repo)
    base = _base_sha(repo)

    # Now delete the whole role and diff base...HEAD: the only change is a
    # deletion, which the gate must ignore.
    shutil.rmtree(repo / "ansible/roles/widget")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "remove widget"], repo)
    res = _run_check(repo, base)
    assert res.returncode == 0, (
        f"deleted role must not be flagged, got rc={res.returncode}: "
        f"{res.stdout}\n{res.stderr}"
    )
    assert "widget" not in res.stderr


def test_changes_paths_dict_form_credited(repo: Path):
    """GitLab's `changes: {paths: [...]}` mapping form must confer the same
    coverage credit as the plain list form."""
    ci = (repo / ".gitlab-ci.yml").read_text() + textwrap.dedent(
        """\

        deploy-ansible-gadget:
          stage: deploy
          script:
            - echo deploy
          rules:
            - if: '$CI_COMMIT_BRANCH == "main"'
              changes:
                paths:
                  - ansible/roles/gadget/**/*
        """
    )
    (repo / ".gitlab-ci.yml").write_text(ci)
    _git(["commit", "-q", "-am", "map gadget via dict changes"], repo)

    base = _base_sha(repo)
    _write(repo, "ansible/roles/gadget/tasks/main.yml", "new role\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add gadget"], repo)
    res = _run_check(repo, base)
    assert res.returncode == 0, (
        f"dict-form changes: should credit coverage, got rc={res.returncode}: "
        f"{res.stdout}\n{res.stderr}"
    )


def test_deleted_plus_added_unmapped_role_still_flags_addition(repo: Path):
    """Renames/replacements surface via their ADDED path: deleting widget while
    adding a different unmapped role (gadget) must still flag gadget — proves
    --diff-filter=d only suppresses the deletion side, not real new coverage
    obligations."""
    _write(repo, "ansible/roles/widget/tasks/main.yml", "doomed\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add widget"], repo)
    base = _base_sha(repo)

    shutil.rmtree(repo / "ansible/roles/widget")
    _write(repo, "ansible/roles/gadget/tasks/main.yml", "new unmapped\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "swap widget for gadget"], repo)
    res = _run_check(repo, base)
    assert res.returncode == 1
    assert "ansible/roles/gadget/" in res.stderr
    assert "ansible/roles/widget/" not in res.stderr


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
