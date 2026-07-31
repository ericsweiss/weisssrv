#!/usr/bin/env python3
"""Assert every ClusterSecretStore is namespace-scoped and covers its consumers.

A `ClusterSecretStore` with no `spec.conditions` is referenceable from EVERY
namespace: any principal that can create an ExternalSecret anywhere can mint any
item in the backing vault. The scoping mechanism is native to ESO (conditions
with `namespaces` / `namespaceRegexes` / `namespaceSelector`), and this check
keeps it honest in both directions:

  * every ClusterSecretStore in the tree declares conditions (an unscoped store
    fails), and
  * every ExternalSecret (and every namespace a ClusterExternalSecret fans out
    to) sits in a namespace those conditions actually admit — so adding an app
    with an ExternalSecret without widening the store fails the build instead of
    failing silently at runtime with a stale Secret.

Condition matching mirrors ESO: a namespace is admitted when ANY condition
matches, and a condition matches on an exact `namespaces` entry, a
`namespaceRegexes` match, or a `namespaceSelector` label match.

Usage (wired into flux:lint, on the accumulated full corpus):
  kustomize build <path> | envsubst >> corpus
  python3 scripts/check-secretstore-scope.py < corpus
"""
from __future__ import annotations

import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

CLUSTER_STORE_KIND = "ClusterSecretStore"


def _selector_matches(selector: dict, labels: dict) -> bool:
    """Kubernetes labelSelector semantics (matchLabels + matchExpressions, ANDed)."""
    if selector is None:
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expr in selector.get("matchExpressions") or []:
        key = expr.get("key")
        op = expr.get("operator")
        values = expr.get("values") or []
        present = key in labels
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and labels.get(key) in values:
            return False
        if op == "Exists" and not present:
            return False
        if op == "DoesNotExist" and present:
            return False
    return True


def _condition_admits(condition: dict, namespace: str, labels: dict) -> bool:
    if namespace in (condition.get("namespaces") or []):
        return True
    for pattern in condition.get("namespaceRegexes") or []:
        if re.search(pattern, namespace):
            return True
    selector = condition.get("namespaceSelector")
    if selector is not None and _selector_matches(selector, labels):
        return True
    return False


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


def main() -> int:
    docs = _load(sys.stdin)

    ns_labels: dict[str, dict] = {}
    stores: dict[str, list] = {}
    # (store, namespace, describing the consumer) tuples to validate.
    consumers: list[tuple[str, str, str]] = []
    cluster_external_secrets: list[dict] = []

    for doc in docs:
        kind = doc.get("kind")
        meta = doc.get("metadata") or {}
        name = meta.get("name", "?")
        spec = doc.get("spec") or {}

        if kind == "Namespace":
            ns_labels[name] = meta.get("labels") or {}
        elif kind == CLUSTER_STORE_KIND:
            stores[name] = spec.get("conditions")
        elif kind == "ExternalSecret":
            ref = spec.get("secretStoreRef") or {}
            if ref.get("kind") == CLUSTER_STORE_KIND:
                ns = meta.get("namespace") or "default"
                consumers.append((ref.get("name", "?"), ns, f"ExternalSecret {ns}/{name}"))
        elif kind == "ClusterExternalSecret":
            cluster_external_secrets.append(doc)

    violations: list[str] = []

    for name, conditions in sorted(stores.items()):
        if not conditions:
            violations.append(
                f"  ClusterSecretStore {name}: no spec.conditions — referenceable "
                f"from every namespace, so any ExternalSecret in the cluster can "
                f"read the whole backing vault. Add conditions scoping it to the "
                f"namespaces that legitimately consume it."
            )

    # A ClusterExternalSecret creates ExternalSecrets in every namespace its
    # selectors match, so those namespaces need the same admission.
    for ces in cluster_external_secrets:
        meta = ces.get("metadata") or {}
        spec = ces.get("spec") or {}
        ref = ((spec.get("externalSecretSpec") or {}).get("secretStoreRef")) or {}
        if ref.get("kind") != CLUSTER_STORE_KIND:
            continue
        selectors = spec.get("namespaceSelectors")
        if selectors is None:
            single = spec.get("namespaceSelector")
            selectors = [single] if single else []
        for ns, labels in ns_labels.items():
            if any(_selector_matches(sel, labels) for sel in selectors):
                consumers.append(
                    (
                        ref.get("name", "?"),
                        ns,
                        f"ClusterExternalSecret {meta.get('name', '?')} -> {ns}",
                    )
                )

    unknown: set[str] = set()
    for store, namespace, description in consumers:
        if store not in stores:
            unknown.add(store)
            continue
        conditions = stores[store] or []
        if not conditions:
            continue  # already reported as unscoped above
        labels = ns_labels.get(namespace, {})
        if not any(_condition_admits(c, namespace, labels) for c in conditions):
            violations.append(
                f"  {description}: namespace {namespace!r} is not admitted by "
                f"ClusterSecretStore {store}'s spec.conditions — ESO will refuse "
                f"the fetch and the Secret will go stale. Add the namespace to the "
                f"store's conditions (or point the app at a scoped store)."
            )

    if unknown:
        print(
            "note: ClusterSecretStore(s) referenced but not defined in this corpus, "
            "so their scope was not checked: " + ", ".join(sorted(unknown)),
            file=sys.stderr,
        )

    if violations:
        print(
            "ClusterSecretStore scoping invariant violated:", file=sys.stderr
        )
        print("\n".join(sorted(set(violations))), file=sys.stderr)
        return 1

    print(
        f"ClusterSecretStore scoping OK ({len(stores)} cluster stores, "
        f"{len(consumers)} namespace consumers checked)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
