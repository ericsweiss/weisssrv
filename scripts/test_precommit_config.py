"""The pre-commit hook set must stay in step with what it guards.

Covers the mutating whitespace hooks skipping every vendored copy, each local
gate resolving on a deletion-only commit with a live `files:` regex, and the pin.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from test_vendored_byte_identity import load_ci_doc

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / ".pre-commit-config.yaml"
MANIFEST = REPO / "scripts" / "vendored-manifest.yml"

# end-of-file-fixer and trailing-whitespace rewrite the files they match.
MUTATING_HOOKS = ("end-of-file-fixer", "trailing-whitespace")

# Not part of the committed tree, so a `files:` branch must not be proven by one.
PRUNED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".tmp", ".task"}


@pytest.fixture(scope="module")
def hooks() -> dict[str, dict]:
    doc = yaml.safe_load(CONFIG.read_text())
    found = {h["id"]: h for repo in doc["repos"] for h in repo["hooks"]}
    assert found, f"{CONFIG} declares no hooks"
    return found


@pytest.fixture(scope="module")
def local_hooks() -> list[dict]:
    doc = yaml.safe_load(CONFIG.read_text())
    local = [h for repo in doc["repos"] if repo["repo"] == "local" for h in repo["hooks"]]
    assert local, f"{CONFIG} declares no `repo: local` hooks"
    return local


def _vendored_paths() -> list[str]:
    doc = yaml.safe_load(MANIFEST.read_text())
    paths = [
        entry if isinstance(entry, str) else (entry.get("consumer") or entry["lib"])
        for entry in doc["vendored"]
    ]
    assert paths, f"{MANIFEST} lists no vendored copies"
    return paths


def _unexcluded(pattern: str, paths: list[str]) -> list[str]:
    """Paths the exclude regex would still let a mutating hook rewrite."""
    compiled = re.compile(pattern)
    return [p for p in paths if not compiled.search(p)]


@pytest.mark.parametrize("hook_id", MUTATING_HOOKS)
def test_vendored_copies_are_excluded(hooks: dict[str, dict], hook_id: str) -> None:
    pattern = hooks[hook_id].get("exclude")
    assert pattern, f"{hook_id} has no `exclude:` — it would rewrite the vendored copies"
    left = _unexcluded(pattern, _vendored_paths())
    assert not left, (
        f"{hook_id} still rewrites vendored copies {left} — a whitespace fix there reds "
        "scripts/test_vendored_byte_identity.py; add them to the hook's `exclude:`"
    )


def test_unlisted_path_is_reported() -> None:
    """The mutation case: a new vendored copy absent from the regex fails."""
    pattern = yaml.safe_load(CONFIG.read_text())
    pattern = [
        h for repo in pattern["repos"] for h in repo["hooks"] if h["id"] == MUTATING_HOOKS[0]
    ][0]["exclude"]
    assert _unexcluded(pattern, ["scripts/check-not-yet-vendored.py"]) == [
        "scripts/check-not-yet-vendored.py"
    ]


def test_local_files_stay_whitespace_checked(hooks: dict[str, dict]) -> None:
    """The exclude is the manifest, not a blanket scripts/ opt-out."""
    pattern = re.compile(hooks[MUTATING_HOOKS[0]]["exclude"])
    vendored = set(_vendored_paths())
    overreach = [
        str(p.relative_to(REPO))
        for p in sorted((REPO / "scripts").iterdir())
        if p.is_file()
        and str(p.relative_to(REPO)) not in vendored
        and pattern.search(str(p.relative_to(REPO)))
    ]
    assert not overreach, f"the exclude also skips this repo's own files: {overreach}"


def test_local_hook_entries_resolve(local_hooks: list[dict]) -> None:
    for hook in local_hooks:
        script = next(
            (word for word in hook["entry"].split() if word.startswith("scripts/")), None
        )
        assert script, f"{hook['id']}: entry {hook['entry']!r} names no scripts/ gate"
        assert (REPO / script).is_file(), f"{hook['id']}: {script} does not exist"


def test_local_hooks_survive_a_deletion_only_commit(local_hooks: list[dict]) -> None:
    for hook in local_hooks:
        assert hook.get("pass_filenames") is False, f"{hook['id']}: gates run on the whole tree"
        assert hook.get("always_run") is True, (
            f"{hook['id']}: pre-commit's staged-file list leaves deletions out, so a "
            "`git rm`-only commit skips this gate without `always_run: true`"
        )


@pytest.mark.parametrize(
    ("distribution", "variable"), [("pyyaml", "PYYAML_VERSION"), ("pytest", "PYTEST_VERSION")]
)
def test_hook_dependency_pins_match_ci(
    local_hooks: list[dict], distribution: str, variable: str
) -> None:
    ci = load_ci_doc(REPO / ".gitlab-ci.yml")
    expected = (ci.get("variables") or {}).get(variable)
    assert expected, f".gitlab-ci.yml variables.{variable} is the single source of the pin"
    pinned = {
        dep
        for hook in local_hooks
        for dep in hook.get("additional_dependencies", [])
        if dep.startswith(distribution)
    }
    assert pinned == {f"{distribution}=={expected}"}, (
        f"pre-commit hook {distribution} pins {sorted(pinned)} drifted from "
        f"variables.{variable} ({expected})"
    )


def repo_paths() -> list[str]:
    """Repo-relative paths of the committed tree, as pre-commit spells them."""
    found = []
    for directory, subdirs, files in os.walk(REPO):
        subdirs[:] = [d for d in subdirs if d not in PRUNED_DIRS]
        base = Path(directory).relative_to(REPO)
        found += [(base / name).as_posix() for name in files]
    assert len(found) > 100, f"the walk of {REPO} found only {len(found)} files"
    return found


def files_branches(pattern: str) -> list[str]:
    """Each top-level alternative of a `files:` regex.

    One dead branch is the whole failure: a Taskfile reorganisation leaves
    `^(Taskfile\\.yml|taskfiles/...)` still matching, with half of it inert.
    """
    match = re.fullmatch(r"\^\((?P<body>[^()]*)\)(?P<tail>.*)", pattern)
    if not match:
        return [pattern]
    return [f"^{alt}{match.group('tail')}" for alt in match.group("body").split("|")]


def dead_branches(hooks: list[dict], paths: list[str]) -> list[str]:
    """`hook: branch` pairs whose regex matches nothing in the tree."""
    dead = []
    for hook in hooks:
        pattern = hook.get("files")
        if not pattern:
            continue
        for branch in files_branches(pattern):
            compiled = re.compile(branch)
            if not any(compiled.search(path) for path in paths):
                dead.append(f"{hook['id']}: {branch}")
    return dead


def test_every_files_branch_still_matches_a_path(local_hooks: list[dict]) -> None:
    dead = dead_branches(local_hooks, repo_paths())
    assert not dead, (
        f"these `files:` branches match nothing in the tree: {dead} — a moved "
        "target leaves the branch inert, so the hook stops being retriggered by "
        "the files it guards (`always_run: true` is what still runs it)"
    )


def test_a_renamed_target_leaves_a_dead_branch() -> None:
    """Mutation case: the live tree is clean, so the arm is proven on fixtures."""
    hooks = [{"id": "check-taskfile", "files": r"^(Taskfile\.yml|taskfiles/.*\.ya?ml)$"}]
    assert dead_branches(hooks, ["Taskfile.yml", "taskfiles/lint.yml"]) == []
    assert dead_branches(hooks, ["Taskfile.yml"]) == [
        "check-taskfile: ^taskfiles/.*\\.ya?ml$"
    ]


def test_a_single_branch_regex_is_checked_whole() -> None:
    hooks = [{"id": "check-doc-links", "files": r"\.md$"}]
    assert dead_branches(hooks, ["docs/01-overview.md"]) == []
    assert dead_branches(hooks, ["docs/01-overview.rst"]) == ["check-doc-links: \\.md$"]
