#!/usr/bin/env python3
"""Fail the first tenant onboarding while Traefik still allows cross-namespace refs.

Traefik runs with `providers.kubernetesCRD.allowCrossNamespace: true` and watches
every namespace — an accepted single-operator risk (the note lives on the
HelmRelease itself). `tenants/tenant-crd-editor.yaml` already grants every future
tenant full `traefik.io` ingressroutes/middlewares/traefikservices write, so the
day a tenant wiring file lands that tenant can author an IngressRoute pointing at
another namespace's Service or Middleware.

docs/30-multi-repo-onboarding.md § Pre-Onboarding Checklist tracks this as the
first pre-tenant gap. This turns that checklist line into a build failure at
exactly the moment it matters: a second resource in the tenants kustomization
while allowCrossNamespace is still true.

Wired from scripts/test_site_configs.py rather than `task flux:lint`, so it runs
in both `task lint` and CI's python-tests job without a second copy of the
invocation in .gitlab-ci.yml.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TENANTS_KUSTOMIZATION = "kubernetes/clusters/weisssrv/tenants/kustomization.yaml"
TRAEFIK_RELEASE = "kubernetes/infrastructure/controllers/traefik/release.yaml"

# The one resource that is always present: the shared tenant RBAC, not a tenant.
BASELINE = {"tenant-crd-editor.yaml"}


def tenant_resources(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(r) for r in doc.get("resources") or []]


def allows_cross_namespace(path: Path) -> bool:
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not doc or doc.get("kind") != "HelmRelease":
            continue
        providers = (doc.get("spec") or {}).get("values", {}).get("providers") or {}
        if (providers.get("kubernetesCRD") or {}).get("allowCrossNamespace"):
            return True
    return False


def check(root: Path = REPO) -> list[str]:
    kustomization = root / TENANTS_KUSTOMIZATION
    release = root / TRAEFIK_RELEASE
    for path in (kustomization, release):
        if not path.is_file():
            return [f"{path.relative_to(root)} is missing — this gate no longer binds"]

    resources = tenant_resources(kustomization)
    if not resources:
        return [f"{TENANTS_KUSTOMIZATION} lists no resources — has the tenant wiring moved?"]

    tenants = [r for r in resources if r not in BASELINE]
    if not tenants or not allows_cross_namespace(release):
        return []

    return [
        f"{TENANTS_KUSTOMIZATION} onboards {', '.join(tenants)} while "
        f"{TRAEFIK_RELEASE} still sets providers.kubernetesCRD.allowCrossNamespace: "
        "true. tenant-crd-editor grants tenants full traefik.io write, so that "
        "tenant can route to another namespace's Service or Middleware. Work the "
        "docs/30 § Pre-Onboarding Checklist first: scope the CRD provider "
        "per-tenant, or set allowCrossNamespace: false."
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo", type=Path, default=REPO)
    args = ap.parse_args(argv)

    problems = check(args.repo)
    if problems:
        print("check-tenant-traefik-isolation: FAILED", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("check-tenant-traefik-isolation: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
