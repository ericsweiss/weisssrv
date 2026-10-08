"""Every generated file in this repo has a drift gate, locally and in CI.

Each scripts/generate-*.py is paired here with its output, a `task lint` arm and
a CI job, so a fourth generator cannot ship ungated the way the third nearly did.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from taskfile_tree import load_tasks
from test_vendored_byte_identity import load_ci_doc

REPO = Path(__file__).resolve().parent.parent
CI_FILE = REPO / ".gitlab-ci.yml"
SCRIPTS_README = REPO / "scripts" / "README.md"

# {generated file: its generator}. A generator absent from the values fails the
# exhaustiveness test below, which is what forces a new one into this map.
GENERATED_FILES = {
    "scripts/hosts.env": "generate-hosts-env.py",
    "kubernetes/infrastructure/sources/versions-configmap.yaml":
        "generate-versions-configmap.py",
    "kubernetes/infrastructure/observability/loki/host-log-staleness.yaml":
        "generate-host-log-staleness.py",
}


def _script_text(body) -> str:
    """Every command line of a task or a CI job, as one string."""
    if isinstance(body, dict):
        parts = []
        for key in ("script", "before_script", "after_script", "cmds"):
            parts.append(_script_text(body.get(key)))
        return "\n".join(parts)
    if isinstance(body, list):
        return "\n".join(_script_text(item) for item in body)
    return str(body) if isinstance(body, str) else ""


# `task <name>` on a command line, and a `- task:` step: a lint arm delegates
# the regenerate half to the namespace that owns the generator.
TASK_CALL_RE = re.compile(r"\btask\s+([a-z0-9][\w.-]*(?::[\w.-]+)*)")


# Root `vars:` reach every included file, so a task may spell a path as
# `{{.VERSIONS_CM}}`. Resolve them before matching a target path.
_VAR_REF_RE = re.compile(r"\{\{\s*\.([A-Z0-9_]+)\s*\}\}")


def _root_vars() -> dict[str, str]:
    doc = yaml.safe_load((REPO / "Taskfile.yml").read_text(encoding="utf-8")) or {}
    return {
        name: value
        for name, value in (doc.get("vars") or {}).items()
        if isinstance(value, str)
    }


def _resolve_vars(text: str) -> str:
    values = _root_vars()
    return _VAR_REF_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _expanded_text(name: str, tasks: dict[str, dict], depth: int = 3) -> str:
    """A task's commands with every task it calls inlined, so an arm that
    delegates the regenerate half still reads as one gate."""
    body = tasks.get(name)
    if body is None or depth == 0:
        return ""
    text = _script_text(body)
    called = {
        str(step["task"]) for step in (body.get("cmds") or [])
        if isinstance(step, dict) and step.get("task")
    }
    called |= {m.group(1) for m in TASK_CALL_RE.finditer(text)}
    for ref in sorted(called - {name}):
        text += "\n" + _expanded_text(ref, tasks, depth - 1)
    return _resolve_vars(text)


def _regenerates_and_compares(text: str, target: str, generator: str) -> bool:
    """True when `text` runs the generator and compares the result: either the
    generator's own `--check`, or a diff against the target."""
    if generator not in text:
        return False
    self_check = re.search(rf"{re.escape(generator)}(?:\s+(?:--[\w-]+|\S+))*?\s+--check\b", text)
    return bool(self_check) or ("diff" in text and target in text)


@pytest.fixture(scope="module")
def tasks() -> dict[str, dict]:
    return load_tasks(REPO)


@pytest.fixture(scope="module")
def ci() -> dict:
    return load_ci_doc(CI_FILE)


def test_every_generator_in_the_tree_is_mapped_to_its_output():
    on_disk = {p.name for p in (REPO / "scripts").glob("generate-*.py")}
    assert on_disk, "found no scripts/generate-*.py — this guard checked nothing"
    assert on_disk == set(GENERATED_FILES.values()), (
        "generators with no entry in GENERATED_FILES: "
        f"{sorted(on_disk - set(GENERATED_FILES.values()))}; mapped generators "
        f"no longer on disk: {sorted(set(GENERATED_FILES.values()) - on_disk)}"
    )


@pytest.mark.parametrize("target,generator", sorted(GENERATED_FILES.items()))
def test_the_generated_file_and_its_generator_exist(target, generator):
    assert (REPO / target).is_file(), f"{target} is not in the tree"
    assert (REPO / "scripts" / generator).is_file()


@pytest.mark.parametrize("target,generator", sorted(GENERATED_FILES.items()))
def test_a_task_regenerates_and_compares_the_generated_file(target, generator, tasks):
    """Locally, under `task lint`: otherwise the drift first shows up in CI."""
    arms = [
        name for name in tasks
        if name.startswith("lint:") and _regenerates_and_compares(
            _expanded_text(name, tasks), target, generator
        )
    ]
    assert arms, (
        f"no lint: task regenerates {target} with scripts/{generator} and "
        "compares the result"
    )
    lint_text = _script_text(tasks["lint"])
    referenced = {
        str(step.get("task")) for step in (tasks["lint"].get("cmds") or [])
        if isinstance(step, dict) and step.get("task")
    }
    referenced |= {m.group(1) for m in TASK_CALL_RE.finditer(lint_text)}
    assert set(arms) & referenced, (
        f"the drift arm for {target} ({arms}) is not reached from `task lint`"
    )


@pytest.mark.parametrize("target,generator", sorted(GENERATED_FILES.items()))
def test_a_ci_job_regenerates_and_compares_the_generated_file(target, generator, ci):
    jobs = [
        name for name, body in ci.items()
        if isinstance(body, dict) and _regenerates_and_compares(
            _script_text(body), target, generator
        )
    ]
    assert jobs, (
        f"no job in .gitlab-ci.yml regenerates {target} with scripts/{generator} "
        "and compares the result"
    )


@pytest.mark.parametrize("target,generator", sorted(GENERATED_FILES.items()))
def test_the_scripts_readme_names_the_generator_and_its_output(target, generator):
    body = SCRIPTS_README.read_text(encoding="utf-8")
    assert generator in body, f"scripts/README.md does not name {generator}"
    assert Path(target).name in body, (
        f"scripts/README.md names {generator} but not its output {Path(target).name}"
    )


# Mutation cases for the matcher the three gates above rest on.

def test_a_generator_run_without_a_comparison_is_not_a_gate():
    """`task flux:sync-versions` regenerates in place, which is the opposite of
    comparing: it must not count as the drift gate."""
    assert not _regenerates_and_compares(
        "python3 scripts/generate-versions-configmap.py --output x.yaml",
        "x.yaml",
        "generate-versions-configmap.py",
    )


def test_a_check_flag_and_a_diff_both_count():
    assert _regenerates_and_compares(
        "python3 scripts/generate-host-log-staleness.py --check",
        "kubernetes/infrastructure/observability/loki/host-log-staleness.yaml",
        "generate-host-log-staleness.py",
    )
    assert _regenerates_and_compares(
        "python3 scripts/generate-hosts-env.py --output /tmp/g.env\n"
        "diff -u scripts/hosts.env /tmp/g.env",
        "scripts/hosts.env",
        "generate-hosts-env.py",
    )


def test_a_diff_of_some_other_file_is_not_this_gate():
    assert not _regenerates_and_compares(
        "python3 scripts/generate-hosts-env.py --output /tmp/g.env\n"
        "diff -u scripts/other.env /tmp/g.env",
        "scripts/hosts.env",
        "generate-hosts-env.py",
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
