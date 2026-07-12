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


CONTAINER_RESOURCE_HPA = """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  metrics:
    - type: ContainerResource
      containerResource:
        name: cpu
        container: app
        target: {type: Utilization, averageUtilization: 80}
"""


def test_container_resource_hpa_with_cpu_vpa_fails(monkeypatch):
    """A per-container (ContainerResource) CPU HPA still clashes with a cpu VPA."""
    assert _run(CONTAINER_RESOURCE_HPA + "---" + _vpa("cpu, memory"), monkeypatch) == 1


def test_container_policy_off_does_not_clash(monkeypatch):
    """A per-container Off policy is recommend-only and must not count as mutating."""
    off_container_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        mode: "Off"
        controlledResources: [cpu, memory]
"""
    assert _run(CPU_HPA + "---" + off_container_vpa, monkeypatch) == 0


def test_named_container_memory_policy_still_clashes(monkeypatch):
    """A memory-only policy naming ONE container leaves the pod's other
    containers under default (cpu+memory) VPA control — the cpu clash with the
    HPA must not be hidden (fail closed without a '*' catch-all)."""
    named_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: app
        controlledResources: [memory]
"""
    assert _run(CPU_HPA + "---" + named_vpa, monkeypatch) == 1


def test_named_container_off_policy_still_clashes(monkeypatch):
    """A mode:Off policy naming ONE container does not turn off the VPA for
    unmatched containers, which keep default cpu+memory control."""
    named_off_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: app
        mode: "Off"
"""
    assert _run(CPU_HPA + "---" + named_off_vpa, monkeypatch) == 1


def test_named_policy_plus_catchall_memory_passes(monkeypatch):
    """A named policy alongside a '*' memory-only catch-all covers every
    container, so no default cpu control remains and there is no clash."""
    combo_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: sidecar
        mode: "Off"
      - containerName: "*"
        controlledResources: [memory]
"""
    assert _run(CPU_HPA + "---" + combo_vpa, monkeypatch) == 0


LIST_DOC = """
apiVersion: v1
kind: List
items:
  - apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata: {name: foo, namespace: ns}
    spec:
      scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
      metrics:
        - type: Resource
          resource: {name: cpu, target: {type: Utilization, averageUtilization: 80}}
  - apiVersion: autoscaling.k8s.io/v1
    kind: VerticalPodAutoscaler
    metadata: {name: foo, namespace: ns}
    spec:
      targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
      resourcePolicy:
        containerPolicies:
          - containerName: "*"
            controlledResources: [cpu, memory]
"""


def test_list_wrapped_resources_are_expanded(monkeypatch):
    """An HPA + clashing VPA inside a kind: List must still be detected."""
    assert _run(LIST_DOC, monkeypatch) == 1


def test_malformed_yaml_exits_cleanly(monkeypatch):
    """Bad YAML exits with a message instead of an uncaught traceback."""
    with pytest.raises(SystemExit):
        _run("foo: [unterminated\n", monkeypatch)


# --- chart-native HPA static assertion (--require-chart-native-vpas) -----------

def _chart_native_vpa(controlled: str = "memory") -> str:
    """A VPA for every CHART_NATIVE_HPA_TARGETS workload (memory-only by default)."""
    out = []
    for (ns, _kind, name), _src in mod.CHART_NATIVE_HPA_TARGETS.items():
        out.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: Auto}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [{controlled}]
""")
    return "\n---\n".join(out)


def _run_flag(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.argv", ["check", "--require-chart-native-vpas"])
    return _run(stdin_text, monkeypatch)


def test_chart_native_all_memory_only_passes(monkeypatch):
    """All chart-native workloads have a memory-only VPA -> OK."""
    assert _run_flag(_chart_native_vpa("memory"), monkeypatch) == 0


def test_chart_native_cpu_vpa_fails(monkeypatch):
    """A chart-native workload whose VPA also controls cpu conflicts with its HPA."""
    assert _run_flag(_chart_native_vpa("cpu, memory"), monkeypatch) == 1


def test_chart_native_missing_vpa_fails(monkeypatch):
    """A chart-native workload with no VPA in the corpus is flagged when required."""
    assert _run_flag("", monkeypatch) == 1


def test_chart_native_off_mode_vpa_does_not_satisfy(monkeypatch):
    """An Off (recommend-only) VPA never right-sizes, so it must NOT satisfy the
    chart-native requirement — the gate should still fail."""
    off = []
    for (ns, _kind, name), _src in mod.CHART_NATIVE_HPA_TARGETS.items():
        off.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: "Off"}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [memory]
""")
    assert _run_flag("\n---\n".join(off), monkeypatch) == 1


