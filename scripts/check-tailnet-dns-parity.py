#!/usr/bin/env python3
"""Assert tailnet-dns's override zone holds exactly the `adguard_home_rewrites`
entries that do not answer with the internal Traefik VIP, plus STRICT_ROUTES.
Exit 0 clean, 1 drifted, 2 the gate could not inspect its subject.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

REPO = Path(__file__).resolve().parent.parent

HOSTS = Path("ansible/inventories/prod/hosts.yml")
DNS_VARS = Path("ansible/inventories/prod/group_vars/dns.yml")
COREFILE = Path("kubernetes/apps/tailnet-dns/configmap.yaml")
CLUSTER_CONFIG = Path("kubernetes/infrastructure/sources/cluster-config.yaml")

# Direct-IP rewrites left out of the override zone, with the reason.
EXEMPT = {
    "immich-ml": "LAN-only inference API for the Immich VM; no tailnet consumer",
}

# Override-zone names that are Traefik routes, not direct-IP rewrites: they are
# chained with lan-tailscale-strict, whose allowlist rejects the pod source IP
# the mesh CNAME path arrives with.
STRICT_ROUTES = {
    "traefik": "chained with lan-tailscale-strict",
    "connect": "chained with lan-tailscale-strict",
    "router": "chained with lan-tailscale-strict",
    "dns-01": "chained with lan-tailscale-strict",
    "dns-02": "chained with lan-tailscale-strict",
}

# Variable spellings of the internal ingress VIP: a rewrite answering
# `{{ metallb_internal_vip }}` is ingress-fronted, not a direct-IP answer.
INTERNAL_VIP_ALIASES = {"metallb_internal_vip"}

# A host label in front of the internal-domain placeholder, e.g.
# `pve-nas-01.${cluster_internal_domain}`.
_ZONE_NAME = re.compile(r"^(?P<name>[A-Za-z0-9_-]+)\.\$\{cluster_internal_domain\}$")
# `dns-01.{{ internal_domain }}` in the Ansible rewrites.
_REWRITE_NAME = re.compile(r"^(?P<name>[A-Za-z0-9_-]+)\.\{\{\s*internal_domain\s*\}\}$")


class GateError(Exception):
    """The gate could not inspect its subject — exit 2, never a violation."""


def _load_yaml(path: Path):
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except OSError as exc:
        raise GateError(f"{path}: unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GateError(f"{path}: unparseable YAML: {exc}") from exc


def _unwrap(value) -> str:
    """A rewrite answer reduced to a literal address or a bare variable name."""
    text = str(value).strip()
    if text.startswith("{{") and text.endswith("}}"):
        text = text[2:-2].strip()
    return text


def rewrite_names(dns_vars: Path, internal_vip: str) -> tuple[set[str], set[str]]:
    """(every rewrite label, the labels answering something other than the VIP)."""
    doc = _load_yaml(dns_vars) or {}
    rewrites = doc.get("adguard_home_rewrites")
    if not rewrites:
        raise GateError(f"{dns_vars}: no adguard_home_rewrites entries")
    every: set[str] = set()
    direct: set[str] = set()
    ingress = {internal_vip} | INTERNAL_VIP_ALIASES
    for entry in rewrites:
        if not isinstance(entry, dict):
            continue
        match = _REWRITE_NAME.match(str(entry.get("domain", "")))
        if not match:
            continue
        every.add(match.group("name"))
        if _unwrap(entry.get("answer", "")) not in ingress:
            direct.add(match.group("name"))
    if not direct:
        raise GateError(f"{dns_vars}: every rewrite answers {internal_vip}")
    return every, direct


def host_names(path: Path) -> set[str]:
    """Inventory hostnames with an address: the role rewrites one A record each."""
    found: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        for name, host in (node.get("hosts") or {}).items():
            if isinstance(host, dict) and host.get("ansible_host"):
                found.add(str(name))
        for child in (node.get("children") or {}).values():
            walk(child)

    walk((_load_yaml(path) or {}).get("all") or {})
    if not found:
        raise GateError(f"{path}: no host declares an ansible_host")
    return found


def override_zone_names(corefile: Path) -> set[str]:
    """Host labels on the Corefile override-zone line."""
    doc = _load_yaml(corefile) or {}
    text = ((doc.get("data") or {}).get("Corefile")) or ""
    best: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line.endswith("{"):
            continue
        names = set()
        for token in line[:-1].split():
            match = _ZONE_NAME.match(token)
            if match:
                names.add(match.group("name"))
        # The default zone line carries the bare placeholder; the override zone
        # is the one listing host names in front of it.
        if len(names) > len(best):
            best = names
    if not best:
        raise GateError(f"{corefile}: no override-zone names found")
    return best


def internal_vip(cluster_config: Path) -> str:
    doc = _load_yaml(cluster_config) or {}
    vip = (doc.get("data") or {}).get("cluster_metallb_internal_vip")
    if not vip:
        raise GateError(f"{cluster_config}: no cluster_metallb_internal_vip key")
    return str(vip)


def check(root: Path) -> list[str]:
    vip = internal_vip(root / CLUSTER_CONFIG)
    every_rewrite, direct = rewrite_names(root / DNS_VARS, vip)
    hosts = host_names(root / HOSTS)
    zone = override_zone_names(root / COREFILE)
    problems = []
    for name in sorted(direct - zone - set(EXEMPT)):
        problems.append(
            f"{name} has a direct-IP rewrite in {DNS_VARS} but is missing from the "
            f"override zone in {COREFILE} — tailnet clients would get the Traefik "
            f"CNAME instead of its LAN IP. Add it, or exempt it with a reason."
        )
    # Subtracting the inventory host set too: a zone entry naming a host that no
    # longer exists is drift, and a STRICT_ROUTES entry would otherwise hide it.
    # EXEMPT stays in: a name declared out of the zone must not be in it.
    for name in sorted(zone - hosts - (direct - set(EXEMPT)) - set(STRICT_ROUTES)):
        if name in EXEMPT:
            detail = f"it is listed EXEMPT ({EXEMPT[name]})"
        elif name in every_rewrite:
            detail = f"its {DNS_VARS} rewrite answers the internal ingress VIP"
        else:
            detail = f"it is neither an inventory host nor a rewrite in {DNS_VARS}"
        problems.append(f"{name} is in the {COREFILE} override zone but {detail}.")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="tailnet-dns override zone must equal the direct-IP AdGuard rewrites.",
    )
    parser.add_argument("--repo-root", default=REPO, type=Path)
    args = parser.parse_args(argv)
    try:
        problems = check(args.repo_root)
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("ERROR: the tailnet-dns override zone has drifted from adguard_home_rewrites:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "tailnet-dns override zone matches the direct-IP AdGuard rewrites "
        f"(plus {len(STRICT_ROUTES)} strict-middleware routes)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
