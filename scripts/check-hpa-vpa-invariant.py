#!/usr/bin/env python3
"""Assert no workload has both an HPA and a CPU-controlling VPA.

A HorizontalPodAutoscaler and a VerticalPodAutoscaler must never drive the same
resource on the same workload: the HPA scales replica count on (typically) CPU
utilization while the VPA updater evicts pods to resize CPU requests — they
fight, and pods thrash. The rule in this repo (docs/33-autoscaling.md) is that
any workload that gains an HPA carries a memory-only VPA
(controlledResources: [memory], no cpu).

This guards that invariant in CI. It reads a stream of rendered Kubernetes
manifests on stdin (the corpus `task flux:lint` builds with
`kustomize build | envsubst` — no `helm template`), then for every HPA finds the
VPAs targeting the same (namespace, kind, name) and fails if any of them controls
a resource the HPA also scales. Because the corpus is kustomize-only, this covers
STANDALONE HPAs/VPAs in the kustomize-build stream.

Chart-native HPAs live inside HelmReleases (Traefik, authentik, onepassword-
connect) and are NOT expanded into the kustomize corpus, so the generic join
above cannot see them. Their paired VPAs ARE in the corpus, though, so with
--require-chart-native-vpas (passed by flux:lint, which renders the *full*
corpus) this also statically asserts each CHART_NATIVE_HPA_TARGETS workload has a
mutating (Auto/Initial) VPA that excludes cpu — an Off/recommend-only VPA does
not count, since it never actually right-sizes. Keep that list in sync with the HelmReleases that set
autoscaling/HPA. The flag is off by default so unit tests can exercise the
generic join on minimal streams.

With --require-chart-native-vpas it ALSO asserts the repo-wide "no CPU limits"
policy (docs/33-autoscaling.md): CPU is compressible, so a CPU limit only adds
CFS throttling that hurts latency and inflates the CPU% a CPU-based HPA reads.
The check covers rendered pod specs, HelmRelease `.spec.values`, and CPU limits
embedded in config-file block strings inside those values (the gitlab-runner
`runners.config` TOML, which is where every CI JOB pod's limits are declared —
those pods never appear in any rendered manifest).

Unconditionally (no flag needed, since it reads only the corpus) it also asserts
that no container sets requests.memory == limits.memory while a mutating VPA
controls its memory with the default controlledValues: that ratio makes every
request revision rewrite the limit 1:1, leaving zero burst headroom — the
prowlarr OOMKills, and authentik-server's live 878Mi request == 878Mi limit.

Limitation: a CPU limit baked into a third-party chart's subchart defaults that
is NOT overridden in `.spec.values` is invisible here (the corpus is kustomize-
only, no `helm template`). validate-helm-values.py renders the value-heavy
releases (see RELEASES there) via `helm template` and reuses
_cpu_limit_violations to catch those; other charts rely on the live pod-spec
audit in docs/33-autoscaling.md. The memory-ratio check has the same blind spot
for chart-rendered pod specs (authentik's own resources live in a HelmRelease),
which is why VPARecommendationExceedsLimit exists as the runtime backstop.

Usage (wired into flux:lint, on the accumulated full corpus):
  kustomize build <path> | envsubst >> corpus
  python3 scripts/check-hpa-vpa-invariant.py --require-chart-native-vpas < corpus
"""
from __future__ import annotations

import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

HPA_KIND = "HorizontalPodAutoscaler"
VPA_KIND = "VerticalPodAutoscaler"

# Workloads whose HPA is chart-native (inside a HelmRelease) and therefore absent
# from the kustomize corpus. Each scales on CPU via its chart's HPA, so its VPA
# (which IS in the corpus) must exist and must NOT control cpu. Keep in sync with:
#   controllers/traefik/release.yaml          (autoscaling.enabled)
#   apps/authentik/release.yaml               (autoscaling)
#   controllers/onepassword-connect/release.yaml (connect.hpa)
CHART_NATIVE_HPA_TARGETS: dict[tuple[str, str, str], str] = {
    ("traefik", "Deployment", "traefik"): "traefik chart autoscaling.enabled",
    ("authentik", "Deployment", "authentik-server"): "authentik chart autoscaling",
    ("external-secrets", "Deployment", "onepassword-connect"): "connect.hpa",
}


