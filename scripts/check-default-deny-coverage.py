#!/usr/bin/env python3
"""Assert every namespace that owns a workload carries an ingress default-deny.

CLAUDE.md and docs/29 state the mandate — "an ingress default-deny is mandatory
in every namespace", normally via the `netpol-baseline` component — but until
this gate it was enforced by review alone. Its sibling
`scripts/check-scrape-netpol.py` cannot cover it by construction: that gate only
inspects namespaces which ALREADY run an ingress-deny policy, so an unfenced
namespace is invisible to it rather than a failure (which is also why handing it
an `--exempt` for an unfenced namespace would be inert — that namespace never
reaches its exempt check).

Reads the rendered corpus `task flux:lint` builds (`kustomize build | envsubst`)
on stdin.

A namespace OWNS A WORKLOAD when the corpus puts a Deployment / StatefulSet /
DaemonSet / ReplicaSet / Job / CronJob / Pod in it, or a HelmRelease targets it
(a chart's own workloads never appear in a kustomize corpus, so the release is
the only visible proxy).

A namespace is FENCED when it carries a NetworkPolicy with `Ingress` in
policyTypes and an empty/absent podSelector — i.e. one that selects every pod —
AND no namespace-wide policy that ALLOWS all ingress. An app-scoped policy is
deliberately not enough: it fences its own pods and leaves every other pod in the
namespace open. Neither is a namespace-wide policy carrying an empty `ingress:`
rule (`{}` — neither `from` nor `ports`, the API's "from anywhere, on any port"):
that shape satisfied the old "has an Ingress policyType" test while granting
exactly what the mandate forbids. NetworkPolicies are additive, so one such
policy defeats every default-deny beside it — hence the namespace is reported
unfenced even when a real `default-deny-ingress` is present too.

EXEMPT namespaces are declared below with their reason. An exemption is a
decision, not a default, and each one is a namespace this repo does not fully
own. Unused exemptions are reported, never fatal: a namespace can drop out of
the corpus (flux-system's gotk manifests are applied by the bootstrap
Kustomization, which the render loop does not walk) without the exemption
becoming wrong.

Exit codes: 0 clean, 1 an unfenced namespace, 2 the gate could not inspect its
subject (empty corpus, or a corpus with no workload namespace at all — the shape
a render loop that never reached the app stages produces).

Usage (wired into flux:lint, on the accumulated full corpus):
  python3 scripts/check-default-deny-coverage.py [--exempt NS=REASON ...] < corpus
"""
from __future__ import annotations

import argparse
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

WORKLOAD_KINDS = {
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "ReplicaSet",
    "Job",
    "CronJob",
    "Pod",
}

# The sanctioned exceptions, machine-readable so a THIRD one fails this gate
# instead of passing review. `downloads` is deliberately NOT here: its local
# policy is namespace-wide and covers ingress as well as egress, so it satisfies
# the invariant outright. `kube-system` is no longer here either — it carries a
# real default-deny plus an enumerated allow set
# (kubernetes/infrastructure/configs/kube-system-policies/).
EXEMPT_NAMESPACES = {
    "flux-system": (
        "the gotk-components manifest ships its own policies and is regenerated "
        "verbatim by `flux install`; a policy added here would be reverted "
        "(docs/29 § Network policy exceptions)."
    ),
}


class OperatorError(RuntimeError):
    """A broken invocation or an uninspectable corpus — exit 2, never exit 1."""


def _policy_types(spec: dict) -> set[str]:
    """policyTypes, applying the API default (inferred from which rules exist)."""
    declared = spec.get("policyTypes")
    if declared:
        return {str(t) for t in declared}
    types = {"Ingress"}
    if spec.get("egress"):
        types.add("Egress")
    return types


def _allows_all_ingress(spec: dict) -> bool:
    """True when an ingress rule admits everything.

    An `ingress:` entry with neither `from` nor `ports` is the API's "allow from
    any source, on any port" rule. A policy carrying one fences nothing, so it
    must not be counted as the namespace's default-deny — the gate is named for
    the mandate, not for the mere presence of an Ingress policyType.
    A rule that names ports but no `from` is NOT treated as wide open here: it
    still narrows the surface, and calling it a violation would be a different
    (port-level) mandate than the one this gate enforces.
    """
    for rule in spec.get("ingress") or []:
        if isinstance(rule, dict) and not rule.get("from") and not rule.get("ports"):
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
        raise OperatorError(f"failed to parse YAML input: {exc}") from exc
    return docs


