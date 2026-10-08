#!/usr/bin/env python3
"""Assert every tenant wiring file carries the confinement it is supposed to.
Each omitted piece fails OPEN: the Pod Security labels, the namespace caps, the
ServiceAccount and RoleBindings, the store conditions, the Kustomization wiring.
"""
from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path

from script_loader import load_path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

TENANTS_DIR = "kubernetes/clusters/weisssrv/tenants"
AGGREGATOR = "kustomization.yaml"
TENANT_GLOBS = ("*.yaml", "*.yml")
# Applied unconditionally and bound by every tenant, so it is not a tenant file.
SHARED = {"tenant-crd-editor.yaml"}
FLUX_NAMESPACE = "flux-system"
STORE_KIND = "ClusterSecretStore"
SCOPE_GATE = Path(__file__).resolve().parent / "check-secretstore-scope.py"
REQUIRED_DEPENDENCY = "infrastructure-configs"
SOURCE_KIND = "GitRepository"
REQUIRED_ROLES = ("admin", "tenant-crd-editor")
REQUIRED_NS_LABELS = (
    "fluxcd.io/tenant",
    "pod-security.kubernetes.io/enforce",
    "pod-security.kubernetes.io/warn",
    "pod-security.kubernetes.io/audit",
)
# The namespace ceilings the onboarding README ships: `admin` places none.
REQUIRED_CAPS = ("ResourceQuota", "LimitRange")
# Cluster-scoped kinds a tenant file may carry. Everything else is namespaced,
# and the tenants aggregator sets no namespace of its own.
CLUSTER_SCOPED = frozenset(
    {
        "ClusterExternalSecret",
        "ClusterRole",
        "ClusterRoleBinding",
        "ClusterSecretStore",
        "Namespace",
    }
)


class Vacuous(Exception):
    """The gate could not inspect its subject — exit 2, never a silent pass."""


def tenant_files(root: Path) -> list[Path]:
    """Tenant wiring files on disk, either YAML suffix.

    Flux applies a listed `*.yml` too, so a single-suffix glob would leave its
    confinement unchecked.
    """
    base = root / TENANTS_DIR
    if not base.is_dir():
        raise Vacuous(f"{TENANTS_DIR} is not a directory")
    return sorted(
        {
            p
            for pattern in TENANT_GLOBS
            for p in base.glob(pattern)
            if p.name != AGGREGATOR and p.name not in SHARED
        }
    )


def listed_resources(root: Path) -> set[str]:
    """Resource entries, normalised. Kustomize accepts `./name.yaml`, so the raw
    spelling would read as a different file from the one on disk."""
    rel = f"{TENANTS_DIR}/{AGGREGATOR}"
    try:
        doc = yaml.safe_load((root / TENANTS_DIR / AGGREGATOR).read_text()) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise Vacuous(f"{rel} could not be read ({exc.__class__.__name__}: {exc})") from exc
    return {posixpath.normpath(str(r)) for r in (doc.get("resources") or [])}


def _of_kind(docs: list, kind: str) -> list[dict]:
    return [d for d in docs if isinstance(d, dict) and d.get("kind") == kind]


def _condition_admits():
    """ESO condition matching, loaded from the corpus gate that owns it."""
    module = load_path(SCOPE_GATE)
    matcher = getattr(module, "_condition_admits", None)
    if matcher is None:
        raise Vacuous(f"{SCOPE_GATE.name} no longer exports the ESO condition matcher")
    return matcher


def _store_problems(docs: list, rel: str, namespace, labels: dict) -> list[str]:
    problems = []
    admits = None
    for store in _of_kind(docs, STORE_KIND):
        name = ((store.get("metadata") or {}).get("name")) or "?"
        conditions = ((store.get("spec") or {}).get("conditions")) or []
        if not conditions:
            problems.append(
                f"{rel}: {STORE_KIND} {name} has no spec.conditions — a cluster-scoped "
                "store is referenceable from every namespace, so any ExternalSecret in "
                "the cluster can read its vault"
            )
            continue
        if not namespace:
            continue
        if admits is None:
            admits = _condition_admits()
        if not any(admits(c, namespace, labels) for c in conditions):
            problems.append(
                f"{rel}: {STORE_KIND} {name} conditions do not admit the tenant "
                f"namespace {namespace} — its ExternalSecrets never sync"
            )
        wider = sorted(
            {str(n) for c in conditions for n in (c.get("namespaces") or [])} - {namespace}
        )
        loose = sorted(
            {
                key
                for c in conditions
                for key in ("namespaceRegexes", "namespaceSelector")
                if c.get(key) is not None
            }
        )
        if wider or loose:
            reach = ", ".join(wider + loose)
            problems.append(
                f"{rel}: {STORE_KIND} {name} reaches past the tenant namespace "
                f"{namespace} ({reach}) — a tenant owns one namespace, so its store "
                "is scoped to that namespace alone"
            )
    return problems


