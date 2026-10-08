"""A Flux stage must outlast the releases it waits on.

A `wait: true` Kustomization fails the moment its own timeout expires, so each
stage keeps headroom over the slowest HelmRelease beneath its path.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import REPO, assert_all_parsed, k8s_documents

K8S_ROOT = REPO / "kubernetes"
CLUSTERS_DIR = K8S_ROOT / "clusters"

_DURATION_RE = re.compile(r"(\d+)([hms])")
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}


def to_seconds(value: str) -> int:
    """Parse a Flux duration ('15m', '90s', '1h30m') into seconds."""
    total = 0
    for amount, unit in _DURATION_RE.findall(str(value)):
        total += int(amount) * _UNIT_SECONDS[unit]
    assert total > 0, f"unparseable duration {value!r}"
    return total


def _cluster_documents() -> tuple[list[tuple[Path, dict]], list[str]]:
    return k8s_documents(CLUSTERS_DIR)


def stage_kustomizations() -> list[tuple[Path, dict]]:
    """Every waiting Flux stage. Selected on `wait` alone: a stage with no
    explicit timeout is the case this gate exists for, so it must not drop out
    of the parametrisation."""
    found = []
    for path, doc in _cluster_documents()[0]:
        if doc.get("kind") != "Kustomization":
            continue
        if not str(doc.get("apiVersion", "")).startswith("kustomize.toolkit.fluxcd.io/"):
            continue
        spec = doc.get("spec") or {}
        if spec.get("wait") and spec.get("path"):
            found.append((path, doc))
    return found


def release_timeouts(stage_path: str) -> dict[str, int]:
    """Every HelmRelease timeout (spec, install, upgrade) under a stage path."""
    directory = REPO / stage_path.lstrip("./")
    assert directory.is_dir(), (
        f"stage path {stage_path} does not exist — the gate examined nothing"
    )
    timeouts: dict[str, int] = {}
    documents, unreadable = k8s_documents(directory)
    assert_all_parsed(unreadable, "HelmRelease")
    for path, doc in documents:
        if doc.get("kind") != "HelmRelease":
            continue
        spec = doc.get("spec") or {}
        values = [spec.get("timeout")]
        for phase in ("install", "upgrade"):
            values.append((spec.get(phase) or {}).get("timeout"))
        for value in filter(None, values):
            name = f"{path.relative_to(REPO)}:{doc['metadata']['name']}"
            timeouts[name] = max(to_seconds(value), timeouts.get(name, 0))
    return timeouts


def _too_tight(stage: int, releases: dict[str, int]) -> list[str]:
    """Releases the stage does not outlast."""
    return [f"{name}'s {release}s" for name, release in releases.items() if stage <= release]


def test_there_are_waiting_stages_to_check():
    assert stage_kustomizations(), "no wait:true cluster Kustomization found"


def test_every_cluster_manifest_parsed():
    """A file that will not parse drops its stage out of the parametrisation
    below, which reads as a pass."""
    assert_all_parsed(_cluster_documents()[1], "Kustomization")


def test_to_seconds_parses_the_flux_spellings():
    assert to_seconds("90s") == 90
    assert to_seconds("15m") == 900
    assert to_seconds("1h30m") == 5400
    with pytest.raises(AssertionError):
        to_seconds("soon")


def test_a_stage_that_does_not_outlast_a_release_is_reported():
    """Mutation case: the comparison, not just the shipped corpus. Equal
    timeouts are a failure — the stage has no headroom at all."""
    releases = {"release.yaml:app": 900}
    assert _too_tight(900, releases) == ["release.yaml:app's 900s"]
    assert _too_tight(600, releases)
    assert _too_tight(901, releases) == []


def test_at_least_one_stage_resolved_release_timeouts():
    """A refactor that moved every explicit HelmRelease timeout out of the stage
    paths would leave each parametrised case below comparing nothing."""
    assert any(release_timeouts(doc["spec"]["path"]) for _path, doc in stage_kustomizations()), (
        "no stage path holds a HelmRelease with an explicit timeout — the "
        "comparison in this gate examined nothing"
    )


@pytest.mark.parametrize(
    "path,doc",
    stage_kustomizations(),
    ids=lambda item: item.name if isinstance(item, Path) else "",
)
def test_a_stage_outlasts_the_releases_it_waits_on(path, doc):
    timeout = (doc.get("spec") or {}).get("timeout")
    assert timeout, (
        f"{path.name}: wait:true with no explicit spec.timeout — it inherits the "
        "interval-derived default and is outside this gate"
    )
    tight = _too_tight(to_seconds(timeout), release_timeouts(doc["spec"]["path"]))
    assert not tight, (
        f"{path.name} timeout {timeout} does not exceed "
        + ", ".join(tight)
        + " — a slow but healthy install fails the stage"
    )
