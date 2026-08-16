#!/usr/bin/env python3
"""Assert the manifests spell cluster identity as placeholders, not literals.

`kubernetes/infrastructure/sources/cluster-config.yaml` is the site's identity —
domains, CIDRs, VIPs — and every Flux Kustomization after `sources` substitutes
from it. This gate is what stops the tree from drifting back to literals one
manifest at a time: a new IngressRoute pasted from an old one, a copied alert
annotation, a hand-typed VIP.

Two halves:

1. NO ADOPTED LITERAL in the substituted trees. Only the values cluster-config
   actually defines are checked — a per-guest or per-node address is deliberately
   NOT one of them (see the exemptions below and cluster-config.yaml's header).

2. cluster-config AGREES WITH THE ANSIBLE INVENTORY for the keys that exist in
   both. Nothing else keeps them in step: Flux renders an unknown placeholder as
   an empty string and a stale one as a wrong-but-valid value, so a domain that
   moves in group_vars and not here fails nowhere at reconcile time.

EXEMPT, because something parses the manifest BEFORE Flux substitutes:
  * any NetworkPolicy document — scripts/check-netpol-except-parity.py reads the
    ipBlock CIDRs straight from git;
  * kubernetes/infrastructure/observability/rules/ — promtool lints and
    unit-tests the exprs from source, against fixtures carrying real addresses;
  * a domain whose dots are backslash-escaped (a regex) — substituting a value
    into a pattern changes what the pattern matches.

Scanned files: every *.yaml in a substituted tree, read through PyYAML, plus the
GENERATOR sources kustomize renders INTO those manifests (dashboard *.json, the
CronJob *.py, *.toml, *.tpl), read as raw text with comment-only lines dropped —
a dashboard's PromQL label matchers must move with the domain just as a
manifest's do. Markdown is NOT scanned: kustomize never renders it, and prose
should name the real names.

Usage: scripts/check-cluster-literals.py [--repo-root PATH]

Exit codes: 0 clean, 1 violations, 2 the gate could not inspect its subject
(no substituted tree derived, a derived path missing from disk, or a
cluster-config key an arm of this gate checks having disappeared).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.exit("PyYAML required: pip install pyyaml")

# Trees a Flux Kustomization renders WITH substituteFrom — DERIVED from the
# cluster dir, never hand-listed, so a new/renamed/re-pathed stage cannot leave a
# tree unscanned while the gate still prints its success line. sources/ and
# clusters/ fall out on their own: infrastructure-sources.yaml carries no
# substituteFrom (it is where the ConfigMaps live) and the bootstrap
# Kustomization has none either.
CLUSTER_DIR = "kubernetes/clusters/weisssrv"
# Rendered INTO those trees as a kustomize component, so it has no Kustomization
# of its own and cannot be derived.
EXTRA_TREES = ("kubernetes/components",)

# Non-YAML generator sources kustomize renders into a substituted manifest.
GENERATOR_SUFFIXES = (".json", ".py", ".toml", ".tpl")

CLUSTER_CONFIG = "kubernetes/infrastructure/sources/cluster-config.yaml"
ANSIBLE_ALL = "ansible/inventories/prod/group_vars/all.yml"
ANSIBLE_K3S = "ansible/inventories/prod/group_vars/k3s.yml"
ANSIBLE_DNS = "ansible/inventories/prod/group_vars/dns.yml"

RULES_TREE = "kubernetes/infrastructure/observability/rules"

# cluster-config key -> the literal it replaces. Domains match as a suffix (any
# hostname under them counts); everything else matches whole.
DOMAIN_KEYS = ("cluster_internal_domain", "cluster_external_domain")
# Addresses/CIDRs are exempt inside a NetworkPolicy and the rules tree.
ADDRESS_KEYS = (
    "cluster_lan_cidr",
    "cluster_pod_cidr",
    "cluster_service_cidr",
    "cluster_tailnet_cidr",
    "cluster_metallb_public_vip",
    "cluster_metallb_internal_vip",
    "cluster_wg_easy_vip",
    "cluster_api_vip",
)

# VIPs mirrored as AdGuard rewrite ANSWERS rather than as a named inventory key.
VIP_MIRROR_KEYS = ("cluster_metallb_public_vip", "cluster_metallb_internal_vip")

# cluster-config key -> where the same value lives in the Ansible inventory.
INVENTORY_MIRRORS = {
    "cluster_internal_domain": (ANSIBLE_ALL, "internal_domain"),
    "cluster_external_domain": (ANSIBLE_ALL, "external_domain"),
    "cluster_node_label_domain": (ANSIBLE_ALL, "internal_domain"),
    "cluster_pod_cidr": (ANSIBLE_K3S, "k3s_cluster_cidr"),
    "cluster_service_cidr": (ANSIBLE_K3S, "k3s_service_cidr"),
    # kube-vip is configured from the inventory side, so this one has a named
    # mirror rather than the AdGuard-answer treatment the MetalLB VIPs get.
    "cluster_api_vip": (ANSIBLE_K3S, "k3s_api_vip"),
}

DNS_SERVERS_KEY = "cluster_upstream_dns_servers"

# Every key an arm of this gate consumes. Each check is individually guarded by
# `if key not in config` — sensible per-arm, catastrophic in aggregate: delete
# three keys and the gate finds zero violations while checking substantially
# less, and still prints its full success line. So the key set is asserted up
# front and a disappearance is Vacuous (exit 2), never a quiet pass.
REQUIRED_KEYS = (
    set(DOMAIN_KEYS)
    | set(ADDRESS_KEYS)
    | set(VIP_MIRROR_KEYS)
    | set(INVENTORY_MIRRORS)
    | {DNS_SERVERS_KEY}
)


class Vacuous(Exception):
    """The gate could not inspect its subject — exit 2, never a silent pass."""


def substituted_trees(root: Path) -> tuple[str, ...]:
    """Every tree a Flux Kustomization renders with a cluster-config substitution.

    Derived from spec.path of the cluster's Kustomizations, so adding a stage is
    a one-file change here too. Paths nested inside another derived path are
    dropped (a stage carved out of a bigger tree would otherwise be walked
    twice), and a derived path missing from disk is a hard error rather than a
    quietly smaller scan.
    """
    cluster = root / CLUSTER_DIR
    if not cluster.is_dir():
        raise Vacuous(f"{CLUSTER_DIR} not found — cannot derive the substituted trees")
    derived: set[str] = set()
    for path in sorted(cluster.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
                continue
            if not str(doc.get("apiVersion", "")).startswith("kustomize.toolkit"):
                continue
            spec = doc.get("spec") or {}
            froms = (spec.get("postBuild") or {}).get("substituteFrom") or []
            if not any(f.get("name") == "cluster-config" for f in froms):
                continue
            tree = str(spec.get("path") or "").lstrip("./")
            if tree:
                derived.add(tree)
    if not derived:
        raise Vacuous(
            f"no Kustomization in {CLUSTER_DIR} substitutes from cluster-config — "
            "nothing would be scanned"
        )
    derived |= set(EXTRA_TREES)
    missing = sorted(t for t in derived if not (root / t).is_dir())
    if missing:
        raise Vacuous(f"substituted tree(s) declared but absent from disk: {', '.join(missing)}")
    # Drop nested paths: kubernetes/infrastructure/controllers/metrics-server is
    # already covered by kubernetes/infrastructure/controllers.
    return tuple(
        sorted(
            t
            for t in derived
            if not any(o != t and t.startswith(f"{o}/") for o in derived)
        )
    )


def load_config(root: Path) -> dict:
    try:
        doc = yaml.safe_load((root / CLUSTER_CONFIG).read_text()) or {}
    except OSError as exc:
        raise Vacuous(
            f"{CLUSTER_CONFIG} could not be read ({exc}) — the gate has no key set to check"
        ) from exc
    data = doc.get("data") or {}
    if not data:
        raise Vacuous(f"no data keys in {CLUSTER_CONFIG} — the gate has no key set to check")
    return {k: str(v) for k, v in data.items()}


def require_keys(config: dict) -> None:
    """Every key the gate's arms consume must be present, or it degrades silently."""
    missing = sorted(REQUIRED_KEYS - set(config))
    if missing:
        raise Vacuous(
            f"{CLUSTER_CONFIG} is missing key(s) this gate checks: {', '.join(missing)}. "
            "Each arm skips an absent key, so the gate would pass while enforcing less. "
            "Re-add the key, or delete it from the gate's key lists deliberately."
        )