def _cap_problems(docs: list, rel: str) -> list[str]:
    """The namespace ceilings, and the CPU limit a LimitRange must not create.

    The apiserver copies a `max` entry with no matching `default` into
    `default`, so `max.cpu` hands every tenant container a CPU limit.
    """
    problems = []
    for kind in REQUIRED_CAPS:
        if not _of_kind(docs, kind):
            problems.append(
                f"{rel}: declares no {kind} — `admin` places no ceiling on what "
                "the tenant may request, so one pod can exhaust a node"
            )
    for limit_range in _of_kind(docs, "LimitRange"):
        name = ((limit_range.get("metadata") or {}).get("name")) or "?"
        for entry in ((limit_range.get("spec") or {}).get("limits") or []):
            if not isinstance(entry, dict) or entry.get("type") != "Container":
                continue
            cpu_keys = [k for k in ("default", "max") if "cpu" in (entry.get(k) or {})]
            if cpu_keys:
                problems.append(
                    f"{rel}: LimitRange {name} sets cpu under "
                    f"{', '.join(cpu_keys)} — every tenant container would be "
                    "admitted with a CPU limit and CFS-throttled"
                )
    return problems


def _namespace_problems(docs: list, rel: str, namespace) -> list[str]:
    """Every namespaced document names the tenant namespace, or flux-system.

    The tenants tree is a Kustomize aggregator with no `namespace:`, so an
    omitted one lands the document in the cluster's `default` namespace.
    """
    problems = []
    for doc in docs:
        kind = doc.get("kind")
        if kind in CLUSTER_SCOPED:
            continue
        meta = doc.get("metadata") or {}
        where = f"{kind}/{meta.get('name', '?')}"
        ns = meta.get("namespace")
        if not ns:
            problems.append(
                f"{rel}: {where} has no metadata.namespace — the tenants "
                "aggregator sets none, so it lands in `default`"
            )
        elif namespace and ns not in (namespace, FLUX_NAMESPACE):
            problems.append(
                f"{rel}: {where} is in namespace {ns!r}, neither the tenant "
                f"namespace {namespace!r} nor {FLUX_NAMESPACE}"
            )
    return problems


