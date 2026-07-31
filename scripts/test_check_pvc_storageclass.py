"""Tests for scripts/check-pvc-storageclass.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_pvc_storageclass",
    Path(__file__).resolve().parent / "check-pvc-storageclass.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _run(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main()


PVC_STATIC = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: loki-data, namespace: observability}
spec:
  storageClassName: ""
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 75Gi}}
"""

# The 2026-07 incident shape: no storageClassName, so the DefaultStorageClass
# admission plugin rewrites it to local-path at create time.
PVC_UNPINNED = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: loki-data, namespace: observability}
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 75Gi}}
"""

STS_PINNED = """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: loki, namespace: observability}
spec:
  volumeClaimTemplates:
    - metadata: {name: storage}
      spec:
        storageClassName: ""
        resources: {requests: {storage: 75Gi}}
"""

STS_UNPINNED = STS_PINNED.replace('        storageClassName: ""\n', "")

HR_PINNED = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: loki, namespace: observability}
spec:
  values:
    singleBinary:
      persistence:
        enabled: true
        size: 75Gi
        storageClass: "-"
"""

HR_UNPINNED = HR_PINNED.replace('        storageClass: "-"\n', "")


def test_static_pvc_passes(monkeypatch):
    assert _run(PVC_STATIC, monkeypatch) == 0


def test_unpinned_pvc_fails(monkeypatch):
    assert _run(PVC_UNPINNED, monkeypatch) == 1


def test_named_class_passes(monkeypatch):
    """An explicit named class is a deliberate choice, not a fall-through."""
    doc = PVC_STATIC.replace('storageClassName: ""', "storageClassName: nfs-downloads")
    assert _run(doc, monkeypatch) == 0


def test_pinned_volume_claim_template_passes(monkeypatch):
    assert _run(STS_PINNED, monkeypatch) == 0


def test_unpinned_volume_claim_template_fails(monkeypatch):
    """volumeClaimTemplates are immutable — this must be caught before apply."""
    assert _run(STS_UNPINNED, monkeypatch) == 1


def test_helmrelease_persistence_with_class_passes(monkeypatch):
    assert _run(HR_PINNED, monkeypatch) == 0


def test_helmrelease_persistence_without_class_fails(monkeypatch):
    """The chart renders the PVC server-side, so only the values can be linted."""
    assert _run(HR_UNPINNED, monkeypatch) == 1


def test_helmrelease_existing_claim_passes(monkeypatch):
    """existingClaim binds a PVC this repo already pins (authentik postgres)."""
    doc = HR_UNPINNED.replace("        size: 75Gi", "        size: 75Gi\n        existingClaim: loki-data")
    assert _run(doc, monkeypatch) == 0


def test_disabled_persistence_block_is_ignored(monkeypatch):
    doc = HR_UNPINNED.replace("enabled: true", "enabled: false")
    assert _run(doc, monkeypatch) == 0


def test_unrelated_size_key_is_not_a_persistence_block(monkeypatch):
    """A `size` under a non-persistence key still names no class — but the
    block is only flagged when it is not explicitly disabled, and a values tree
    with neither `size` nor `enabled` must never trip it."""
    doc = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: thing, namespace: ns}
spec:
  values:
    resources: {limits: {memory: 128Mi}}
"""
    assert _run(doc, monkeypatch) == 0


def test_list_wrapped_resources_are_expanded(monkeypatch):
    doc = """
apiVersion: v1
kind: List
items:
  - apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: {name: a, namespace: ns}
    spec:
      resources: {requests: {storage: 1Gi}}
"""
    assert _run(doc, monkeypatch) == 1


def test_malformed_yaml_exits_cleanly(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a: [1,\nb: {"))
    with pytest.raises(SystemExit):
        mod.main()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
