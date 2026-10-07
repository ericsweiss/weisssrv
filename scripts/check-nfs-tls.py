#!/usr/bin/env python3
"""Assert every NFS PersistentVolume mounts over TLS, by hostname.

The nas_storage exports reject plaintext and the server cert has no IP SAN, so a
PV naming the NAS by IP fails the handshake. Reads the corpus on stdin (docs/29).
"""
from __future__ import annotations

import argparse
import ipaddress
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

REQUIRED_OPTION = "xprtsec=tls"


def _flatten(raw) -> list[dict]:
    """The mapping documents in `raw`, unwrapping `kind: List` and bare lists.

    A corpus that wraps its PersistentVolumes in a List would otherwise present
    no PV at all, and every one inside it would go uninspected.
    """
    if isinstance(raw, dict):
        if raw.get("kind") == "List" and isinstance(raw.get("items"), list):
            return [item for item in raw["items"] if isinstance(item, dict)]
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _is_ip(server: str) -> bool:
    try:
        ipaddress.ip_address(server)
    except ValueError:
        return False
    return True


def nfs_violations(
    docs: list[dict], allow_ip_server: bool = False
) -> tuple[list[str], int]:
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
        # Kubernetes comma-joins mountOptions for the mount helper, so one
        # element may carry several options.
        flat = {part.strip() for element in options for part in element.split(",")}
        if REQUIRED_OPTION not in flat:
            out.append(
                f"PersistentVolume/{name}: mountOptions lack {REQUIRED_OPTION} "
                f"(has {options or 'none'}) — the export rejects plaintext"
            )
        server = str(nfs.get("server", ""))
        if not server:
            out.append(f"PersistentVolume/{name}: spec.nfs.server is empty")
        elif _is_ip(server) and not allow_ip_server:
            out.append(
                f"PersistentVolume/{name}: server {server} is an IP — the "
                "*.esweiss.com certificate has no IP SAN, so the TLS handshake fails"
            )
    return out, seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--allow-ip-server",
        action="store_true",
        help="the server certificate carries an IP SAN, so an IP in "
        "spec.nfs.server is allowed. This cluster's *.esweiss.com cert has "
        "none, so the flag stays off here.",
    )
    args = parser.parse_args(argv)

    try:
        docs = [
            doc
            for raw in yaml.safe_load_all(sys.stdin.read())
            for doc in _flatten(raw)
        ]
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

    found, seen = nfs_violations(docs, args.allow_ip_server)
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
        f"document(s) (every one mounts {REQUIRED_OPTION}"
        f"{'' if args.allow_ip_server else ' by hostname'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
