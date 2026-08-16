#!/usr/bin/env python3
"""Print the cluster's child Flux Kustomizations in dependsOn order.

Derived from kubernetes/clusters/weisssrv/*.yaml, never hand-listed, so adding a
stage is a one-file change and both consumers (`task flux:reconcile`,
scripts/deploy-verify.sh) pick it up. Order is a topological sort
of `spec.dependsOn`, ties broken alphabetically, so the printed sequence is the
order Flux itself will converge them in and is stable across runs.

The root `flux-system` Kustomization is NOT printed: it lives under
clusters/weisssrv/flux-system/ (bootstrap-owned) and callers reconcile it first
by name, before the children it applies.

Usage:
    scripts/flux-child-kustomizations.py [--dir kubernetes/clusters/weisssrv]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO / "kubernetes" / "clusters" / "weisssrv"


def child_kustomizations(directory: Path) -> list[str]:
    deps: dict[str, set[str]] = {}
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as fh:
            for doc in yaml.safe_load_all(fh):
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") != "Kustomization":
                    continue
                if not str(doc.get("apiVersion", "")).startswith("kustomize.toolkit"):
                    continue
                name = doc["metadata"]["name"]
                deps[name] = {
                    d["name"] for d in (doc.get("spec", {}).get("dependsOn") or [])
                }

    ordered: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(
            n for n, d in remaining.items() if not (d & set(remaining)) - {n}
        )
        if not ready:
            # A dependsOn cycle would otherwise loop forever; emit what is left
            # deterministically and let the caller's own reconcile surface it.
            ready = sorted(remaining)
        for name in ready:
            ordered.append(name)
            del remaining[name]
    return ordered


def main(argv: list[str]) -> int:
    directory = DEFAULT_DIR
    if "--dir" in argv:
        directory = Path(argv[argv.index("--dir") + 1])
    names = child_kustomizations(directory)
    if not names:
        print(f"no Flux Kustomizations found under {directory}", file=sys.stderr)
        return 1
    print("\n".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
