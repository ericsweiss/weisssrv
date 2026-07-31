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
