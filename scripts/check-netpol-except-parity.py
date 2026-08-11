#!/usr/bin/env python3
"""Assert every public-egress NetworkPolicy uses a canonical reserved-CIDR except-list.

Public egress in this repo is written as `ipBlock: {cidr: 0.0.0.0/0, except: [...]}`.
The except-list is what stops a compromised pod from reaching the LAN, and it is
repeated in every policy that needs it — kustomize has no mechanism to share a
list fragment, and Flux postBuild substitution would need the value in the
generated cluster-versions ConfigMap. So the lists are compared instead: each one
must match a named canonical list exactly, in order.

Adding a CIDR to a canonical list here and to the policies is a deliberate,
reviewable act; a hand-edited or half-updated copy fails this check.

Comparing only the lists that are STILL THERE would miss the single edit that
most directly re-opens the LAN: dropping the `except:` key altogether. So an
egress peer whose ipBlock is a /0 must carry a canonical except-list — an absent
or empty one is a violation, not a skip. Ingress is exempt: an unfenced
`0.0.0.0/0` ingress peer is a deliberate shape here (wg-easy's WAN endpoint).

That is still only the "the except-list drifted" edit. The invariant that
actually matters is "no fenced pod has unrestricted egress", and there are two
ways to reach it without touching an except-list at all — both of which this
check used to pass silently, because it only ever looked at ipBlock peers that
existed:

  1. delete the whole `to:` (`- {}`, or a rule carrying only `ports:`). A NetworkPolicy
     egress rule with no peers allows egress to EVERY destination, so this is
     strictly more open than a /0 ipBlock with an except-list — and it leaves no
     ipBlock for a peer-shaped check to find;
  2. reach a fenced range through blocks that individually look specific:
     `0.0.0.0/1` + `128.0.0.0/1` is `0.0.0.0/0` written so that
     `endswith("/0")` is false for both halves, and a lone `192.168.0.0/16`
     peer hands a pod the entire LAN while never going near a /0 at all.

So the check is peer-shaped AND rule-shaped. Per egress rule it also computes
the address space the rule's ipBlocks actually reach (each cidr minus its OWN
excepts) and fails when a whole fenced range still fits inside it. The test is
containment, not total coverage — a rule need not reach everything to hand a pod
the LAN. Rules that legitimately allow a LAN /32 are untouched: a /32 contains no
fence range, so the coverage arm has nothing to report on them.

Unrestricted egress that IS intended (privileged CI job pods, Flux's own
upstream-shipped policy) is allowlisted BY NAME in UNRESTRICTED_EGRESS_OK, with
the reason — an exemption a reviewer can see beats a check that cannot see the
shape at all.

Usage: scripts/check-netpol-except-parity.py [path ...]   (default: kubernetes/)
"""
from __future__ import annotations

import ipaddress
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

REPO = Path(__file__).resolve().parent.parent

# Full reserved-space list. Used by the platform policies whose egress is a
# narrow public-API call (Cloudflare, ACME, 1Password cloud).
RESERVED_FULL = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "0.0.0.0/8",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
]

# LAN-fence list. Used by app policies that need broad internet egress and only
# have to be kept off the LAN, Tailscale and the metadata address.
LAN_FENCE = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
]

CANONICAL = {"reserved-full": RESERVED_FULL, "lan-fence": LAN_FENCE}