def analyze(docs: list[dict]) -> tuple[dict[str, set[str]], set[str]]:
    """-> (namespace -> the workloads that put it in scope, fenced namespaces)."""
    workloads: dict[str, set[str]] = {}
    fenced: set[str] = set()
    # Namespace-wide policies that ALLOW all ingress. Tracked separately and
    # subtracted at the end because NetworkPolicies are additive: one of these
    # re-opens the namespace no matter what else is declared beside it.
    wide_open: set[str] = set()

    for doc in docs:
        kind = doc.get("kind")
        meta = doc.get("metadata") or {}
        ns = meta.get("namespace") or ""
        name = meta.get("name", "?")
        spec = doc.get("spec") or {}

        if kind in WORKLOAD_KINDS and ns:
            workloads.setdefault(ns, set()).add(f"{kind}/{name}")
        elif kind == "HelmRelease":
            target = spec.get("targetNamespace") or ns
            if target:
                workloads.setdefault(target, set()).add(f"HelmRelease/{name}")
        elif kind == "NetworkPolicy" and ns:
            if "Ingress" in _policy_types(spec) and not spec.get("podSelector"):
                if _allows_all_ingress(spec):
                    wide_open.add(ns)
                else:
                    fenced.add(ns)

    return workloads, fenced - wide_open


def _parse_exempt(values: list[str]) -> dict[str, str]:
    """`NS=REASON` pairs. A reason is mandatory: an unexplained exemption is a hole."""
    exempt: dict[str, str] = {}
    for raw in values or []:
        ns, sep, reason = raw.partition("=")
        if not sep or not ns.strip() or not reason.strip():
            raise OperatorError(f"--exempt takes NS=REASON, got {raw!r}")
        exempt[ns.strip()] = reason.strip()
    return exempt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Every workload-owning namespace must carry an ingress default-deny.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--exempt",
        action="append",
        default=[],
        metavar="NS=REASON",
        help="additional exempt namespace, with its reason (repeatable)",
    )
    args = parser.parse_args(argv)

    try:
        exempt = dict(EXEMPT_NAMESPACES)
        exempt.update(_parse_exempt(args.exempt))
        docs = _load(sys.stdin)
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not docs:
        print(
            "ERROR: empty corpus — no manifests on stdin. A gate that passes on nothing "
            "is not a gate; check the pipe and the `kustomize build` paths feeding it.",
            file=sys.stderr,
        )
        return 2

    workloads, fenced = analyze(docs)

    if not workloads:
        print(
            f"ERROR: inspected 0 workload namespaces in {len(docs)} document(s) — the "
            "render loop produced documents but reached no stage that deploys a "
            "workload, so every namespace went unexamined.",
            file=sys.stderr,
        )
        return 2

    violations = []
    for ns in sorted(workloads):
        if ns in fenced or ns in exempt:
            continue
        owners = ", ".join(sorted(workloads[ns])[:4])
        violations.append(
            f"  {ns}: owns workloads ({owners}) but no namespace-wide NetworkPolicy "
            f"that denies ingress by default — a policy carrying an empty ingress "
            f"rule (`ingress: [{{}}]`) allows everything and does not count. Add the "
            f"netpol-baseline component to the namespace's kustomization "
            f"(kubernetes/components/README.md), or declare a reasoned exemption in "
            f"scripts/check-default-deny-coverage.py."
        )

    if violations:
        print(
            "Ingress default-deny mandate violated — namespaces open to every pod "
            "in the cluster:",
            file=sys.stderr,
        )
        print("\n".join(violations), file=sys.stderr)
        return 1

    unused = sorted(ns for ns in exempt if ns not in workloads)
    if unused:
        print(f"(exemptions declared but not exercised by this corpus: {', '.join(unused)})")
    print(
        f"Ingress default-deny OK ({len(workloads)} workload namespaces, "
        f"{len(workloads) - len([n for n in workloads if n in exempt])} fenced, "
        f"{len([n for n in workloads if n in exempt])} exempt) in {len(docs)} document(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