def check_file(path: Path, rel: str, listed: set[str],
               listed_as: str | None = None, root: Path | None = None) -> list[str]:
    problems = []
    try:
        docs = [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        return [f"{rel}: unparseable YAML ({exc.__class__.__name__})"]

    if (listed_as or path.name) not in listed:
        problems.append(
            f"{rel}: not listed in {AGGREGATOR} resources — Kustomize does not "
            "auto-discover, so this tenant is never applied"
        )

    namespaces = _of_kind(docs, "Namespace")
    namespace = None
    labels: dict = {}
    if not namespaces:
        problems.append(f"{rel}: declares no Namespace")
    else:
        namespace = (namespaces[0].get("metadata") or {}).get("name")
        labels = (namespaces[0].get("metadata") or {}).get("labels") or {}
        for label in REQUIRED_NS_LABELS:
            if label not in labels:
                problems.append(f"{rel}: namespace {namespace} has no {label} label")

    problems += _cap_problems(docs, rel)
    problems += _namespace_problems(docs, rel, namespace)
    problems += _store_problems(docs, rel, namespace, labels)

    kustomizations = _of_kind(docs, "Kustomization")
    kustomizations = [k for k in kustomizations if "kustomize.toolkit" in str(k.get("apiVersion"))]
    if not kustomizations:
        problems.append(f"{rel}: declares no Flux Kustomization")
        return problems
    spec = kustomizations[0].get("spec") or {}

    service_account = spec.get("serviceAccountName")
    if not service_account:
        problems.append(
            f"{rel}: Kustomization has no serviceAccountName — it would reconcile "
            "as kustomize-controller's own cluster-admin"
        )
    if spec.get("prune") is not True:
        problems.append(f"{rel}: Kustomization does not set prune: true")
    target = spec.get("targetNamespace")
    if not target:
        problems.append(f"{rel}: Kustomization has no targetNamespace")
    elif namespace and target != namespace:
        problems.append(
            f"{rel}: Kustomization targetNamespace {target!r} is not the tenant "
            f"namespace {namespace!r}"
        )
    depends = {str((d or {}).get("name")) for d in (spec.get("dependsOn") or [])}
    if REQUIRED_DEPENDENCY not in depends:
        problems.append(
            f"{rel}: Kustomization does not dependOn {REQUIRED_DEPENDENCY} — on a "
            "fresh bootstrap it applies before the platform CRDs exist"
        )
    problems += _source_problems(docs, rel, spec)
    problems += _path_problems(rel, spec, root)

    if service_account:
        accounts = {
            (m.get("name"), m.get("namespace"))
            for m in ((d.get("metadata") or {}) for d in _of_kind(docs, "ServiceAccount"))
        }
        if (service_account, FLUX_NAMESPACE) not in accounts:
            problems.append(
                f"{rel}: no ServiceAccount {service_account} in {FLUX_NAMESPACE} — "
                "kustomize-controller impersonates it from its own namespace"
            )
        problems += _rolebinding_problems(docs, rel, service_account, namespace)
    return problems


def _source_problems(docs: list, rel: str, spec: dict) -> list[str]:
    """The Kustomization must reconcile the tenant's own repo.

    A sourceRef naming the cluster's own GitRepository reconciles this repo's
    manifests into the tenant namespace under the tenant ServiceAccount.
    """
    source = spec.get("sourceRef") or {}
    name = source.get("name")
    if not name:
        return [
            f"{rel}: Kustomization has no sourceRef.name — it reconciles no "
            "tenant repository"
        ]
    if source.get("kind") != SOURCE_KIND:
        return [
            f"{rel}: Kustomization sourceRef.kind is {source.get('kind')!r}, not "
            f"{SOURCE_KIND}"
        ]
    namespace = source.get("namespace") or FLUX_NAMESPACE
    if namespace != FLUX_NAMESPACE:
        return [
            f"{rel}: Kustomization sourceRef.namespace is {namespace!r}; the "
            f"tenant source lives in {FLUX_NAMESPACE} beside the Kustomization"
        ]
    sources = {
        ((d.get("metadata") or {}).get("name"),
         (d.get("metadata") or {}).get("namespace"))
        for d in _of_kind(docs, SOURCE_KIND)
    }
    if (name, FLUX_NAMESPACE) not in sources:
        return [
            f"{rel}: no {SOURCE_KIND} {name} in {FLUX_NAMESPACE} — the tenant's "
            "own source is declared in its wiring file, so this reference "
            "either points at another tenant's repo or at nothing"
        ]
    return []


def _path_problems(rel: str, spec: dict, root: Path | None) -> list[str]:
    """`spec.path` must stay inside the tenant repository.

    The path is resolved in the tenant's checkout, so a name that is also a
    directory here means it was aimed at this cluster's own tree.
    """
    raw = spec.get("path")
    if not raw:
        return [
            f"{rel}: Kustomization has no spec.path — it reconciles the tenant "
            "repository root, every manifest in it included"
        ]
    path = posixpath.normpath(str(raw).lstrip("/"))
    if path == ".." or path.startswith("../"):
        return [
            f"{rel}: Kustomization spec.path {raw!r} climbs above the tenant "
            "repository root"
        ]
    if root is not None and (root / path).is_dir():
        return [
            f"{rel}: Kustomization spec.path {raw!r} is a directory of this "
            "cluster repo — the tenant ServiceAccount would reconcile the "
            "cluster's own manifests into the tenant namespace"
        ]
    return []


def _rolebinding_problems(docs: list, rel: str, account: str, namespace) -> list[str]:
    problems = []
    for role in REQUIRED_ROLES:
        bound = [
            b
            for b in _of_kind(docs, "RoleBinding")
            if (b.get("roleRef") or {}).get("name") == role
            and any(
                s.get("kind") == "ServiceAccount"
                and s.get("name") == account
                and s.get("namespace") == FLUX_NAMESPACE
                for s in (b.get("subjects") or [])
            )
        ]
        if not bound:
            problems.append(
                f"{rel}: no RoleBinding grants {role} to {FLUX_NAMESPACE}/{account}"
            )
            continue
        if namespace and all(
            (b.get("metadata") or {}).get("namespace") != namespace for b in bound
        ):
            problems.append(
                f"{rel}: the {role} RoleBinding is not in the tenant namespace {namespace}"
            )
    return problems


def check(root: Path) -> list[str]:
    base = root / TENANTS_DIR
    on_disk = tenant_files(root)
    listed = listed_resources(root)
    # The subject is what Flux applies plus what is on disk: a listed file the
    # glob never saw is still checked, not only reported as existing.
    subject = set(on_disk) | {
        base / resource for resource in listed - SHARED if (base / resource).is_file()
    }
    problems = []
    for path in sorted(subject):
        problems += check_file(
            path, path.relative_to(root).as_posix(), listed,
            listed_as=path.relative_to(base).as_posix(), root=root,
        )
    for resource in sorted(listed - SHARED):
        if not (base / resource).exists():
            problems.append(
                f"{TENANTS_DIR}/{AGGREGATOR} lists {resource}, which does not exist"
            )
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tenant wiring completeness")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    root = Path(args.repo_root)

    try:
        count = len(tenant_files(root))
        if not count:
            # An empty tree is only a fact when the aggregator agrees. A tenant
            # it lists that the glob missed means the glob broke, which would
            # otherwise degrade this gate to a vacuous pass.
            unseen = sorted(listed_resources(root) - SHARED)
            if unseen:
                raise Vacuous(
                    f"{TENANTS_DIR}/{AGGREGATOR} lists {', '.join(unseen)}, which "
                    f"the {TENANTS_DIR}/*.yaml scan did not find"
                )
            print("check-tenant-wiring: no tenant onboarded, and none listed.")
            return 0
        problems = check(root)
    except (Vacuous, OSError) as exc:
        print(f"check-tenant-wiring inspected nothing: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("Tenant wiring is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Tenant wiring OK ({count} tenant file(s) in {TENANTS_DIR}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
