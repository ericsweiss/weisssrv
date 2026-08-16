"""Tests for scripts/check-live-cpu-limits.py."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_live_cpu_limits",
    Path(__file__).resolve().parent / "check-live-cpu-limits.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _pod(ns: str, name: str, limits: dict | None, key: str = "containers",
         cname: str = "manager") -> dict:
    container: dict = {"name": cname}
    if limits is not None:
        container["resources"] = {"limits": limits}
    return {"metadata": {"namespace": ns, "name": name}, "spec": {key: [container]}}


# The real drift this check exists for: the removal patch in
# clusters/weisssrv/flux-system/kustomization.yaml renders no CPU limit, but the
# live Deployment kept one because a retired field manager still co-owned it.
FLUX_DRIFT = _pod("flux-system", "helm-controller-6d4f9b7c8-abcde",
                  {"cpu": "1", "memory": "1Gi"})
FLUX_CLEAN = _pod("flux-system", "helm-controller-6d4f9b7c8-abcde",
                  {"memory": "1Gi"})


def _run(pods: list[dict], monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"items": pods})))
    return mod.main()


def test_cpu_limit_on_live_pod_fails(monkeypatch):
    assert _run([FLUX_DRIFT], monkeypatch) == 1


def test_memory_only_limit_passes(monkeypatch):
    assert _run([FLUX_CLEAN], monkeypatch) == 0


def test_no_resources_at_all_passes(monkeypatch):
    assert _run([_pod("kube-system", "kube-vip-abcde", None)], monkeypatch) == 0


def test_init_container_cpu_limit_fails(monkeypatch):
    pod = _pod("ns", "app-1", {"cpu": "100m"}, key="initContainers", cname="init")
    assert _run([pod], monkeypatch) == 1


def test_ephemeral_container_cpu_limit_fails(monkeypatch):
    pod = _pod("ns", "app-1", {"cpu": "100m"}, key="ephemeralContainers",
               cname="debug")
    assert _run([pod], monkeypatch) == 1


def test_empty_string_cpu_limit_is_not_a_limit(monkeypatch):
    assert _run([_pod("ns", "app-1", {"cpu": ""})], monkeypatch) == 0


def test_violation_message_names_pod_and_container():
    out = mod.live_cpu_limit_violations([FLUX_DRIFT])
    assert len(out) == 1
    assert "flux-system/helm-controller-6d4f9b7c8-abcde" in out[0]
    assert "'manager'" in out[0] and "limits.cpu=1" in out[0]


def test_allowlist_matches_on_pod_name_prefix(monkeypatch):
    """Pod names carry a per-replica suffix, so the allowlist matches a prefix."""
    monkeypatch.setattr(
        mod, "LIVE_CPU_LIMIT_ALLOWLIST",
        {"flux-system/helm-controller/manager": "reason"},
    )
    assert _run([FLUX_DRIFT], monkeypatch) == 0


def test_allowlist_does_not_leak_across_namespaces(monkeypatch):
    monkeypatch.setattr(
        mod, "LIVE_CPU_LIMIT_ALLOWLIST",
        {"other-ns/helm-controller/manager": "reason"},
    )
    assert _run([FLUX_DRIFT], monkeypatch) == 1


def test_allowlist_does_not_leak_across_containers(monkeypatch):
    monkeypatch.setattr(
        mod, "LIVE_CPU_LIMIT_ALLOWLIST",
        {"flux-system/helm-controller/sidecar": "reason"},
    )
    assert _run([FLUX_DRIFT], monkeypatch) == 1


def test_shipped_allowlist_is_empty():
    """The policy has no exceptions today; adding one must be a deliberate diff."""
    assert mod.LIVE_CPU_LIMIT_ALLOWLIST == {}


# --- memory-limit drift -------------------------------------------------------
def _live_pod(name: str, mem: str, labels: dict | None = None) -> dict:
    return {
        "kind": "Pod",
        "metadata": {"namespace": "external-dns", "name": name,
                     "labels": labels if labels is not None else {"app": "external-dns"}},
        "spec": {"containers": [{"name": "external-dns",
                                 "resources": {"limits": {"memory": mem}}}]},
    }


def _deploy(mem: str = "256Mi") -> dict:
    return {
        "kind": "Deployment",
        "metadata": {"namespace": "external-dns", "name": "external-dns"},
        "spec": {
            "selector": {"matchLabels": {"app": "external-dns"}},
            "template": {"spec": {"containers": [
                {"name": "external-dns", "resources": {"limits": {"memory": mem}}}]}},
        },
    }


def test_stale_pod_memory_limit_is_reported():
    """external-dns: admitted at 99Mi under the old shape, template says 256Mi."""
    out = mod.memory_limit_drift([_live_pod("external-dns-9f8-764cm", "104295996")],
                                 [_deploy()]).lines
    assert len(out) == 1
    assert "external-dns/Deployment/external-dns" in out[0]
    assert "99Mi" in out[0] and "256Mi" in out[0]


def test_matching_memory_limit_is_silent():
    assert mod.memory_limit_drift([_live_pod("external-dns-abc", "256Mi")],
                                  [_deploy()]).lines == []


def test_drift_matches_superseded_replicaset_pods_by_label():
    """A pod from an older ReplicaSet still carries the selector labels."""
    pod = _live_pod("external-dns-old-xyz", "104295996",
                    labels={"app": "external-dns", "pod-template-hash": "9f8dc8b57"})
    assert len(mod.memory_limit_drift([pod], [_deploy()]).lines) == 1


def test_drift_does_not_match_an_unrelated_workload():
    pod = _live_pod("other-abc", "64Mi", labels={"app": "something-else"})
    assert mod.memory_limit_drift([pod], [_deploy()]).lines == []


# --- VPA-controlled limits are expected to diverge, not reported --------------
def _vpa(controlled_values: str | None = "RequestsAndLimits",
         update_mode: str | None = "Initial",
         container: str = "*", extra: list | None = None) -> dict:
    policy: dict = {"containerName": container}
    if controlled_values is not None:
        policy["controlledValues"] = controlled_values
    spec: dict = {
        "targetRef": {"apiVersion": "apps/v1", "kind": "Deployment",
                      "name": "external-dns"},
        "resourcePolicy": {"containerPolicies": [policy] + (extra or [])},
    }
    if update_mode is not None:
        spec["updatePolicy"] = {"updateMode": update_mode}
    return {
        "kind": "VerticalPodAutoscaler",
        "metadata": {"namespace": "external-dns", "name": "external-dns"},
        "spec": spec,
    }


STALE = _live_pod("external-dns-9f8-764cm", "104295996")


def test_requests_and_limits_target_is_skipped_not_reported():
    """The updater ratio-scales this limit at every admission — divergence from
    the template is the DESIGN, so reporting it is a finding nobody can fix."""
    out = mod.memory_limit_drift([STALE], [_deploy()], [_vpa()])
    assert out.lines == [] and out.skipped == 1


def test_requests_only_target_still_reports():
    out = mod.memory_limit_drift([STALE], [_deploy()], [_vpa("RequestsOnly")])
    assert len(out.lines) == 1 and out.skipped == 0


def test_update_mode_off_target_still_reports():
    """An Off VPA only recommends; it never rewrote this pod, so the ordinary
    admitted-before-the-commit reading holds."""
    out = mod.memory_limit_drift([STALE], [_deploy()],
                                 [_vpa("RequestsAndLimits", update_mode="Off")])
    assert len(out.lines) == 1 and out.skipped == 0


def test_no_vpa_at_all_still_reports():
    assert len(mod.memory_limit_drift([STALE], [_deploy()], []).lines) == 1


def test_absent_controlled_values_counts_as_requests_and_limits():
    """The API default is RequestsAndLimits, so an unset field means the VPA
    DOES own the limit — reading absent as uncontrolled would re-flag it."""
    out = mod.memory_limit_drift([STALE], [_deploy()], [_vpa(controlled_values=None)])
    assert out.lines == [] and out.skipped == 1


def test_a_vpa_with_no_container_policies_still_controls_every_container():
    vpa = _vpa()
    del vpa["spec"]["resourcePolicy"]
    out = mod.memory_limit_drift([STALE], [_deploy()], [vpa])
    assert out.lines == [] and out.skipped == 1


def test_exact_container_policy_overrides_the_wildcard():
    """The admission controller resolves the exact name first; so must this."""
    vpa = _vpa("RequestsAndLimits",
               extra=[{"containerName": "external-dns",
                       "controlledValues": "RequestsOnly"}])
    out = mod.memory_limit_drift([STALE], [_deploy()], [vpa])
    assert len(out.lines) == 1 and out.skipped == 0


def test_container_mode_off_is_not_limit_control():
    vpa = _vpa("RequestsAndLimits",
               extra=[{"containerName": "external-dns", "mode": "Off"}])
    assert len(mod.memory_limit_drift([STALE], [_deploy()], [vpa]).lines) == 1


def test_a_vpa_for_another_workload_does_not_silence_this_one():
    vpa = _vpa()
    vpa["spec"]["targetRef"]["name"] = "something-else"
    assert len(mod.memory_limit_drift([STALE], [_deploy()], [vpa]).lines) == 1


def test_main_reads_vpas_from_the_input_stream_and_reports_the_skip(monkeypatch, capsys):
    assert _run([STALE, _deploy(), _vpa()], monkeypatch) == 0
    out = capsys.readouterr()
    assert "diverge from their workload templates" not in out.err
    assert "1 container(s) skipped" in out.out


def test_drift_never_changes_the_exit_code(monkeypatch, capsys):
    items = [_live_pod("external-dns-9f8-764cm", "104295996"), _deploy()]
    assert _run(items, monkeypatch) == 0
    assert "diverge from their workload templates" in capsys.readouterr().err


def test_pods_only_input_skips_the_drift_check(monkeypatch, capsys):
    assert _run([FLUX_CLEAN], monkeypatch) == 0
    assert "memory-limit drift not checked" in capsys.readouterr().out


def test_quantity_parses_the_forms_kubectl_emits():
    assert mod._quantity("256Mi") == 256 * 1024**2
    assert mod._quantity("1Gi") == 1024**3
    assert mod._quantity("104295996") == 104295996
    assert mod._quantity("1e9") == 10**9
    assert mod._quantity(None) is None
    assert mod._quantity("banana") is None


def test_malformed_input_exits_cleanly(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    with pytest.raises(SystemExit):
        mod.main()


def test_non_pod_list_input_exits_cleanly(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"kind": "Pod"}'))
    with pytest.raises(SystemExit):
        mod.main()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
