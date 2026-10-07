#!/usr/bin/env python3
"""Assert every hand-written LAN address is a real inventory host.

Inside a declared host CIDR an endpoint address or NFS export client must cover
an `ansible_host` (the gateway aside), and an export admits a k3s group whole.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

# PYTHONSAFEPATH, and any invocation that is not a direct script run, keeps this
# directory off sys.path, so the companion module is placed there explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from inventory_tree import (  # noqa: E402
        addresses_by_host,
        group_index,
        hosts_by_address,
        load_inventory,
        resolve_hosts,
    )
except ImportError:  # pragma: no cover - environment guard
    sys.exit("inventory_tree.py must sit next to this script")

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

HOSTS_YML = "ansible/inventories/prod/hosts.yml"
MANIFEST_TREE = "kubernetes"
# Both suffixes: kustomize renders a `.yml` manifest, so a one-suffix walk
# leaves an EndpointSlice invisible to this gate.
MANIFEST_GLOBS = ("*.yaml", "*.yml")
CLUSTER_CONFIG = "kubernetes/infrastructure/sources/cluster-config.yaml"
INVENTORY = "ansible/inventories/prod"
EXPORTS_KEY = "nas_storage_exports"
DEFAULT_LAN_CIDR_KEY = "cluster_lan_cidr"
GATEWAY_KEY = "cluster_lan_gateway"
# Groups whose members an export either admits in full or not at all.
K3S_SCOPED_GROUPS = ("k3s_servers", "k3s_agents")


class Vacuous(Exception):
    """The gate could not inspect its subject — exit 2, never a silent pass."""


def inventory_addresses(root: Path) -> dict[str, str]:
    """{ansible_host: inventory_hostname} for every host that declares one."""
    try:
        found = hosts_by_address(load_inventory(root / HOSTS_YML))
    except (OSError, yaml.YAMLError) as exc:
        raise Vacuous(f"{HOSTS_YML} unreadable: {exc}") from exc
    if not found:
        raise Vacuous(f"{HOSTS_YML} declares no ansible_host values")
    return found


def inventory_groups(root: Path) -> dict[str, set[str]]:
    """{group: {ansible_host, ...}}, children expanded, for the k3s groups.

    A membership question is asked of addresses, not host names, because an
    export admits CIDRs and /32s.
    """
    try:
        inventory = load_inventory(root / HOSTS_YML)
    except (OSError, yaml.YAMLError) as exc:
        raise Vacuous(f"{HOSTS_YML} unreadable: {exc}") from exc
    index = group_index(inventory)
    by_host = addresses_by_host(inventory)
    return {
        group: {by_host[host] for host in resolve_hosts(group, index) if host in by_host}
        for group in K3S_SCOPED_GROUPS
    }


def _inventory_var_files(root: Path) -> list[Path]:
    """Every group_vars and host_vars file, both YAML spellings and nested dirs."""
    found: list[Path] = []
    for tree in ("group_vars", "host_vars"):
        directory = root / INVENTORY / tree
        if not directory.is_dir():
            continue
        for pattern in ("*.yml", "*.yaml"):
            found += [path for path in directory.rglob(pattern) if path.is_file()]
    return sorted(set(found))


def nfs_exports(root: Path) -> tuple[list[tuple[str, list[str], str]], list[str]]:
    """((export path, client specs, the file it came from), unreadable files)."""
    found: list[tuple[str, list[str], str]] = []
    skipped: list[str] = []
    for path in _inventory_var_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            skipped.append(
                f"{rel}: unreadable ({exc.__class__.__name__}: {exc}) — "
                "its NFS exports were not checked"
            )
            continue
        if not isinstance(doc, dict):
            continue
        for export in doc.get(EXPORTS_KEY) or []:
            if not isinstance(export, dict):
                continue
            specs = [
                str(client["spec"])
                for client in export.get("clients") or []
                if isinstance(client, dict) and client.get("spec")
            ]
            found.append((str(export.get("path", "?")), specs, rel))
    return found, skipped


def _networks(specs: list[str]) -> list[tuple[str, object]]:
    """(spec, network) for every client spec that is a CIDR or a bare address.

    A hostname, netgroup or wildcard spec is a legal export client this gate
    cannot resolve to an address, so it is left out rather than reported.
    """
    found = []
    for spec in specs:
        try:
            found.append((spec, ipaddress.ip_network(spec, strict=False)))
        except ValueError:
            continue
    return found


def partial_group_exports(
    exports: list[tuple[str, list[str], str]], groups: dict[str, set[str]]
) -> list[str]:
    """Exports admitting some of a k3s group's nodes but not all of them.

    A client list is frozen when it is written, so a node added or renumbered
    later loses the mount on itself alone while the rest keep it.
    """
    problems = []
    for export, specs, rel in exports:
        networks = [network for _spec, network in _networks(specs)]
        if not networks:
            continue
        for group, members in groups.items():
            admitted = {
                address
                for address in members
                if any(ipaddress.ip_address(address) in net for net in networks)
            }
            if not admitted or admitted == members:
                continue
            problems.append(
                f"{rel}: export {export} admits part of {group} but not "
                f"{', '.join(sorted(members - admitted))} — that node mounts "
                "nothing while the rest do"
            )
    return problems


def cluster_config(root: Path) -> dict[str, str]:
    """cluster-config's data map, the substitution source Flux uses."""
    try:
        doc = yaml.safe_load((root / CLUSTER_CONFIG).read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise Vacuous(f"{CLUSTER_CONFIG} unreadable: {exc}") from exc
    return {str(k): str(v) for k, v in (doc.get("data") or {}).items()}


def cluster_values(
    root: Path,
    lan_cidr_keys: tuple[str, ...] = (DEFAULT_LAN_CIDR_KEY,),
    extra_lan_cidrs: tuple[str, ...] = (),
) -> tuple[list[str], str]:
    """(lan cidrs, lan gateway) from cluster-config plus any extra ranges.

    A cluster with a management, storage or DMZ VLAN declares a key per range:
    an address is in scope when it falls inside ANY of them.
    """
    data = cluster_config(root)
    cidrs = [data[key] for key in lan_cidr_keys if data.get(key)]
    cidrs += [c for c in extra_lan_cidrs if c]
    if not cidrs:
        raise Vacuous(
            f"{CLUSTER_CONFIG} declares none of {', '.join(lan_cidr_keys)} — "
            "the gate has no LAN to scope to"
        )
    # A cluster that declares no gateway key simply gets no gateway allowance.
    return cidrs, data.get(GATEWAY_KEY, "")


def substitute(address: str, config: dict[str, str]) -> str:
    """Resolve a `${cluster_*}` placeholder the way Flux's postBuild does.

    An unknown placeholder is left alone and fails the IP parse below.
    """
    match = _PLACEHOLDER.fullmatch(address.strip())
    if match and match.group(1) in config:
        return config[match.group(1)]
    return address


def _endpoint_list(value, config: dict[str, str]) -> tuple[list, str]:
    """(entries, reason it could not be read), resolving a whole-list roster key.

    A slice may spell its endpoints as one `${cluster_*}` placeholder holding a
    JSON list; a reason, not a bare empty list, keeps a dropped slice visible.
    """
    if isinstance(value, str):
        resolved = substitute(value, config)
        if resolved == value.strip():
            return [], f"{value!r} names no cluster-config key"
        try:
            value = yaml.safe_load(resolved)
        except yaml.YAMLError as exc:
            return [], f"{value!r} resolves to unparsable YAML ({exc.__class__.__name__})"
    entries = [entry for entry in (value or []) if isinstance(entry, dict)]
    if value and not entries:
        return [], f"{value!r} resolves to no list of endpoint entries"
    return entries, ""


def endpoint_addresses(
    root: Path, config: dict[str, str]
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(addresses, skipped): every address a hand-written endpoint names, plus
    the files this gate could not read."""
    found: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    tree = root / MANIFEST_TREE
    for path in sorted({p for glob in MANIFEST_GLOBS for p in tree.rglob(glob)}):
        rel = path.relative_to(root).as_posix()
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except (OSError, yaml.YAMLError) as exc:
            skipped.append(
                f"{rel}: unreadable ({exc.__class__.__name__}: {exc}) — "
                "this gate could not inspect it"
            )
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            name = (doc.get("metadata") or {}).get("name", "?")
            if doc.get("kind") == "EndpointSlice":
                entries, reason = _endpoint_list(doc.get("endpoints"), config)
                if reason:
                    skipped.append(
                        f"{rel}: EndpointSlice/{name} endpoints {reason} — "
                        "this gate could not inspect it"
                    )
                for endpoint in entries:
                    for address in endpoint.get("addresses") or []:
                        found.append((str(address), f"EndpointSlice/{name}", rel))
            elif doc.get("kind") == "Endpoints":
                entries, reason = _endpoint_list(doc.get("subsets"), config)
                if reason:
                    skipped.append(
                        f"{rel}: Endpoints/{name} subsets {reason} — "
                        "this gate could not inspect it"
                    )
                for subset in entries:
                    for address in subset.get("addresses") or []:
                        if not isinstance(address, dict):
                            continue
                        if address.get("ip"):
                            found.append((str(address["ip"]), f"Endpoints/{name}", rel))
    return found, skipped


def check(
    root: Path,
    lan_cidr_keys: tuple[str, ...] = (DEFAULT_LAN_CIDR_KEY,),
    extra_lan_cidrs: tuple[str, ...] = (),
) -> tuple[list[str], int]:
    """(problems, number of in-LAN addresses compared against the inventory)."""
    known = inventory_addresses(root)
    config = cluster_config(root)
    cidrs, gateway = cluster_values(root, lan_cidr_keys, extra_lan_cidrs)
    try:
        lans = [ipaddress.ip_network(c) for c in cidrs]
    except ValueError as exc:
        raise Vacuous(f"declared LAN CIDR is not a network: {exc}") from exc
    raw, skipped = endpoint_addresses(root, config)
    addresses = [(substitute(a, config), resource, rel) for a, resource, rel in raw]
    if not addresses:
        raise Vacuous(f"no EndpointSlice/Endpoints address found under {MANIFEST_TREE}/")

    problems = list(skipped)
    checked = 0
    for address, resource, rel in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            problems.append(f"{rel}: {resource} address {address!r} is not an IP address")
            continue
        if not any(parsed in lan for lan in lans) or address == gateway:
            continue
        checked += 1
        if address not in known:
            problems.append(
                f"{rel}: {resource} points at {address}, which is no ansible_host "
                f"in {HOSTS_YML} — the guest was renumbered on one side only"
            )

    exports, export_skipped = nfs_exports(root)
    problems.extend(export_skipped)
    hosts = [ipaddress.ip_address(a) for a in known]
    for export, specs, rel in exports:
        for spec, network in _networks(specs):
            in_lan = [lan for lan in lans if lan.version == network.version
                      and network.subnet_of(lan)]
            if not in_lan:
                continue
            checked += 1
            if not any(host in network for host in hosts):
                problems.append(
                    f"{rel}: export {export} admits {spec}, which covers no "
                    f"ansible_host in {HOSTS_YML} — the client list was written "
                    "for an address range the inventory no longer uses"
                )
    problems.extend(partial_group_exports(exports, inventory_groups(root)))

    if not checked:
        raise Vacuous(
            f"no address inside {', '.join(cidrs)} reached the comparison"
        )
    return problems, checked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Guest endpoint addresses vs the inventory")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument(
        "--lan-cidr-key", action="append", default=None, metavar="KEY",
        help="cluster-config key holding a host CIDR; repeatable for a cluster "
             f"with several host VLANs (default: {DEFAULT_LAN_CIDR_KEY})",
    )
    parser.add_argument(
        "--extra-lan-cidr", action="append", default=None, metavar="CIDR",
        help="host CIDR cluster-config does not name; repeatable",
    )
    args = parser.parse_args(argv)
    root = Path(args.repo_root)
    lan_cidr_keys = tuple(args.lan_cidr_key or (DEFAULT_LAN_CIDR_KEY,))
    extra_lan_cidrs = tuple(args.extra_lan_cidr or ())

    try:
        problems, total = check(root, lan_cidr_keys, extra_lan_cidrs)
    except Vacuous as exc:
        print(f"check-guest-endpoint-parity inspected nothing: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("Hand-written LAN addresses have drifted from the inventory:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(
        "Guest endpoint addresses and NFS export clients agree with the inventory "
        f"({total} LAN address(es) checked)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
