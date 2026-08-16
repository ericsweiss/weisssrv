#!/usr/bin/env python3
"""Assert the LIVE cluster imposes no CPU limits (docs/33-autoscaling.md).

`scripts/check-hpa-vpa-invariant.py` proves the policy holds in *git*; that is
not the same as it holding in the *cluster*. Three ways a CPU limit reaches a
live pod without appearing in git:
  1. SSA co-ownership by a retired field manager — a controller dropping the
     field from its OWN fieldset cannot delete a field another manager owns;
  2. a Helm chart default the values file never mentions (map-merge keeps it);
  3. an admission mutation (LimitRange defaults, a mutating webhook).

None is visible to a static lint, so this check reads the cluster instead.

Input: `kubectl get pods -A -o json` on stdin (keeps the logic unit-testable
with no cluster). Exit 1 and list the offenders if any container — init,
regular or ephemeral — declares a CPU limit.

It also reports the OPPOSITE drift, on memory: a limit git declares that the
live pod never got. A mutating VPA rewrites resources at pod ADMISSION, so a pod
keeps the pair it was admitted with for its whole lifetime — a new memory limit
(or a flipped `controlledValues`) on an `Initial`-tier VPA updates the template
while every running pod keeps the old ceiling. The fix is a `kubectl rollout
restart`, which is why this half only WARNS and never changes the exit code:
nothing in the reconcile loop clears it.

Feed the workload templates in alongside the pods to enable it — the check is
skipped (with a note) when the input holds pods only. Feed the VPAs in too:
where an ACTIVE VPA controls a container's limits (`controlledValues:
RequestsAndLimits`, which is also the API default), the live limit is SUPPOSED to
diverge from the template — the updater ratio-scales it with each request
revision — so those containers are excluded from drift reporting rather than
reported as findings nobody can fix.

  kubectl get pods -A -o json | python3 scripts/check-live-cpu-limits.py
  kubectl get pods,deployments,statefulsets,daemonsets,verticalpodautoscalers \\
    -A -o json | python3 scripts/check-live-cpu-limits.py
"""
from __future__ import annotations

import json
import sys
from typing import NamedTuple

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


# --- memory-limit drift (live pod vs its workload template) -------------------
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}
VPA_KIND = "VerticalPodAutoscaler"

_SUFFIXES = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5,
    "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5,
}


