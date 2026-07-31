#!/usr/bin/env python3
"""Assert the LIVE cluster imposes no CPU limits (docs/33-autoscaling.md).

`scripts/check-hpa-vpa-invariant.py` proves the policy holds in *git*: nothing in
the rendered corpus or a HelmRelease's values sets `limits.cpu`. That is not the
same as the policy holding in the *cluster*, and the difference is not
theoretical — the Flux controllers carried `limits.cpu: 1` for months after the
removal patch landed (clusters/weisssrv/flux-system/kustomization.yaml), because
under server-side apply the stale `flux` bootstrap field manager still co-owned
the field: kustomize-controller dropping it from its OWN fieldset cannot delete a
field another manager owns. `kustomize build` rendered no limit, the live
Deployments had one, and a VPA then scaled that surviving limit down with every
request revision until helm-controller sat at 79% of it with measurable CFS
throttling.

Three known ways a CPU limit reaches a live pod without appearing in git:
  1. SSA co-ownership by a retired field manager (the case above);
  2. a Helm chart default the values file never mentions (map-merge keeps it);
  3. an admission mutation (LimitRange defaults, a mutating webhook).

None is visible to a static lint, so this check reads the cluster instead.

Input: `kubectl get pods -A -o json` on stdin (keeps the logic unit-testable
with no cluster). Exit 1 and list the offenders if any container — init,
regular or ephemeral — declares a CPU limit.

Usage:
  kubectl get pods -A -o json | python3 scripts/check-live-cpu-limits.py
"""
from __future__ import annotations

import json
import sys

# Containers permitted a live CPU limit despite the repo-wide policy, as
# "namespace/pod-name-prefix/container". Empty by design — an entry here is a
# claim that CFS throttling is WANTED for that container, and needs a reason.
LIVE_CPU_LIMIT_ALLOWLIST: dict[str, str] = {}

REMEDIATION = """
Remediation depends on where the limit comes from:
  - rendered by git   -> remove it from the manifest (the static lint,
                         scripts/check-hpa-vpa-invariant.py, should have caught
                         it: extend that check too)
  - stale SSA owner   -> release the field once per workload, e.g.
                         kubectl -n flux-system patch deploy <name> --type=json \\
                           -p '[{"op":"remove","path":"/spec/template/spec/containers/0/resources/limits/cpu"}]'
                         (verify with: kubectl get deploy <name> -o yaml \\
                           | grep -A3 managedFields -- the retired manager must
                           no longer list f:limits{f:cpu})
  - chart default     -> set the value explicitly to null in the HelmRelease
                         (`limits: {cpu: null, memory: ...}`), which makes Helm
                         drop the key on merge
""".rstrip()


def _containers(pod: dict) -> list[dict]:
    spec = pod.get("spec") or {}
    out: list[dict] = []
    for key in ("initContainers", "containers", "ephemeralContainers"):
        v = spec.get(key)
        if isinstance(v, list):
            out.extend(c for c in v if isinstance(c, dict))
    return out


def _allowlisted(namespace: str, pod: str, container: str) -> bool:
    """Pods are named per-replica, so the middle segment is a NAME PREFIX."""
    for key in LIVE_CPU_LIMIT_ALLOWLIST:
        ns, prefix, cname = key.split("/", 2)
        if ns == namespace and container == cname and pod.startswith(prefix):
            return True
    return False


def live_cpu_limit_violations(pods: list[dict]) -> list[str]:
    """One line per container declaring a CPU limit, allowlist applied."""
    out: list[str] = []
    for pod in pods:
        meta = pod.get("metadata") or {}
        ns = meta.get("namespace", "")
        name = meta.get("name", "?")
        for c in _containers(pod):
            cpu = ((c.get("resources") or {}).get("limits") or {}).get("cpu")
            if cpu in (None, ""):
                continue
            cname = c.get("name", "?")
            if _allowlisted(ns, name, cname):
                continue
            out.append(f"  {ns}/{name}: container {cname!r} has limits.cpu={cpu}")
    return sorted(out)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.exit(f"Failed to parse `kubectl get pods -o json` input: {exc}")
    pods = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(pods, list):
        sys.exit("Input is not a pod list (expected `kubectl get pods -A -o json`)")

    violations = live_cpu_limit_violations([p for p in pods if isinstance(p, dict)])
    if violations:
        print(
            "Live CPU-limit policy violated — these containers are running with a "
            "CPU limit that the repo does not declare (docs/33-autoscaling.md). "
            "CFS throttling hurts tail latency and, on a VPA-managed workload, the "
            "limit shrinks with every request revision:",
            file=sys.stderr,
        )
        print("\n".join(violations), file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    print(f"Live CPU-limit policy OK ({len(pods)} pods checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
