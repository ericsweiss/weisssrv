#!/usr/bin/env python3
"""Assert every NFS PersistentVolume mounts over TLS, by hostname.

The `nas_storage` k3s exports require TLS: the client lines carry `xprtsec=tls`
and the server REJECTS a plaintext mount. A PV that omits the option therefore
does not degrade to plaintext, it fails to mount — after the pod is scheduled,
as a mount error nothing else predicts.

The paired half is the server address. `xprtsec=tls` verifies the server
certificate, whose SAN is `*.esweiss.com` with NO IP SAN, so a PV that names the
NAS by IP fails the handshake. Every PV happens to be correct; the next one
copied from a non-TLS example would not be, and neither half is expressed
anywhere a tool could read.

SCOPE: `spec.nfs` on a PersistentVolume, which is how every NFS mount in this
repo is declared. NOT covered, because neither exists here and both would need a
different shape: a pod-inline `volumes[].nfs` (no mountOptions field at all — the
options come from the kubelet default), and a CSI-provisioned volume, whose
server and options live in the driver's StorageClass parameters rather than in
the PV. Adding either means extending this gate in the same commit.

Input: the rendered manifest corpus on stdin (what `task flux:lint` accumulates
from `kustomize build | envsubst`), the same contract as
check-pvc-storageclass.py and check-scrape-netpol.py.

The gate refuses to be vacuous: an empty corpus, or one that renders documents
but declares no NFS PV, is an operator error (exit 2) rather than a pass — the
render loop having missed the storage-declaring stages is exactly what that
looks like.

Exit 0 clean, 1 on a finding, 2 on an operator error.

Usage:
  cat rendered-corpus.yaml | python3 scripts/check-nfs-tls.py
"""
from __future__ import annotations

import ipaddress
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

REQUIRED_OPTION = "xprtsec=tls"


def _is_ip(server: str) -> bool:
    try:
        ipaddress.ip_address(server)
    except ValueError:
        return False
    return True


def nfs_violations(docs: list[dict]) -> tuple[list[str], int]:
    """-> (violations, NFS PVs inspected). The count feeds the vacuity guard."""
    out: list[str] = []
    seen = 0
    for doc in docs:
        if doc.get("kind") != "PersistentVolume":
            continue
        spec = doc.get("spec") or {}
        nfs = spec.get("nfs")
        if not isinstance(nfs, dict):
            continue
        seen += 1
        name = (doc.get("metadata") or {}).get("name", "?")
        options = [str(o) for o in (spec.get("mountOptions") or [])]
        if REQUIRED_OPTION not in options:
            out.append(
                f"PersistentVolume/{name}: mountOptions lack {REQUIRED_OPTION} "
                f"(has {options or 'none'}) — the export rejects plaintext"
            )
        server = str(nfs.get("server", ""))
        if _is_ip(server):
            out.append(
                f"PersistentVolume/{name}: server {server} is an IP — the "
                "*.esweiss.com certificate has no IP SAN, so the TLS handshake fails"
            )
        elif not server:
            out.append(f"PersistentVolume/{name}: spec.nfs.server is empty")
    return out, seen


def main() -> int:
    try:
        docs = [d for d in yaml.safe_load_all(sys.stdin.read()) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        print(f"ERROR: could not parse the corpus on stdin: {exc}", file=sys.stderr)
        return 2

    if not docs:
        print(
            "ERROR: empty corpus on stdin — a gate that checks nothing is not a "
            "gate. Pipe the accumulated `kustomize build | envsubst` output in.",
            file=sys.stderr,
        )
        return 2

    found, seen = nfs_violations(docs)
    if found:
        print(
            "ERROR: NFS PersistentVolumes that cannot mount against the "
            "TLS-only nas_storage exports:",
            file=sys.stderr,
        )
        print("\n".join(found), file=sys.stderr)
        return 1

    if not seen:
        print(
            f"ERROR: inspected 0 NFS PersistentVolumes in {len(docs)} document(s) — "
            "check that the `kustomize build` paths feeding stdin cover the stages "
            "that declare NFS storage.",
            file=sys.stderr,
        )
        return 2

    print(
        f"NFS TLS policy OK — {seen} NFS PersistentVolume(s) across {len(docs)} "
        f"document(s) (every one mounts {REQUIRED_OPTION} by hostname)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
