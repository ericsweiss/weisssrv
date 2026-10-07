"""Every Namespace in kubernetes/ declares a Pod Security admission level.

A namespace with no `pod-security.kubernetes.io/enforce` label runs privileged
workloads unchallenged. Mirrored by the cluster template's own PSA test.
"""
from __future__ import annotations

import functools
from pathlib import Path

from conftest import REPO, k8s_documents, reported

MANIFESTS = REPO / "kubernetes"
ENFORCE = "pod-security.kubernetes.io/enforce"
LEVELS = {"privileged", "baseline", "restricted"}

# Files `flux install` renders, re-generated on every Flux upgrade: an edit here
# is lost at the next bootstrap, so the label has to come from upstream.
EXEMPT = {
    "kubernetes/clusters/weisssrv/flux-system/gotk-components.yaml": (
        "rendered by `flux bootstrap`; it ships pod-security warn labels only "
        "and is replaced wholesale on every Flux version bump"
    ),
}


def _walk(root: Path) -> tuple[list[tuple[str, str, dict]], list[str]]:
    """(namespaces, manifests that would not parse) from one walk of a tree.

    An unreadable manifest is returned, not dropped: a namespace the walk never
    saw leaves every assertion below green.
    """
    documents, unreadable = k8s_documents(root, suffix="*.yaml")
    found: list[tuple[str, str, dict]] = []
    for path, doc in documents:
        if doc.get("kind") != "Namespace":
            continue
        meta = doc.get("metadata") or {}
        found.append((reported(path), meta.get("name", ""), meta.get("labels") or {}))
    return found, unreadable


@functools.cache
def _scan() -> tuple[tuple[tuple[str, str, dict], ...], tuple[str, ...]]:
    """The shipped tree, walked and parsed once for the whole module."""
    found, unreadable = _walk(MANIFESTS)
    return tuple(found), tuple(unreadable)


def _namespaces() -> tuple[tuple[str, str, dict], ...]:
    return _scan()[0]


def test_the_walk_finds_the_namespaces():
    """A collector that stopped matching would pass every assertion below."""
    assert len(_namespaces()) >= 30


def test_no_manifest_is_dropped_as_unparseable():
    """An unreadable manifest shrinks the subject; it is a finding, not a skip."""
    assert list(_scan()[1]) == []


def test_every_namespace_enforces_a_pod_security_level():
    missing = sorted(
        f"{path}: {name}"
        for path, name, labels in _namespaces()
        if path not in EXEMPT and labels.get(ENFORCE) not in LEVELS
    )
    assert not missing, (
        "namespaces with no pod-security.kubernetes.io/enforce label: "
        + ", ".join(missing)
        + "\n\nAdd the label at the level the workload needs (baseline for most "
        "apps, privileged only for host-access workloads), or exempt the file "
        "here with the reason it cannot carry one."
    )


def test_every_exemption_is_still_a_file_without_the_label():
    live = {path: labels for path, _, labels in _namespaces()}
    for path, reason in EXEMPT.items():
        assert path in live, f"EXEMPT names {path}, which holds no Namespace any more"
        assert live[path].get(ENFORCE) not in LEVELS, (
            f"{path} now enforces a level; drop it from EXEMPT"
        )
        assert len(reason.split()) >= 8, f"{path} needs a real reason"


def test_the_gate_would_notice_an_unlabelled_namespace(tmp_path):
    """Mutation case: the same walk over a namespace with no enforce label."""
    (tmp_path / "ns.yaml").write_text(
        "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: loose\n"
    )
    found, unreadable = _walk(tmp_path)
    assert unreadable == []
    assert [(name, labels.get(ENFORCE)) for _, name, labels in found] == [("loose", None)]


def test_the_walk_reports_a_manifest_that_will_not_parse(tmp_path):
    """Mutation case: an unparseable file comes back as a reported path rather
    than quietly shrinking the corpus."""
    (tmp_path / "ns.yaml").write_text(
        "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: fine\n"
    )
    (tmp_path / "broken.yaml").write_text("kind: Namespace\n  name: [unclosed\n")
    found, unreadable = _walk(tmp_path)
    assert [name for _, name, _ in found] == ["fine"]
    assert len(unreadable) == 1 and "broken.yaml" in unreadable[0]
