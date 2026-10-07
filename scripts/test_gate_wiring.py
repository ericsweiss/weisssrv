"""Every gate in scripts/ is invoked by something that runs.

`task lint` and the pipeline are checked against each other elsewhere, which
leaves a gate neither names invisible to both: its invariant rots unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Files that invoke a gate: the pipeline, the task tree, the hook set.
RUNNER_GLOBS = (
    ".gitlab-ci.yml",
    ".gitlab/ci/*.yml",
    "Taskfile.yml",
    "taskfiles/*.yml",
    ".pre-commit-config.yaml",
)
_GATE_PREFIX = "check-"
_GATE_SUFFIXES = (".py", ".sh")

# Gates no runner names, each with how it runs instead. A reason naming a
# `scripts/test_*.py` is cross-checked against that module below.
RUNS_ELSEWHERE = {
    "check-molecule-image-pin.py": (
        "scripts/test_site_configs.py runs it against the real tree, so "
        "`task scripts:test` and the python-tests job both cover it"
    ),
    "check-tenant-traefik-isolation.py": (
        "scripts/test_site_configs.py runs it against the real tree, so "
        "`task scripts:test` and the python-tests job both cover it"
    ),
    "check-role-default-flips.py": (
        "operator-run at a collection pin bump: --from/--to compare two "
        "installed weisssrv.infra tags, which no pipeline has side by side"
    ),
}


def gates() -> list[str]:
    """Every gate script shipped in scripts/, by file name."""
    return sorted(
        p.name
        for p in SCRIPTS.iterdir()
        if p.is_file() and p.name.startswith(_GATE_PREFIX) and p.suffix in _GATE_SUFFIXES
    )


def runner_text() -> str:
    """Every runner file's text, concatenated, comments removed.

    A gate named only in a comment or a `changes:` path is not a run of it, but
    a comment naming it would read as one.
    """
    parts = []
    for glob in RUNNER_GLOBS:
        for path in sorted(REPO.glob(glob)):
            parts.append(path.read_text(encoding="utf-8"))
    assert parts, f"no runner file matched {RUNNER_GLOBS}"
    return re.sub(r"(?m)#.*$", "", "\n".join(parts))


def unwired(names: list[str], text: str) -> list[str]:
    """Gates the runner text never names."""
    return [name for name in names if f"scripts/{name}" not in text]


def test_the_gate_walk_sees_the_whole_set():
    """A walk that stopped finding gates would make the assertion below vacuous."""
    found = gates()
    assert len(found) > 30, f"only {len(found)} gate scripts found in {SCRIPTS}"


def test_every_gate_is_invoked_by_a_runner():
    missing = sorted(set(unwired(gates(), runner_text())) - set(RUNS_ELSEWHERE))
    assert not missing, (
        "gate scripts that no pipeline job, task or pre-commit hook runs: "
        + ", ".join(missing)
        + "\n\nWire each one up, or record it in RUNS_ELSEWHERE with how it runs."
    )


def test_the_exemptions_are_not_stale():
    text = runner_text()
    absent = sorted(name for name in RUNS_ELSEWHERE if not (SCRIPTS / name).is_file())
    assert not absent, f"RUNS_ELSEWHERE names gates that are gone: {absent}"
    wired = sorted(name for name in RUNS_ELSEWHERE if f"scripts/{name}" in text)
    assert not wired, (
        f"RUNS_ELSEWHERE excuses gates a runner now invokes: {wired} — drop the entry"
    )


@pytest.mark.parametrize("gate,reason", sorted(RUNS_ELSEWHERE.items()))
def test_a_suite_named_as_the_runner_still_runs_the_gate(gate: str, reason: str):
    """An exemption pointing at a suite that stopped running the gate would
    excuse a gate nothing runs at all."""
    for named in re.findall(r"scripts/test_[\w.-]+\.py", reason):
        module = REPO / named
        assert module.is_file(), f"{gate} is exempted because {named} runs it, but it is gone"
        assert gate in module.read_text(encoding="utf-8"), (
            f"{gate} is exempted because {named} runs it, but that suite no longer names it"
        )


def test_an_unrun_gate_is_reported():
    """Mutation case: the gate this suite exists to catch."""
    names = ["check-wired.py", "check-orphan.sh"]
    text = "cmds:\n  - scripts/check-wired.py\n"
    assert unwired(names, text) == ["check-orphan.sh"]
    assert unwired(names, text + "  - scripts/check-orphan.sh\n") == []


def test_a_commented_out_invocation_does_not_count():
    assert unwired(["check-orphan.sh"], "# scripts/check-orphan.sh\n") == []
    assert unwired(
        ["check-orphan.sh"], re.sub(r"(?m)#.*$", "", "# scripts/check-orphan.sh\n")
    ) == ["check-orphan.sh"]