def mirror_check_count(config: dict) -> int:
    """How many inventory-mirror assertions actually ran (never a constant)."""
    return (
        len([k for k in INVENTORY_MIRRORS if k in config])
        + len([k for k in VIP_MIRROR_KEYS if k in config])
        + (1 if DNS_SERVERS_KEY in config else 0)
    )


COMMENT_LINE = re.compile(r"^\s*#")


def strip_comments(text: str) -> str:
    """Drop comment-only lines from a scalar.

    A block scalar can carry a whole embedded file — the runners' TOML, a
    CoreDNS Corefile, an inline kustomize patch — whose comments PyYAML hands
    over as content. Prose naming the real domain belongs in a comment wherever
    it is written, so those lines are not scanned.
    """
    return "\n".join(line for line in text.split("\n") if not COMMENT_LINE.match(line))


def scalars(node):
    """Every scalar in a document, keys included."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from scalars(value)
    elif isinstance(node, list):
        for item in node:
            yield from scalars(item)
    elif node is not None:
        yield node


def domain_hits(text: str, domain: str) -> bool:
    """A literal use of `domain`, ignoring backslash-escaped (regex) spellings."""
    for match in re.finditer(re.escape(domain), text):
        start = match.start()
        # `esweiss\.com` — the dot before "com" is escaped, so this is a pattern.
        if "\\" in text[start : match.end()]:
            continue
        if start and text[start - 1] == "\\":
            continue
        return True
    return False


def escaped_free(text: str, domain: str) -> str:
    """Drop escaped spellings so a bare-domain search cannot see them."""
    return text.replace(domain.replace(".", "\\\\."), "").replace(
        domain.replace(".", "\\."), ""
    )


def scan_text(rel: str, text: str, domains: list[str], addresses: dict) -> list[str]:
    """Domain/address literals in a raw scalar or a whole generator file."""
    violations = []
    scalar = strip_comments(text)
    for domain in domains:
        if domain_hits(escaped_free(scalar, domain), domain):
            violations.append(
                f"{rel}: literal {domain!r} — use the cluster-config placeholder"
            )
    for literal, key in addresses.items():
        if re.search(rf"(?<![\d.]){re.escape(literal)}(?![\d.])", scalar):
            violations.append(f"{rel}: literal {literal!r} — use ${{{key}}}")
    return violations


def check_literals(root: Path, config: dict, trees: tuple[str, ...]) -> list[str]:
    violations = []
    domains = [config[k] for k in DOMAIN_KEYS if k in config]
    addresses = {config[k]: k for k in ADDRESS_KEYS if k in config}
    for tree in trees:
        base = root / tree
        if not base.is_dir():
            continue
        for path in sorted(
            p
            for p in base.rglob("*")
            if p.is_file() and p.suffix in GENERATOR_SUFFIXES
        ):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(RULES_TREE):
                continue
            violations += scan_text(rel, path.read_text(), domains, addresses)
        for path in sorted(base.rglob("*.yaml")):
            rel = path.relative_to(root).as_posix()
            in_rules = rel.startswith(RULES_TREE)
            try:
                docs = list(yaml.safe_load_all(path.read_text()))
            except yaml.YAMLError as exc:
                violations.append(f"{rel}: unparseable YAML ({exc.__class__.__name__})")
                continue
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                is_netpol = doc.get("kind") == "NetworkPolicy"
                # Addresses stay literal where a tool parses them pre-Flux.
                doc_addresses = {} if (is_netpol or in_rules) else addresses
                for raw in scalars(doc):
                    if not isinstance(raw, str):
                        continue
                    violations += scan_text(rel, raw, domains, doc_addresses)
    return sorted(set(violations))


def check_inventory(root: Path, config: dict) -> list[str]:
    violations = []
    cache: dict[str, dict] = {}
    for key, (path, var) in INVENTORY_MIRRORS.items():
        if key not in config:
            continue
        if path not in cache:
            cache[path] = yaml.safe_load((root / path).read_text()) or {}
        expected = cache[path].get(var)
        if expected is None:
            violations.append(f"{path}: {var} not found (cluster-config {key} has nothing to check)")
        elif str(expected) != config[key]:
            violations.append(
                f"cluster-config {key}={config[key]!r} != {path} {var}={expected!r}"
            )
    # The MetalLB VIPs have no single mirrored KEY in the inventory — they are
    # the answer side of AdGuard's rewrites, ~34 copies of them. cluster-config
    # declares itself their source of truth, so assert each VIP still appears
    # among those answers: move one in either file alone and it stops matching.
    dns_yml = yaml.safe_load((root / ANSIBLE_DNS).read_text()) or {}
    answers = {
        str(r.get("answer"))
        for r in (dns_yml.get("adguard_home_rewrites") or [])
        if isinstance(r, dict)
    }
    for key in VIP_MIRROR_KEYS:
        if key not in config:
            continue
        if config[key] not in answers:
            violations.append(
                f"cluster-config {key}={config[key]!r} answers no rewrite in "
                f"{ANSIBLE_DNS} (adguard_home_rewrites) — the VIP moved in one file only"
            )
    dns = (cache.get(ANSIBLE_ALL) or yaml.safe_load((root / ANSIBLE_ALL).read_text()) or {}).get(
        "dns_servers"
    )
    if DNS_SERVERS_KEY in config:
        # A missing mirror is a violation, not a skip — same rule the
        # INVENTORY_MIRRORS loop above applies. Silently dropping the check
        # because the inventory side vanished is exactly the drift it exists for.
        if dns is None:
            violations.append(
                f"{ANSIBLE_ALL}: dns_servers not found (cluster-config "
                f"{DNS_SERVERS_KEY} has nothing to check)"
            )
        else:
            expected = " ".join(str(x) for x in dns)
            if expected != config[DNS_SERVERS_KEY]:
                violations.append(
                    f"cluster-config {DNS_SERVERS_KEY}="
                    f"{config[DNS_SERVERS_KEY]!r} != {ANSIBLE_ALL} dns_servers={expected!r}"
                )
    return violations


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    root = Path(args.repo_root)

    try:
        config = load_config(root)
        require_keys(config)
        trees = substituted_trees(root)
    except Vacuous as exc:
        print(f"check-cluster-literals inspected nothing: {exc}", file=sys.stderr)
        return 2
    violations = check_literals(root, config, trees) + check_inventory(root, config)
    if violations:
        print("Cluster-identity literals / inventory drift:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print(
        f"Cluster identity is placeholder-only across {len(trees)} substituted trees "
        f"({', '.join(trees)}), and cluster-config agrees with the Ansible inventory "
        f"({mirror_check_count(config)} mirrored values checked)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
