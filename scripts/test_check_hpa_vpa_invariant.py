"""Tests for scripts/check-hpa-vpa-invariant.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_hpa_vpa_invariant",
    Path(__file__).resolve().parent / "check-hpa-vpa-invariant.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _run(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main()


CPU_HPA = """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  metrics:
    - type: Resource
      resource: {name: cpu, target: {type: Utilization, averageUtilization: 80}}
"""


def _vpa(controlled: str, name: str = "foo") -> str:
    return f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: ns}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: foo}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [{controlled}]
"""


def test_memory_only_vpa_with_cpu_hpa_passes(monkeypatch):
    assert _run(CPU_HPA + "---" + _vpa("memory"), monkeypatch) == 0


def test_cpu_vpa_with_cpu_hpa_fails(monkeypatch):
    assert _run(CPU_HPA + "---" + _vpa("cpu, memory"), monkeypatch) == 1


def test_vpa_default_controlled_resources_fails(monkeypatch):
    """No controlledResources means cpu+memory — clashes with a CPU HPA."""
    vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        minAllowed: {memory: 32Mi}
"""
    assert _run(CPU_HPA + "---" + vpa, monkeypatch) == 1


def test_hpa_without_matching_vpa_passes(monkeypatch):
    assert _run(CPU_HPA, monkeypatch) == 0


def test_different_namespace_does_not_clash(monkeypatch):
    other_ns_vpa = _vpa("cpu, memory").replace("namespace: ns", "namespace: other")
    assert _run(CPU_HPA + "---" + other_ns_vpa, monkeypatch) == 0


def test_memory_hpa_vs_memory_vpa_fails(monkeypatch):
    mem_hpa = CPU_HPA.replace("name: cpu", "name: memory")
    assert _run(mem_hpa + "---" + _vpa("memory"), monkeypatch) == 1


EXTERNAL_HPA = """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  metrics:
    - type: External
      external:
        metric: {name: queue_depth}
        target: {type: AverageValue, averageValue: "10"}
"""


def test_external_only_hpa_with_cpu_vpa_passes(monkeypatch):
    """metrics present but purely External — no Resource metric, so no clash."""
    assert _run(EXTERNAL_HPA + "---" + _vpa("cpu, memory"), monkeypatch) == 0


def test_two_vpas_one_cpu_one_memory_fails(monkeypatch):
    """Two VPAs on one target must be unioned: the cpu one must not be masked."""
    mem_vpa = _vpa("memory", name="foo-mem")
    cpu_vpa = _vpa("cpu", name="foo-cpu")
    assert _run(CPU_HPA + "---" + mem_vpa + "---" + cpu_vpa, monkeypatch) == 1


def test_update_mode_off_vpa_does_not_clash(monkeypatch):
    """A recommend-only (Off) VPA never mutates pods — coredns pattern."""
    off_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  updatePolicy: {updateMode: "Off"}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [cpu, memory]
"""
    assert _run(CPU_HPA + "---" + off_vpa, monkeypatch) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