def test_chart_native_per_container_off_vpa_does_not_satisfy(monkeypatch):
    """A mutating (Auto) VPA whose every containerPolicy is mode:Off right-sizes
    nothing (empty controlled set) and must NOT satisfy the chart-native gate."""
    off = []
    for (ns, _kind, name), _src in mod.CHART_NATIVE_HPA_TARGETS.items():
        off.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: Auto}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        mode: "Off"
        controlledResources: [memory]
""")
    assert _run_flag("\n---\n".join(off), monkeypatch) == 1


def test_chart_native_check_is_opt_in(monkeypatch):
    """Without the flag, missing chart-native VPAs do not fail (generic-join only)."""
    monkeypatch.setattr("sys.argv", ["check"])
    assert _run("", monkeypatch) == 0


# --- "no CPU limits" policy (--require-chart-native-vpas) ----------------------

import yaml as _yaml  # noqa: E402


def _docs(text: str) -> list:
    return [d for d in _yaml.safe_load_all(text) if isinstance(d, dict)]


DEPLOY_WITH_CPU_LIMIT = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: ns}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits: {cpu: 500m, memory: 128Mi}
"""

DEPLOY_NO_CPU_LIMIT = DEPLOY_WITH_CPU_LIMIT.replace("cpu: 500m, ", "")

HR_WITH_CPU_LIMIT = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: thing, namespace: ns}
spec:
  values:
    controller:
      resources:
        requests: {cpu: 10m}
        limits: {cpu: 200m, memory: 64Mi}
"""

HR_NO_CPU_LIMIT = HR_WITH_CPU_LIMIT.replace("cpu: 200m, ", "")

# `cpu: null` clears a chart default rather than setting a limit — not a violation.
DEPLOY_NULL_CPU_LIMIT = DEPLOY_WITH_CPU_LIMIT.replace("cpu: 500m, ", "cpu: null, ")
HR_NULL_CPU_LIMIT = HR_WITH_CPU_LIMIT.replace("cpu: 200m, ", "cpu: null, ")

CRONJOB_WITH_CPU_LIMIT = """
apiVersion: batch/v1
kind: CronJob
metadata: {name: job, namespace: ns}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: c
              resources:
                limits: {cpu: 250m, memory: 64Mi}
"""


def test_cpu_limit_pod_spec_flagged():
    assert mod._cpu_limit_violations(_docs(DEPLOY_WITH_CPU_LIMIT))


def test_cpu_limit_pod_spec_memory_only_ok():
    assert mod._cpu_limit_violations(_docs(DEPLOY_NO_CPU_LIMIT)) == []


def test_cpu_limit_helmrelease_flagged():
    assert mod._cpu_limit_violations(_docs(HR_WITH_CPU_LIMIT))


def test_cpu_limit_helmrelease_memory_only_ok():
    assert mod._cpu_limit_violations(_docs(HR_NO_CPU_LIMIT)) == []


def test_cpu_limit_pod_spec_null_ok():
    """limits.cpu: null clears the default — not an effective CPU limit."""
    assert mod._cpu_limit_violations(_docs(DEPLOY_NULL_CPU_LIMIT)) == []


def test_cpu_limit_helmrelease_null_ok():
    """A HelmRelease clearing limits.cpu with null must not be flagged."""
    assert mod._cpu_limit_violations(_docs(HR_NULL_CPU_LIMIT)) == []


def test_cpu_limit_cronjob_flagged():
    assert mod._cpu_limit_violations(_docs(CRONJOB_WITH_CPU_LIMIT))


def test_cpu_limit_allowlist_exempts(monkeypatch):
    monkeypatch.setattr(mod, "CPU_LIMIT_ALLOWLIST", {"ns/Deployment/app"})
    assert mod._cpu_limit_violations(_docs(DEPLOY_WITH_CPU_LIMIT)) == []


def test_cpu_limit_integrated_fails_with_flag(monkeypatch):
    """Full-corpus mode (flag set) fails when a workload sets a CPU limit."""
    stream = _chart_native_vpa("memory") + "\n---\n" + DEPLOY_WITH_CPU_LIMIT
    assert _run_flag(stream, monkeypatch) == 1


def test_cpu_limit_integrated_passes_with_flag(monkeypatch):
    """Full-corpus mode passes when CPU limits are absent (memory-only limits)."""
    stream = _chart_native_vpa("memory") + "\n---\n" + DEPLOY_NO_CPU_LIMIT
    assert _run_flag(stream, monkeypatch) == 0


def test_cpu_limit_not_checked_without_flag(monkeypatch):
    """The generic join (no flag) does not enforce the CPU-limit policy."""
    monkeypatch.setattr("sys.argv", ["check"])
    assert _run(DEPLOY_WITH_CPU_LIMIT, monkeypatch) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
