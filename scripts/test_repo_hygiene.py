"""Repo-level invariants that only a tracked-file check can see.

.gitignore stops a file being ADDED by accident; it says nothing about a file
already tracked (git ignores ignore-rules for tracked paths). These assert the
end state instead of the rule.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Saved terraform plans. A plan resolves every variable, so a plan of
# terraform/authentik is a binary blob holding the eight OIDC client secrets and
# the injected basic-auth passwords — and gitleaks cannot pattern-match msgpack,
# so committing one is a silent leak into a repo mirrored to GitHub.
PLAN_GLOBS = ["tfplan", "tfplan.json", "*.tfplan", "*.tfplan.json", "plan.out"]


def _tracked() -> list[str]:
    run = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True
    )
    return run.stdout.splitlines()


def test_no_terraform_plan_file_is_tracked():
    import fnmatch

    offenders = sorted(
        path
        for path in _tracked()
        if any(fnmatch.fnmatch(Path(path).name, pattern) for pattern in PLAN_GLOBS)
    )
    assert not offenders, (
        f"terraform plan files are tracked: {offenders}. A plan holds every "
        "resolved variable in the clear; remove it from the index and rotate any "
        "credential it contained."
    )


def test_both_gitignores_cover_the_bare_plan_name():
    """`-out=tfplan` writes a name `*.tfplan` does not match — the gap this
    invariant exists to close. Checked at both levels, since a plan is written
    from either the repo root or a module directory."""
    for probe in (
        "tfplan",
        "tfplan.json",
        "terraform/authentik/tfplan",
        "terraform/cloudflare/plan.out",
    ):
        run = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", probe],
            capture_output=True,
        )
        assert run.returncode == 0, f"{probe} is not ignored by any .gitignore"
