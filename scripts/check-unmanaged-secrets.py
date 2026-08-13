#!/usr/bin/env python3
"""Assert every live Secret is owned by something (ESO, Flux, Helm, a controller).

A Secret that nothing declares is a Secret nothing rotates: `task
flux:rotate-secret` and docs/15-credential-rotation.md walk the ExternalSecrets,
so a hand-applied leftover keeps serving a credential value long after that
credential was rotated everywhere else — readable by anyone with `secrets get` in
the namespace and by every cluster-wide secret reader. Flux never prunes them
either, because no manifest claims them.

This is deliberately a LIVE check (like check-live-cpu-limits.py): git cannot see
a Secret that git does not contain. Everything ESO/Flux/Helm/cert-manager creates
carries an ownership marker, so the residue is exactly the hand-applied set.

Input: `kubectl get secrets -A -o json` on stdin (keeps the logic unit-testable
with no cluster). Exit 1 and list the offenders.

Usage:
  kubectl get secrets -A -o json | python3 scripts/check-unmanaged-secrets.py
"""
from __future__ import annotations

import json
import sys

# Secrets that legitimately have no controller owner, "namespace/name" -> why.
# An entry here is a claim that the value is created out-of-band on purpose and
# that its rotation is documented somewhere other than an ExternalSecret.
ALLOWLIST: dict[str, str] = {
    # The two documented bootstrap secrets — they are what lets ESO exist at all,
    # so they cannot come from ESO (task flux:bootstrap-onepassword, docs/29).
    "external-secrets/op-credentials": "ESO bootstrap (docs/29)",
    "external-secrets/onepassword-connect-token": "ESO bootstrap (docs/29)",
    # Written by `flux bootstrap`; holds the git deploy key.
    "flux-system/flux-system": "flux bootstrap git credentials",
    # Created out-of-band by the GitLab agent's Flux module alongside its
    # Receiver; the HMAC trigger token is minted by KAS, not by us (docs/29).
    # Inert today (the Secret also carries ownerReferences) — it is here so the
    # owner is on the record rather than inferred.
    "flux-system/gitlab-receiver-flux-system": "GitLab agent Flux module (docs/29)",
    # Controller-generated state, not credentials we mint.
    "tailscale/operator": "tailscale operator device state",
    "kube-system/k3s-serving": "k3s dynamic listener serving cert",
    "observability/kube-prometheus-stack-admission": "chart admission-webhook cert",
    "vpa-system/vpa-tls-secret": "VPA webhook cert (chart hook)",
}

# Ownership markers, checked in metadata.labels and metadata.annotations.
MANAGED_LABELS = {
    "app.kubernetes.io/managed-by",       # Helm and friends
    "controller.cert-manager.io/fao",     # cert-manager-issued TLS
    "tailscale.com/managed",              # tailscale operator device secrets
}
MANAGED_KEY_PREFIXES = (
    "kustomize.toolkit.fluxcd.io/",       # Flux-applied
    "helm.toolkit.fluxcd.io/",
    "meta.helm.sh/",                      # Helm release membership
    "reconcile.external-secrets.io/",     # ESO
    "cert-manager.io/certificate-name",   # cert-manager-issued TLS
    "listener.cattle.io/",                # k3s dynamic listener
)
MANAGED_TYPES = (
    "helm.sh/release.v1",                 # Helm release history
    "kubernetes.io/service-account-token",
    "bootstrap.kubernetes.io/token",
)

REMEDIATION = """
For each offender: confirm nothing consumes it
  kubectl -n <ns> get pods,deploy,sts,cronjob -o yaml | grep -c '<name>'
then delete it and rotate any value it duplicated (docs/15-credential-rotation.md).
If it must exist out-of-band, declare it: add an ExternalSecret, or add it to
ALLOWLIST in this script with the reason.
Note: an old ReplicaSet revision can still reference a deleted Secret, so a
`kubectl rollout undo` far enough back would fail — prune stale RS history too.
""".rstrip()


def _is_managed(secret: dict) -> bool:
    meta = secret.get("metadata") or {}
    if meta.get("ownerReferences"):
        return True
    if str(secret.get("type", "")) in MANAGED_TYPES:
        return True
    keys = set(meta.get("labels") or {}) | set(meta.get("annotations") or {})
    if keys & MANAGED_LABELS:
        return True
    return any(k.startswith(MANAGED_KEY_PREFIXES) for k in keys)


def unmanaged_secrets(secrets: list[dict]) -> list[str]:
    """One line per Secret with no ownership marker, allowlist applied."""
    out: list[str] = []
    for secret in secrets:
        meta = secret.get("metadata") or {}
        ns = meta.get("namespace", "")
        name = meta.get("name", "?")
        if f"{ns}/{name}" in ALLOWLIST or _is_managed(secret):
            continue
        keys = sorted((secret.get("data") or {}).keys())
        out.append(f"  {ns}/{name}: no owner/manager, keys={keys}")
    return sorted(out)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.exit(f"Failed to parse `kubectl get secrets -o json` input: {exc}")
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        sys.exit("Input is not a secret list (expected `kubectl get secrets -A -o json`)")

    violations = unmanaged_secrets([s for s in items if isinstance(s, dict)])
    if violations:
        print(
            "Unmanaged Secrets found — these are outside the ESO rotation "
            "lifecycle, so `task flux:rotate-secret` and docs/15 never touch "
            "them and a superseded credential value can live on indefinitely:",
            file=sys.stderr,
        )
        print("\n".join(violations), file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    print(f"Unmanaged-Secret check OK ({len(items)} secrets checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
