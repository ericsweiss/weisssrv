#!/usr/bin/env python3
"""Assert every claim pins a storageClassName (docs/29, docs/33).

Nothing in this cluster may use dynamic provisioning: every PersistentVolume is
pre-provisioned (zvol or NFS) with `storageClassName: ""` and `Retain`, and a
`local-path` PV lands on the k3s VM bootdisk, which NO backup path covers.

The trap is that omitting the field is not neutral. A PVC with no
`storageClassName` is rewritten by the DefaultStorageClass admission plugin to
whatever class is marked default — silently, at create time, with no diff in
git. That is exactly how `observability/storage-loki-0` was provisioned onto a
local-path PV in 2026-07 while its intended `loki-data` PV sat unbound; the data
was only saved because an operator rsynced it back. StatefulSet
`volumeClaimTemplates` are immutable, so the mistake is not editable after the
fact — the PVC has to be deleted and recreated.

`local-storage` is now in `k3s_disable` (group_vars/k3s.yml) so no default class
exists to fall through to, but that is one inventory edit away from returning.
This check makes the omission itself fail, in CI, before it can reach a cluster.

Input: the rendered manifest corpus on stdin (what `task flux:lint` accumulates
from `kustomize build | envsubst`).

Usage:
  cat rendered-corpus.yaml | python3 scripts/check-pvc-storageclass.py
"""
from __future__ import annotations

import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

# Chart values shapes that create a PVC the corpus never renders (the chart
# does, server-side). A persistence block that declares a size is provisioning
# storage, so it must also say WHICH class — `storageClass: ""` for a static
# bind, the chart-specific `"-"` sentinel where the template's `with` guard
# would otherwise drop an empty string (loki), or an existingClaim.
_CLASS_KEYS = ("storageClass", "storageClassName", "existingClaim", "existingVolume")


def _claim_violations(docs: list[dict]) -> list[str]:
    out: list[str] = []
    for d in docs:
        kind = d.get("kind")
        meta = d.get("metadata") or {}
        where = f"{meta.get('namespace', '')}/{kind}/{meta.get('name', '?')}"
        claims: list[tuple[str, dict]] = []
        if kind == "PersistentVolumeClaim":
            claims.append((where, d.get("spec") or {}))
        elif kind == "StatefulSet":
            templates = ((d.get("spec") or {}).get("volumeClaimTemplates") or [])
            for t in templates:
                if not isinstance(t, dict):
                    continue
                tname = (t.get("metadata") or {}).get("name", "?")
                claims.append((f"{where} volumeClaimTemplate {tname!r}", t.get("spec") or {}))
        for label, spec in claims:
            if not isinstance(spec, dict) or "storageClassName" not in spec:
                out.append(
                    f"  {label}: no storageClassName — the default StorageClass "
                    f'would capture this claim (use "" to bind a static PV)'
                )
    return out


def _values_violations(node, doc_label: str, path: str = "values") -> list[str]:
    """Find HelmRelease persistence blocks that size a volume but name no class."""
    out: list[str] = []
    if isinstance(node, dict):
        if "size" in node and node.get("enabled") is not False:
            if not any(k in node for k in _CLASS_KEYS):
                out.append(
                    f"  {doc_label}: {path} declares size={node['size']!r} but no "
                    f"storageClass — the chart's PVC would take the default class"
                )
        for k, v in node.items():
            out.extend(_values_violations(v, doc_label, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_values_violations(v, doc_label, f"{path}[{i}]"))
    return out


def violations(docs: list[dict]) -> list[str]:
    out = _claim_violations(docs)
    for d in docs:
        if d.get("kind") != "HelmRelease":
            continue
        meta = d.get("metadata") or {}
        label = f"{meta.get('namespace', '')}/HelmRelease/{meta.get('name', '?')}"
        out.extend(_values_violations((d.get("spec") or {}).get("values") or {}, label))
    return out


def main() -> int:
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

    found = violations(docs)
    if found:
        print(
            "Claims without an explicit storageClassName — a missing field is "
            "rewritten to the cluster-default StorageClass at admission, which is "
            "how a PVC silently lands on an unbacked-up disk (docs/29 §Loki PV "
            "storageClass guard):",
            file=sys.stderr,
        )
        print("\n".join(found), file=sys.stderr)
        return 1

    print("storageClassName policy OK (every claim pins its class)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