# The ranges no egress rule may reach IN FULL. LAN_FENCE is the v4 half (it is
# exactly what every canonical
# list fences off); the v6 entries are its analogue, so an `::/0` egress written
# in a future policy cannot slip past a v4-only test.
FENCE_NETS = [ipaddress.ip_network(c) for c in LAN_FENCE] + [
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Egress rules that deliberately have no peers, i.e. allow egress everywhere.
# Keyed "<namespace>/<name>"; the value is why. Anything not listed here that
# omits `to:` is the accidental version of the same edit.
UNRESTRICTED_EGRESS_OK = {
    "gitlab-runner-privileged/infrastructure-jobs-egress": (
        "infrastructure job pods deploy the homelab itself (ansible over SSH, "
        "kubectl, 1Password) — unrestricted egress is the point of the "
        "privileged runner class, and the namespace's default-deny-egress makes "
        "every pod WITHOUT the runner-class label fail closed"
    ),
    "flux-system/allow-egress": (
        "shipped verbatim inside the upstream gotk-components manifest; Flux's "
        "controllers reach arbitrary git/OCI/Helm origins. Editing it here would "
        "be reverted by the next `flux install` regeneration"
    ),
}


def _policy_key(doc) -> str:
    meta = doc.get("metadata") or {}
    return f"{meta.get('namespace') or '<no-namespace>'}/{meta.get('name', '<unnamed>')}"


def has_egress(spec) -> bool:
    """Whether the policy actually restricts egress.

    Mirrors the API's own derivation: an omitted policyTypes is inferred from
    the rules present, so a policy with `egress:` and no `policyTypes:` is an
    Egress policy — and its peer-less rule opens exactly as much.
    """
    declared = spec.get("policyTypes")
    if declared is None:
        return bool(spec.get("egress"))
    return "Egress" in declared


def _nets(cidrs):
    """Parse cidrs, dropping (and reporting) the unparseable ones."""
    parsed, bad = [], []
    for cidr in cidrs:
        try:
            parsed.append(ipaddress.ip_network(cidr, strict=False))
        except (ValueError, TypeError):
            bad.append(cidr)
    return parsed, bad


def _exclude(nets, cuts):
    """nets minus cuts. Two CIDRs are always disjoint or nested, so each cut
    either removes a network entirely, splits it, or does not touch it."""
    remaining = list(nets)
    for cut in cuts:
        nxt = []
        for net in remaining:
            if net.version != cut.version:
                nxt.append(net)
            elif net.subnet_of(cut):
                continue
            elif cut.subnet_of(net):
                nxt.extend(net.address_exclude(cut))
            else:
                nxt.append(net)
        remaining = nxt
    return remaining


def unfenced_reach(blocks):
    """The fence ranges an egress rule can still reach, [] when properly fenced.

    `blocks` is [(cidr, [except, ...]), ...] — one entry per ipBlock peer, and
    the excepts stay WITH their own peer. Pooling them across the rule would be
    strictly wrong in the permissive direction: an `except:` narrows only the
    ipBlock it is written in, so a sibling peer with no except re-opens
    everything its neighbour just fenced off.

    The test is CONTAINMENT, not total coverage: a rule is unfenced when a whole
    fence range still fits inside what it allows. That subsumes the /0 written
    as two /1s (both halves together contain every fence) and the shape a
    coverage test cannot see at all — a single narrower block, `192.168.0.0/16`,
    that reaches the whole LAN without going near the rest of the address space.

    A rule allowing part of a fence range — the LAN /32s most policies in this
    repo use — is a deliberate shape and never contains a fence, so it is not
    reported here. A /0 whose except-list is merely wrong (a partially fenced
    range) is owned by the per-peer canonical-list arm instead.
    """
    allowed = []
    for cidr, excepts in blocks:
        net, _ = _nets([cidr])
        cuts, _ = _nets(excepts)
        allowed.extend(_exclude(net, cuts))
    return sorted(
        {
            str(fence)
            for fence in FENCE_NETS
            for net in allowed
            if net.version == fence.version and fence.subnet_of(net)
        }
    )


def iter_egress_rules(doc):
    """Yield (key, index, rule) for every egress rule of an Egress policy."""
    if not isinstance(doc, dict) or doc.get("kind") != "NetworkPolicy":
        return
    spec = doc.get("spec") or {}
    if not has_egress(spec):
        return
    key = _policy_key(doc)
    for index, rule in enumerate(spec.get("egress") or []):
        if isinstance(rule, dict):
            yield key, index, rule


def iter_ip_blocks(doc):
    """Yield (policy_name, direction, cidr, except_list) for every ipBlock peer.

    except_list is [] when the key is absent or empty — the deletion case the
    check has to be able to see.
    """
    if not isinstance(doc, dict) or doc.get("kind") != "NetworkPolicy":
        return
    name = (doc.get("metadata") or {}).get("name", "<unnamed>")
    spec = doc.get("spec") or {}
    for direction, key in (("egress", "to"), ("ingress", "from")):
        for rule in spec.get(direction) or []:
            for peer in rule.get(key) or []:
                if not isinstance(peer, dict):
                    continue
                block = peer.get("ipBlock")
                if not isinstance(block, dict):
                    continue
                yield name, direction, block.get("cidr"), list(block.get("except") or [])


def is_default_route(cidr):
    """True for a /0 ipBlock — the peer shape an except-list is mandatory on."""
    return isinstance(cidr, str) and cidr.strip().endswith("/0")


def classify(except_list):
    """Return the canonical list name this matches, or None."""
    for label, canonical in CANONICAL.items():
        if except_list == canonical:
            return label
    return None


def check_paths(paths):
    """Return a list of human-readable violations."""
    violations = []
    for root in paths:
        root = Path(root)
        if root.is_dir():
            files = sorted(p for ext in ("*.yaml", "*.yml") for p in root.rglob(ext))
        else:
            files = [root]
        for path in files:
            try:
                docs = list(yaml.safe_load_all(path.read_text()))
            except yaml.YAMLError as e:
                violations.append(f"{path}: unparseable YAML: {e}")
                continue
            for doc in docs:
                # (a) A peer-less egress rule allows egress to everything, and
                # leaves no ipBlock behind for the per-peer arms to inspect.
                for key, index, rule in iter_egress_rules(doc):
                    if not rule.get("to"):
                        if key not in UNRESTRICTED_EGRESS_OK:
                            violations.append(
                                f"{path}: NetworkPolicy {key} egress rule "
                                f"[{index}] has no `to:` peers — that allows "
                                f"egress to EVERY destination, LAN included, "
                                f"which is strictly more open than a /0 ipBlock "
                                f"with no except-list. Add peers, or declare the "
                                f"exemption in UNRESTRICTED_EGRESS_OK with its "
                                f"reason."
                            )
                        continue
                    # (b) The peers may still reach a whole fenced range
                    # without any single one of them being a /0.
                    blocks = []
                    for peer in rule.get("to") or []:
                        block = peer.get("ipBlock") if isinstance(peer, dict) else None
                        if isinstance(block, dict):
                            blocks.append(
                                (block.get("cidr"), list(block.get("except") or []))
                            )
                    cidrs = [cidr for cidr, _ in blocks]
                    # A rule built only from literal /0 peers is already fully
                    # owned by the per-peer arm below, which names the offending
                    # except-list; reporting it twice would just be noise. A
                    # MIXED rule (a fenced /0 plus a bare half) is not owned by
                    # either arm alone, so it still runs through the containment
                    # test.
                    all_default = cidrs and all(is_default_route(c) for c in cidrs)
                    reachable = [] if all_default else unfenced_reach(blocks)
                    if reachable and key not in UNRESTRICTED_EGRESS_OK:
                        violations.append(
                            f"{path}: NetworkPolicy {key} egress rule [{index}] "
                            f"reaches all of {', '.join(reachable)} via {cidrs}. "
                            f"A fenced range reached in full is a LAN escape "
                            f"however it is spelled — one narrower block or a /0 "
                            f"split into halves — so fence it with the canonical "
                            f"lan-fence (or reserved-full) except-list, or narrow "
                            f"the peer to the addresses actually needed."
                        )
                for name, direction, cidr, except_list in iter_ip_blocks(doc):
                    if not except_list:
                        # Only egress /0 is required to be fenced; everything
                        # else legitimately has no except-list.
                        if direction == "egress" and is_default_route(cidr):
                            violations.append(
                                f"{path}: NetworkPolicy {name} has an EGRESS "
                                f"ipBlock {cidr} with no except-list — that is "
                                f"unrestricted egress to the LAN, loopback and "
                                f"cloud-metadata ranges. Add the canonical "
                                f"lan-fence (or reserved-full) list."
                            )
                        continue
                    if classify(except_list):
                        continue
                    violations.append(
                        f"{path}: NetworkPolicy {name} ({direction} ipBlock {cidr}) "
                        f"has a non-canonical except-list: {except_list}"
                    )
    return violations


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    paths = argv or [REPO / "kubernetes"]
    violations = check_paths(paths)
    if violations:
        print("ERROR: NetworkPolicy except-lists have drifted from the canonical sets:")
        for v in violations:
            print(f"  - {v}")
        print("Canonical sets are defined in scripts/check-netpol-except-parity.py.")
        return 1
    print(
        "NetworkPolicy egress is fenced: every /0 peer carries a canonical "
        "except-list, no egress rule reaches a fenced range in full, "
        f"and the {len(UNRESTRICTED_EGRESS_OK)} peer-less rule(s) are declared."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