def _target_key(ns: str, ref: dict) -> tuple[str, str, str]:
    """(namespace, target-kind, target-name) — the join key between HPA and VPA."""
    return (ns or "default", ref.get("kind", ""), ref.get("name", ""))


def _hpa_metrics(spec: dict) -> set[str]:
    """Resource names an HPA scales on (cpu/memory).

    An HPA with no `metrics` field (or an empty list) defaults to CPU 80% under
    autoscaling/v2, so that case yields {"cpu"}. But if `metrics` IS present and
    holds only non-Resource entries (External/Object/Pods), the HPA scales on
    nothing the VPA touches — return the empty set so no phantom CPU is assumed.
    """
    metrics = spec.get("metrics") or []
    if not metrics:
        return {"cpu"}
    resources: set[str] = set()
    for metric in metrics:
        mtype = metric.get("type")
        if mtype == "Resource":
            name = (metric.get("resource") or {}).get("name")
        elif mtype == "ContainerResource":
            # Per-container CPU/memory target — still a cpu/memory HPA.
            name = (metric.get("containerResource") or {}).get("name")
        else:
            continue
        if name:
            resources.add(str(name).lower())
    return resources


def _vpa_resources(spec: dict) -> set[str]:
    """Resources a VPA controls. Default (no controlledResources) is cpu+memory."""
    controlled: set[str] = set()
    policies = (spec.get("resourcePolicy") or {}).get("containerPolicies", []) or []
    if not policies:
        # No policy means the VPA controls everything by default.
        return {"cpu", "memory"}
    for p in policies:
        if (p.get("mode") or "").lower() == "off":
            # Per-container Off policy is recommend-only — not mutating.
            continue
        cr = p.get("controlledResources")
        if cr is None:
            controlled |= {"cpu", "memory"}
        else:
            controlled |= {str(r).lower() for r in cr}
    # containerPolicies scope by containerName: a policy naming one container
    # says nothing about the pod's other containers, which the VPA still
    # controls with the default (cpu+memory). Without a '*' catch-all policy,
    # fail closed and count those defaults so e.g. a named-container
    # memory-only policy cannot hide a real CPU/HPA clash on a sidecar.
    if not any(p.get("containerName") == "*" for p in policies):
        controlled |= {"cpu", "memory"}
    return controlled


# "no CPU limits" policy (docs/33-autoscaling.md)
POD_SPEC_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "Pod"}

# Workloads intentionally permitted a CPU limit despite the repo-wide policy.
# Empty by design; add a "namespace/Kind/name" string here only with a reason.
# SHARED: validate-helm-values.py imports this set (and _cpu_limit_violations)
# so the kustomize-side and helm-rendered-side checks honor one allowlist.
CPU_LIMIT_ALLOWLIST: set[str] = set()


def _containers_of(doc: dict) -> list[dict]:
    """All containers (init + regular + ephemeral) of a pod-spec workload, else []."""
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        pod = spec
    elif kind == "CronJob":
        pod = ((((spec.get("jobTemplate") or {}).get("spec") or {})
                .get("template") or {}).get("spec") or {})
    elif kind in POD_SPEC_KINDS:
        pod = (spec.get("template") or {}).get("spec") or {}
    else:
        return []
    out: list[dict] = []
    for key in ("initContainers", "containers", "ephemeralContainers"):
        v = pod.get(key)
        if isinstance(v, list):
            out.extend(c for c in v if isinstance(c, dict))
    return out


