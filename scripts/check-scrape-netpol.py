#!/usr/bin/env python3
"""Assert every scraped namespace admits Prometheus through its NetworkPolicies.

A namespace that carries ANY NetworkPolicy with `Ingress` in policyTypes is
ingress-default-deny for everything that policy set does not explicitly allow —
including the Prometheus scrape. Enabling a ServiceMonitor/PodMonitor without the
paired ingress allow leaves the pod healthy (kubelet probes originate in the host
netns and bypass the CNI policy chain) while the scrape is REJECTed, so the only
symptom is a `TargetDown` alert. That is exactly how the `reloader` PodMonitor
shipped broken in !180 and stayed broken for four days.

This guards that invariant in CI. It reads the rendered corpus `task flux:lint`
builds (`kustomize build | envsubst`, no `helm template`) on stdin and fails when
a scraped, ingress-restricted namespace has no ingress rule sourced from the
observability namespace.

A namespace counts as scraped when either:
  * a ServiceMonitor/PodMonitor targets it — its own namespace, or the namespaces
    named in `spec.namespaceSelector.matchNames`; or
  * a HelmRelease deploying into it enables a chart-native monitor
    (any `.../serviceMonitor.enabled: true` or `.../podMonitor.enabled: true` in
    `.spec.values`). Chart-rendered monitors never appear in the kustomize corpus,
    so matching on the HelmRelease values is the only way to see them — and it is
    the case that actually broke.

Deliberately NOT checked: the port. A chart-native monitor names a container/
service port (`port: http`) that only resolves once the chart is rendered, so a
port-level assertion would be enforceable for hand-written monitors and silently
vacuous for chart ones. Namespace-level reachability is the invariant that broke
and the one that can be checked uniformly.

Usage (wired into flux:lint, on the accumulated full corpus):
  kustomize build <path> | envsubst >> corpus
  python3 scripts/check-scrape-netpol.py < corpus
"""
from __future__ import annotations

import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

MONITOR_KINDS = {"ServiceMonitor", "PodMonitor"}
OBSERVABILITY_NS = "observability"
NS_NAME_LABEL = "kubernetes.io/metadata.name"

# Namespaces exempt from the invariant, "namespace": "reason". Empty by design —
# add an entry only with a written reason (e.g. a monitor that scrapes an
# out-of-cluster Endpoints object, where no in-namespace pod is ever the target).
EXEMPT_NAMESPACES: dict[str, str] = {}


def _selects_observability(peer: dict) -> bool:
    """True if a NetworkPolicy `from` peer matches the observability namespace."""
    nssel = peer.get("namespaceSelector")
    if nssel is None:
        return False
    labels = nssel.get("matchLabels") or {}
    if labels.get(NS_NAME_LABEL) == OBSERVABILITY_NS:
        return True
    for expr in nssel.get("matchExpressions") or []:
        if expr.get("key") != NS_NAME_LABEL:
            continue
        values = expr.get("values") or []
        if expr.get("operator") == "In" and OBSERVABILITY_NS in values:
            return True
    return False


def _policy_types(spec: dict) -> set[str]:
    """policyTypes, applying the API default (inferred from which rules exist)."""
    declared = spec.get("policyTypes")
    if declared:
        return {str(t) for t in declared}
    types = {"Ingress"}
    if spec.get("egress"):
        types.add("Egress")
    return types


def _find_monitor_toggles(node, path: str = "") -> list[str]:
    """Paths of truthy `serviceMonitor.enabled` / `podMonitor.enabled` in values.

    Case-insensitive: charts spell it both `serviceMonitor` (traefik, ESO) and
    `servicemonitor` (cert-manager).
    """
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else key
            if (
                str(key).lower() in ("servicemonitor", "podmonitor")
                and isinstance(value, dict)
                and value.get("enabled") is True
            ):
                hits.append(f"{child}.enabled")
            hits.extend(_find_monitor_toggles(value, child))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits.extend(_find_monitor_toggles(value, f"{path}[{i}]"))
    return hits