def _quantity(value) -> int | None:
    """A Kubernetes memory quantity as bytes, or None if it is unparseable."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    for suffix, factor in _SUFFIXES.items():
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)]) * factor)
            except ValueError:
                return None
    try:
        return int(float(text))  # plain bytes, incl. the "1e9" form
    except ValueError:
        return None


def _human(n: int) -> str:
    return f"{n / 1024**2:.0f}Mi" if n >= 1024**2 else f"{n}B"


def _template_containers(workload: dict) -> list[dict]:
    pod = ((workload.get("spec") or {}).get("template") or {}).get("spec") or {}
    return _containers({"spec": pod})


def _matches(workload: dict, pod: dict) -> bool:
    """A pod belongs to a workload when the workload's selector covers its labels.

    Deliberately label-based rather than ownerReference-based: a pod from a
    SUPERSEDED ReplicaSet still matches, and that is exactly the stale pod this
    check is looking for.
    """
    selector = ((workload.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
    if not selector:
        return False
    labels = (pod.get("metadata") or {}).get("labels") or {}
    return all(labels.get(k) == v for k, v in selector.items())


def _vpa_limit_policies(vpas: list[dict]) -> dict[tuple[str, str, str], dict]:
    """-> {(namespace, target kind, target name): {containerName: policy}}.

    Only ACTIVE VPAs are indexed: `updateMode: Off` is recommendation-only and
    never rewrites a pod, so a workload under one drifts for the ordinary
    (reportable) reason. A VPA with no `containerPolicies` at all still controls
    every container, hence the wildcard default seeded before the explicit
    entries overlay it.
    """
    index: dict[tuple[str, str, str], dict] = {}
    for vpa in vpas:
        spec = vpa.get("spec") or {}
        if ((spec.get("updatePolicy") or {}).get("updateMode") or "Auto") == "Off":
            continue
        target = spec.get("targetRef") or {}
        key = ((vpa.get("metadata") or {}).get("namespace", ""),
               target.get("kind", "?"), target.get("name", "?"))
        policies = index.setdefault(key, {"*": {}})
        for policy in (spec.get("resourcePolicy") or {}).get("containerPolicies") or []:
            if isinstance(policy, dict) and policy.get("containerName"):
                policies[policy["containerName"]] = policy
    return index


def _limits_are_vpa_controlled(policies: dict, container: str | None) -> bool:
    """Does the VPA rewrite this container's LIMITS at admission?

    An exact containerName policy wins over the wildcard, exactly as the VPA
    admission controller resolves it. `controlledValues` is absent far more often
    than it is set, and its API default is RequestsAndLimits — so absent means
    controlled, not uncontrolled.
    """
    policy = policies.get(container)
    if policy is None:
        policy = policies.get("*")
    if policy is None or policy.get("mode") == "Off":
        return False
    return (policy.get("controlledValues") or "RequestsAndLimits") == "RequestsAndLimits"


class DriftReport(NamedTuple):
    lines: list[str]
    # Containers whose live/template divergence is EXPECTED because an active
    # VPA owns their limits. Surfaced so a shrinking findings list cannot be
    # mistaken for a fixed cluster.
    skipped: int


def memory_limit_drift(pods: list[dict], workloads: list[dict],
                       vpas: list[dict] | None = None) -> DriftReport:
    """One line per container whose live memory limit differs from its template."""
    seen: dict[tuple[str, str, int | None, int], int] = {}
    vpa_policies = _vpa_limit_policies(vpas or [])
    skipped = 0
    for wl in workloads:
        meta = wl.get("metadata") or {}
        ns, name, kind = meta.get("namespace", ""), meta.get("name", "?"), wl.get("kind", "?")
        policies = vpa_policies.get((ns, kind, name), {})
        declared: dict = {}
        for c in _template_containers(wl):
            want = _quantity(((c.get("resources") or {}).get("limits") or {}).get("memory"))
            if want is None:
                continue
            if _limits_are_vpa_controlled(policies, c.get("name")):
                # The updater rewrites this limit at admission, so the template
                # figure is a starting point, not the value to hold pods to.
                skipped += 1
                continue
            declared[c.get("name")] = want
        if not declared:
            continue
        for pod in pods:
            if (pod.get("metadata") or {}).get("namespace", "") != ns or not _matches(wl, pod):
                continue
            for c in _containers(pod):
                want = declared.get(c.get("name"))
                if want is None:
                    continue
                live = _quantity(((c.get("resources") or {}).get("limits") or {}).get("memory"))
                if live == want:
                    continue
                key = (f"{ns}/{kind}/{name}", c.get("name", "?"), live, want)
                seen[key] = seen.get(key, 0) + 1
    out = []
    for (wlkey, cname, live, want), count in seen.items():
        live_txt = _human(live) if live is not None else "unset"
        out.append(
            f"  {wlkey}: container {cname!r} runs at limits.memory={live_txt} "
            f"but the template declares {_human(want)} ({count} pod(s))"
        )
    return DriftReport(sorted(out), skipped)


DRIFT_REMEDIATION = """
A mutating VPA applies at pod ADMISSION, so pods older than the commit keep the
limit they were admitted with. Restart the workload to pick up the declared one:
  kubectl -n <ns> rollout restart <kind>/<name>
""".rstrip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.exit(f"Failed to parse `kubectl get pods -o json` input: {exc}")
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        sys.exit("Input is not a pod list (expected `kubectl get pods -A -o json`)")

    # A single-type `kubectl get pods` strips `kind` from each item; asking for
    # several types keeps it. So an item without a kind is a pod.
    items = [i for i in items if isinstance(i, dict)]
    pods = [i for i in items if i.get("kind") in (None, "Pod")]
    workloads = [i for i in items if i.get("kind") in WORKLOAD_KINDS]
    vpas = [i for i in items if i.get("kind") == VPA_KIND]

    violations = live_cpu_limit_violations(pods)
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

    if not workloads:
        print(
            f"Live CPU-limit policy OK ({len(pods)} pods checked); memory-limit "
            "drift not checked — pass the workload templates too (kubectl get "
            "pods,deployments,statefulsets,daemonsets,verticalpodautoscalers "
            "-A -o json)"
        )
        return 0

    drift = memory_limit_drift(pods, workloads, vpas)
    if drift.lines:
        # Warning only: the exit code stays owned by the CPU-limit policy.
        print(
            "WARNING: live memory limits diverge from their workload templates — "
            "these pods were admitted before the current commit and a mutating VPA "
            "froze the old pair (docs/33 § Live drift):",
            file=sys.stderr,
        )
        print("\n".join(drift.lines), file=sys.stderr)
        print(DRIFT_REMEDIATION, file=sys.stderr)

    # The skip count keeps the VPA exclusion visible: without it, a check that
    # silently stopped seeing containers would read as a clean cluster.
    skipped = (f"; {drift.skipped} container(s) skipped (limits owned by an active VPA)"
               if drift.skipped else "")
    print(
        f"Live CPU-limit policy OK ({len(pods)} pods checked); "
        f"{len(drift.lines)} memory-limit divergence(s) across "
        f"{len(workloads)} workloads{skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