def _find_values_cpu_limits(node, path: str = "") -> list[str]:
    """Recursively find `limits.cpu` inside a HelmRelease `.spec.values` tree."""
    hits: list[str] = []
    if isinstance(node, dict):
        lim = node.get("limits")
        # `cpu: null`/`""` clears a chart default rather than setting a limit
        # (k8s treats it as "no CPU limit"), so don't flag a merely-present key.
        if isinstance(lim, dict) and lim.get("cpu") not in (None, ""):
            key = f"{path}.limits.cpu" if path else "limits.cpu"
            hits.append(f"{key}={lim.get('cpu')}")
        for k, v in node.items():
            if k != "limits":
                hits.extend(_find_values_cpu_limits(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits.extend(_find_values_cpu_limits(v, f"{path}[{i}]"))
    return hits


# A CPU limit can also hide inside an embedded config FILE carried as a YAML
# block string in `.spec.values` — the gitlab-runner charts put the whole
# `config.toml` there, so `cpu_limit`/`service_cpu_limit`/`helper_cpu_limit`
# (the limits every CI JOB pod is created with) are structurally invisible to
# the dict walk above. Anchored at line start so a `# cpu_limit ...` comment
# does not match, and the key must end at `=` so the
# `*_overwrite_max_allowed` ceilings (which grant an override, not a limit)
# are not conflated with setting one.
_CONFIG_CPU_LIMIT_RE = re.compile(
    r"^[ \t]*((?:service_|helper_)?cpu_limit)[ \t]*=[ \t]*(\S+)", re.MULTILINE
)


def _find_config_cpu_limits(node, path: str = "") -> list[str]:
    """Find CPU limits inside embedded config-file strings in a values tree."""
    hits: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            hits.extend(_find_config_cpu_limits(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits.extend(_find_config_cpu_limits(v, f"{path}[{i}]"))
    elif isinstance(node, str):
        for key, value in _CONFIG_CPU_LIMIT_RE.findall(node):
            # `cpu_limit = ""` clears the chart default, same as limits.cpu: ""
            if value.strip('"\'') == "":
                continue
            hits.append(f"{path}:{key}={value}")
    return hits


def _cpu_limit_violations(docs: list[dict]) -> list[str]:
    """Flag any pod-spec container or HelmRelease values that set a CPU limit."""
    out: list[str] = []
    for d in docs:
        kind = d.get("kind")
        meta = d.get("metadata") or {}
        wlkey = f"{meta.get('namespace', '')}/{kind}/{meta.get('name', '?')}"
        if wlkey in CPU_LIMIT_ALLOWLIST:
            continue
        if kind == "HelmRelease":
            values = (d.get("spec") or {}).get("values") or {}
            for hit in _find_values_cpu_limits(values, "values"):
                out.append(f"  {wlkey}: HelmRelease sets a CPU limit ({hit})")
            for hit in _find_config_cpu_limits(values, "values"):
                out.append(
                    f"  {wlkey}: HelmRelease embeds a CPU limit in a config "
                    f"string ({hit})"
                )
        else:
            for c in _containers_of(d):
                lim = (c.get("resources") or {}).get("limits") or {}
                if lim.get("cpu") not in (None, ""):
                    out.append(
                        f"  {wlkey}: container {c.get('name', '?')!r} sets "
                        f"limits.cpu={lim.get('cpu')}"
                    )
    return out


# --- memory request == limit under a limit-rewriting VPA ----------------------
# The VPA's default controlledValues is RequestsAndLimits, which preserves the
# manifest's request:limit RATIO. At a 1:1 memory ratio that means every request
# revision rewrites the limit down with it, so the container has permanently zero
# burst headroom — observed as OOMKills on prowlarr, and as authentik-server's
# live 878Mi request == 878Mi limit against an 810Mi working set. The fix is
# either controlledValues: RequestsOnly (prowlarr, authentik) or a manifest ratio
# above 1:1.


def _policy_for(spec: dict, container: str) -> dict:
    """The containerPolicy a VPA applies to `container` (first match wins).

    Returns {} when nothing matches. VPA's own GetContainerResourcePolicy
    returns nil there, but nil does NOT mean "not autoscaled" — it means the
    container falls through to the defaults (updateMode Auto,
    controlledValues RequestsAndLimits), i.e. exactly the 1:1 limit-rewrite trap
    this rule exists to catch. Returning None here used to make the caller skip
    the container: silent for any VPA whose containerPolicies list omits a "*"
    entry and does not name the container.
    """
    policies = (spec.get("resourcePolicy") or {}).get("containerPolicies", []) or []
    for p in policies:
        if p.get("containerName") == container:
            return p
    for p in policies:
        if p.get("containerName") == "*":
            return p
    return {}


def _memory_ratio_violations(docs: list[dict]) -> list[str]:
    """Flag 1:1 memory containers whose VPA would rewrite the limit."""
    vpas: dict[tuple[str, str, str], list[tuple[str, dict]]] = {}
    for d in docs:
        if d.get("kind") != VPA_KIND:
            continue
        meta = d.get("metadata") or {}
        spec = d.get("spec") or {}
        ref = spec.get("targetRef") or {}
        if not ref.get("name"):
            continue
        if str((spec.get("updatePolicy") or {}).get("updateMode", "Auto")).lower() == "off":
            continue  # recommend-only: never mutates a pod, so no rewrite
        key = _target_key(meta.get("namespace", ""), ref)
        vpas.setdefault(key, []).append((meta.get("name", "?"), spec))

    out: list[str] = []
    for d in docs:
        kind = d.get("kind")
        if kind not in POD_SPEC_KINDS:
            continue
        meta = d.get("metadata") or {}
        key = _target_key(meta.get("namespace", ""), {"kind": kind, "name": meta.get("name", "")})
        if key not in vpas:
            continue
        for c in _containers_of(d):
            res = c.get("resources") or {}
            req = (res.get("requests") or {}).get("memory")
            lim = (res.get("limits") or {}).get("memory")
            if req is None or lim is None or str(req) != str(lim):
                continue
            cname = c.get("name", "?")
            for vpa_name, spec in vpas[key]:
                policy = _policy_for(spec, cname)
                if (policy.get("mode") or "").lower() == "off":
                    continue
                controlled = policy.get("controlledResources")
                if controlled is not None and "memory" not in {
                    str(r).lower() for r in controlled
                }:
                    continue
                if (policy.get("controlledValues") or "") == "RequestsOnly":
                    continue
                out.append(
                    f"  {key[0]}/{kind}/{key[2]}: container {cname!r} sets "
                    f"requests.memory == limits.memory ({req}) while VPA "
                    f"{vpa_name!r} controls memory with the default "
                    f"controlledValues — every request revision rewrites the "
                    f"limit 1:1, leaving zero burst headroom. Set "
                    f"controlledValues: RequestsOnly on that policy, or raise "
                    f"the limit above the request."
                )
    return out


def main() -> int:
    # safe_load_all is lazy, so parse errors surface during iteration — wrap the
    # loop (not just the generator) so a malformed stream exits cleanly. Also
    # flatten `kind: List` and top-level YAML lists so wrapped resources count.
    docs: list[dict] = []
    try:
        for raw in yaml.safe_load_all(sys.stdin):
            if isinstance(raw, dict):
                if raw.get("kind") == "List" and isinstance(raw.get("items"), list):
                    docs.extend(i for i in raw["items"] if isinstance(i, dict))
                else:
                    docs.append(raw)
            elif isinstance(raw, list):
                docs.extend(i for i in raw if isinstance(i, dict))
    except yaml.YAMLError as exc:
        sys.exit(f"Failed to parse YAML input: {exc}")

    # Multiple HPAs or VPAs can target one workload, so aggregate per key rather
    # than last-wins: union the resource sets so a memory-only VPA can never mask
    # a cpu-controlling one on the same target.
    hpas: dict[tuple[str, str, str], set[str]] = {}
    vpas: dict[tuple[str, str, str], set[str]] = {}  # mutating VPAs only (Off skipped)
    vpa_names: dict[tuple[str, str, str], list[str]] = {}

    for d in docs:
        kind = d.get("kind")
        meta = d.get("metadata") or {}
        ns = meta.get("namespace", "")
        spec = d.get("spec") or {}
        if kind == HPA_KIND:
            ref = spec.get("scaleTargetRef") or {}
            if not ref.get("name"):
                continue
            key = _target_key(ns, ref)
            hpas[key] = hpas.get(key, set()) | _hpa_metrics(spec)
        elif kind == VPA_KIND:
            ref = spec.get("targetRef") or {}
            if not ref.get("name"):
                continue
            key = _target_key(ns, ref)
            # updateMode "Off" is recommend-only: it never mutates pods, so it
            # cannot fight an HPA (this is how coredns pairs a min==max HPA pin
            # with a right-sizing VPA). Only mutating modes can conflict.
            mode = (spec.get("updatePolicy") or {}).get("updateMode", "Auto")
            if str(mode).lower() == "off":
                continue
            vpas[key] = vpas.get(key, set()) | _vpa_resources(spec)
            vpa_names.setdefault(key, []).append(meta.get("name", "?"))

    violations: list[str] = []
    for key in sorted(hpas):
        hpa_res = hpas[key]
        if key not in vpas:
            continue
        vpa_res = vpas[key]
        # hpa_res already encodes the autoscaling/v2 default: it is {"cpu"} when
        # no metrics were declared and empty when metrics held only non-Resource
        # (External/Object/Pods) entries, which can't clash with a VPA.
        clash = hpa_res & vpa_res
        if clash:
            ns, tkind, tname = key
            names = ", ".join(repr(n) for n in sorted(vpa_names[key]))
            violations.append(
                f"  {ns}/{tkind}/{tname}: HPA scales {sorted(hpa_res)} "
                f"but VPA(s) {names} also control {sorted(clash)} "
                f"(set the VPA to controlledResources excluding {sorted(clash)})"
            )

    # Static check for chart-native HPAs (their HPA isn't in the corpus, but their
    # VPA is). Opt-in: only meaningful on the full rendered corpus flux:lint builds.
    if "--require-chart-native-vpas" in sys.argv:
        for key, source in sorted(CHART_NATIVE_HPA_TARGETS.items()):
            ns, tkind, tname = key
            # `not vpas.get(key)` (vs `key not in vpas`) also catches a mutating
            # VPA whose every containerPolicy is mode:Off — it registers with an
            # empty controlled set but right-sizes nothing, so it must not count.
            if not vpas.get(key):
                violations.append(
                    f"  {ns}/{tkind}/{tname}: chart-native HPA ({source}) has no "
                    f"mutating (Auto/Initial) VPA in the rendered corpus — add a "
                    f"memory-only VPA (controlledResources: [memory]) so CPU stays "
                    f"HPA-owned and memory is actually right-sized (an Off VPA "
                    f"recommends but never resizes, so it does not satisfy this)"
                )
            elif "cpu" in vpas.get(key, set()):
                names = ", ".join(repr(n) for n in sorted(vpa_names.get(key, [])))
                violations.append(
                    f"  {ns}/{tkind}/{tname}: chart-native HPA ({source}) scales cpu "
                    f"but mutating VPA(s) {names} also control cpu — set "
                    f"controlledResources to exclude cpu (memory-only)"
                )

    cpu_violations = (
        _cpu_limit_violations(docs)
        if "--require-chart-native-vpas" in sys.argv else []
    )
    ratio_violations = _memory_ratio_violations(docs)

    failed = False
    if violations:
        print("HPA/VPA invariant violated — same resource driven by both:", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        failed = True
    if cpu_violations:
        print(
            "CPU-limit policy violated — pods/HelmReleases must not set a CPU limit "
            "(compressible resource; CFS throttling hurts latency and distorts "
            "CPU-based HPAs — see docs/33-autoscaling.md). Offenders:",
            file=sys.stderr,
        )
        print("\n".join(cpu_violations), file=sys.stderr)
        failed = True
    if ratio_violations:
        print(
            "Memory request == limit under a limit-rewriting VPA (docs/33 — the "
            "prowlarr/authentik-server trap). Offenders:",
            file=sys.stderr,
        )
        print("\n".join(ratio_violations), file=sys.stderr)
        failed = True
    if failed:
        return 1

    print(
        f"HPA/VPA invariant OK ({len(hpas)} HPAs, {len(vpas)} VPAs checked"
        + (f", {len(CHART_NATIVE_HPA_TARGETS)} chart-native targets asserted"
           ", CPU-limit policy OK"
           if "--require-chart-native-vpas" in sys.argv else "")
        + ")"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