def _load(stream) -> list[dict]:
    docs: list[dict] = []
    try:
        for raw in yaml.safe_load_all(stream):
            if isinstance(raw, dict):
                if raw.get("kind") == "List" and isinstance(raw.get("items"), list):
                    docs.extend(i for i in raw["items"] if isinstance(i, dict))
                else:
                    docs.append(raw)
            elif isinstance(raw, list):
                docs.extend(i for i in raw if isinstance(i, dict))
    except yaml.YAMLError as exc:
        sys.exit(f"Failed to parse YAML input: {exc}")
    return docs


def analyze(docs: list[dict]) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """-> (scraped namespace -> reasons, ingress-restricted namespaces, allowed)."""
    scraped: dict[str, list[str]] = {}
    restricted: set[str] = set()
    allowed: set[str] = set()

    for doc in docs:
        kind = doc.get("kind")
        meta = doc.get("metadata") or {}
        ns = meta.get("namespace") or ""
        name = meta.get("name", "?")
        spec = doc.get("spec") or {}

        if kind in MONITOR_KINDS:
            selector = spec.get("namespaceSelector") or {}
            match_names = selector.get("matchNames") or []
            if match_names:
                targets = [str(n) for n in match_names]
            elif selector.get("any"):
                # Cluster-wide discovery: which namespaces actually hold a
                # matching Service is unknowable from the corpus, so it is not
                # attributable to any one namespace.
                continue
            else:
                # No namespaceSelector means the monitor's own namespace.
                targets = [ns]
            for target in targets:
                scraped.setdefault(target, []).append(f"{kind} {ns}/{name}")

        elif kind == "HelmRelease":
            target = spec.get("targetNamespace") or ns
            for hit in _find_monitor_toggles(spec.get("values") or {}, "values"):
                scraped.setdefault(target, []).append(
                    f"HelmRelease {ns}/{name} ({hit}: true)"
                )

        elif kind == "NetworkPolicy":
            if "Ingress" not in _policy_types(spec):
                continue
            pod_selector = spec.get("podSelector")
            namespace_wide = not pod_selector  # {} or absent selects every pod
            if namespace_wide:
                restricted.add(ns)
            for rule in spec.get("ingress") or []:
                peers = rule.get("from")
                if peers is None and namespace_wide:
                    # No `from` = allow every source, so the scrape gets through.
                    allowed.add(ns)
                    continue
                for peer in peers or []:
                    if _selects_observability(peer):
                        allowed.add(ns)

    return scraped, restricted, allowed


def main() -> int:
    docs = _load(sys.stdin)
    scraped, restricted, allowed = analyze(docs)

    violations: list[str] = []
    checked = 0
    for ns in sorted(scraped):
        if ns not in restricted or ns in EXEMPT_NAMESPACES:
            continue
        checked += 1
        if ns in allowed:
            continue
        reasons = ", ".join(sorted(set(scraped[ns])))
        violations.append(
            f"  {ns}: scraped ({reasons}) and ingress-restricted, but no "
            f"NetworkPolicy admits the {OBSERVABILITY_NS} namespace — the scrape "
            f"is REJECTed at the CNI and TargetDown fires. Add an "
            f"`allow-metrics-ingress` policy (namespaceSelector "
            f"{NS_NAME_LABEL}: {OBSERVABILITY_NS}) on the monitored port."
        )

    if violations:
        print(
            "Scrape/NetworkPolicy invariant violated — monitored namespaces that "
            "block Prometheus:",
            file=sys.stderr,
        )
        print("\n".join(violations), file=sys.stderr)
        return 1

    print(
        f"Scrape/NetworkPolicy invariant OK ({checked} scraped ingress-restricted "
        f"namespaces checked, {len(scraped)} scraped namespaces seen)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
