"""The local `task lint` gates that mirror a CI include keep its floors.

A linter exits 0 on a target list that matches nothing, so the floor is the only
thing that tells "clean" apart from "linted nothing".
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
LINT_TASKFILE = REPO / "taskfiles" / "lint.yml"
FLUX_TASKFILE = REPO / "taskfiles" / "flux.yml"

_RUFF_FLOOR = re.compile(r"ruff matched no Python file")
_EMPTY_RENDER_FLOOR = re.compile(r"rendered no resources")


def _task(taskfile: Path, name: str) -> dict:
    """One task mapping out of a Taskfile tree file."""
    doc = yaml.safe_load(taskfile.read_text(encoding="utf-8")) or {}
    return (doc.get("tasks") or {}).get(name) or {}


def _cmds(task: dict) -> list:
    return [c for c in (task.get("cmds") or []) if isinstance(c, str)]


def ruff_floor_missing(task: dict) -> bool:
    """True when the ruff task runs ruff without first asserting it has work."""
    cmds = _cmds(task)
    if not any("ruff check" in c for c in cmds):
        return False
    return not any(_RUFF_FLOOR.search(c) for c in cmds)


def empty_render_floor_missing(task: dict) -> bool:
    """True when the task builds a Kustomization without asserting it rendered."""
    cmds = _cmds(task)
    if not any("kustomize build" in c for c in cmds):
        return False
    return not any(_EMPTY_RENDER_FLOOR.search(c) for c in cmds)


def test_the_local_ruff_task_keeps_the_linted_nothing_floor():
    """The CI python-lint include fails a target list holding no Python file, and
    the local task claims to mirror it."""
    assert LINT_TASKFILE.is_file(), "taskfiles/lint.yml is missing"
    task = _task(LINT_TASKFILE, "ruff")
    assert task, "taskfiles/lint.yml declares no `ruff` task"
    assert not ruff_floor_missing(task), (
        "taskfiles/lint.yml's ruff task runs `ruff check` with no linted-nothing "
        "floor: a target list that matches no .py file would pass. Restore the "
        "loop that exits 1 with 'ruff matched no Python file'."
    )


def test_the_local_flux_lint_task_keeps_the_empty_render_floor():
    """kubeconform exits 0 on empty input, so an emptied `resources:` list would
    lint clean while the reconcile prunes every object the stage applied."""
    assert FLUX_TASKFILE.is_file(), "taskfiles/flux.yml is missing"
    task = _task(FLUX_TASKFILE, "lint")
    assert task, "taskfiles/flux.yml declares no `lint` task"
    assert not empty_render_floor_missing(task), (
        "taskfiles/flux.yml's lint task builds each Kustomization with no "
        "empty-render floor: an emptied resources: list would pass. Restore the "
        "guard that fails with 'rendered no resources'."
    )


def test_a_floorless_ruff_task_is_reported():
    """Mutation case: the collector, not just the shipped state."""
    assert ruff_floor_missing({"cmds": ["ruff check --config ruff.toml scripts"]})
    assert not ruff_floor_missing({"cmds": ["echo hi"]})
    assert not ruff_floor_missing(
        {
            "cmds": [
                'test -n "$x" || { echo "ERROR: ruff matched no Python file"; exit 1; }',
                "ruff check --config ruff.toml scripts",
            ]
        }
    )


def test_a_floorless_flux_lint_task_is_reported():
    """Mutation case: the collector, not just the shipped state."""
    assert empty_render_floor_missing({"cmds": ['RAW=$(kustomize build "$SRCPATH")']})
    assert not empty_render_floor_missing({"cmds": ["echo hi"]})
    assert not empty_render_floor_missing(
        {
            "cmds": [
                'RAW=$(kustomize build "$SRCPATH")\n'
                'printf %s "$RAW" | grep -qE "^kind:" '
                '|| { echo "ERROR: $SRCPATH rendered no resources"; exit 1; }',
            ]
        }
    )
