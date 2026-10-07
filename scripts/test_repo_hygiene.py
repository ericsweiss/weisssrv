"""Repo-level invariants that only a tracked-file check can see.

.gitignore stops a file being added by accident; it says nothing about a file
already tracked. These assert the end state instead of the rule.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The allowlist carries no rules of its own, so gitleaks detects nothing at all
# without this key. Its absence greens every secret scan with nothing red.
GITLEAKS = ".gitleaks.toml"

# Saved terraform plans resolve every variable, so a plan of terraform/authentik
# holds OIDC client secrets in msgpack that gitleaks cannot pattern-match.
PLAN_GLOBS = ["tfplan", "tfplan.json", "*.tfplan", "*.tfplan.json", "plan.out"]

# The one .gitignore the probes resolve against, named so the python-tests
# `changes:` parity gate can see this file's subject.
GITIGNORE = ".gitignore"


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


def test_bare_plan_names_are_ignored_at_every_level():
    """`-out=tfplan` writes a name `*.tfplan` does not match — the gap this
    invariant exists to close. The probes cover the repo root and module depth,
    since a plan is written from either."""
    assert (REPO / GITIGNORE).is_file(), f"{GITIGNORE} is missing"
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


def test_local_tool_artifacts_are_ignored():
    """`task scripts:test` and a local `pytest --cov` drop caches and a
    .coverage database in the tree; untracked noise there hides a real change
    in `git status` during a pre-MR check."""
    for probe in (
        ".coverage",
        ".coverage.abc123",
        "coverage.xml",
        "htmlcov/index.html",
        ".ruff_cache/CACHEDIR.TAG",
        ".pytest_cache/CACHEDIR.TAG",
    ):
        run = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", probe],
            capture_output=True,
        )
        assert run.returncode == 0, f"{probe} is not ignored by any .gitignore"


def _extends_default(text: str) -> bool:
    """Whether a gitleaks config loads the upstream rule set."""
    return (tomllib.loads(text).get("extend") or {}).get("useDefault") is True


def test_the_gitleaks_config_loads_the_default_rules():
    assert _extends_default((REPO / GITLEAKS).read_text(encoding="utf-8")), (
        f"{GITLEAKS} has no `[extend] useDefault = true`, so gitleaks loads only "
        "the rules in that file — it defines none, so every secret scan passes "
        "while detecting nothing"
    )


def test_a_config_without_the_key_is_reported():
    """Mutation case: the edit that disarms secret detection silently."""
    assert not _extends_default('title = "x"\n\n[[allowlists]]\npaths = ["y"]\n')
    assert not _extends_default('[extend]\nuseDefault = false\n')


# CRITICAL: the manifest gates (PSA labels, staged kustomization coverage,
# default-deny coverage, netpol parity) walk `*.yaml` only, while Kustomize
# applies whatever `resources:` names. A `.yml` manifest reaches the cluster
# ungated, so the convention is one suffix under kubernetes/.
K8S_TREE = "kubernetes"


def yml_manifests(root: Path) -> list[str]:
    tree = root / K8S_TREE
    return sorted(
        path.relative_to(root).as_posix() for path in tree.rglob("*.yml")
    )


def test_no_manifest_under_kubernetes_uses_the_yml_suffix():
    assert (REPO / K8S_TREE).is_dir(), f"{K8S_TREE}/ is missing — this gate walked nothing"
    offenders = yml_manifests(REPO)
    assert not offenders, (
        "manifests under kubernetes/ with the .yml suffix; the gates in scripts/ "
        "read .yaml only, so these are applied by Flux but checked by nothing:\n  "
        + "\n  ".join(offenders)
    )


def test_a_yml_manifest_is_reported(tmp_path: Path):
    (tmp_path / K8S_TREE / "apps/demo").mkdir(parents=True)
    (tmp_path / K8S_TREE / "apps/demo/release.yaml").write_text("kind: HelmRelease\n")
    assert yml_manifests(tmp_path) == []
    (tmp_path / K8S_TREE / "apps/demo/netpol.yml").write_text("kind: NetworkPolicy\n")
    assert yml_manifests(tmp_path) == ["kubernetes/apps/demo/netpol.yml"]
