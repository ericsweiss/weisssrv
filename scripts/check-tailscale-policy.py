#!/usr/bin/env python3
"""Validate terraform/tailscale/policy.hujson before the supervised apply.

The module reads policy.hujson with `file()`, so `terraform fmt/validate` never
parse it and the only other reader is the Tailscale API during the supervised
apply — the one place a mistake must not be discovered, because a wrong policy
severs tailnet SSH.

Three assertions, cheapest first:

  1. It parses as HuJSON (comments + trailing commas stripped) and carries the
     five top-level keys the module and README describe.
  2. Every `tag:` referenced anywhere — acls, ssh, autoApprovers, and the
     tagOwners values themselves — is a tagOwners KEY. An undeclared tag is
     accepted by nothing: devices carrying it match no rule.
  3. `autoApprovers.routes` covers exactly the CIDRs the inventory advertises
     (`tailscale_advertise_routes`). A route advertised but not auto-approved
     needs a manual admin approval on every failover, which is the thing the
     auto-approver exists to remove; an auto-approved route nothing advertises
     is stale policy.

Run through `task lint:tailscale-policy` (CI's repo-policy-checks calls the same
task), so there is one implementation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
POLICY = "terraform/tailscale/policy.hujson"
INVENTORY_GLOBS = (
    "ansible/inventories/prod/group_vars/*.yml",
    "ansible/inventories/prod/host_vars/*.yml",
)
REQUIRED_KEYS = ("groups", "tagOwners", "acls", "ssh", "autoApprovers")

# `tag:name` anywhere in a policy string. A dst is written `tag:k8s:53,443`, so
# the port suffix has to be excluded rather than assumed absent.
TAG_RE = re.compile(r"tag:[A-Za-z0-9][A-Za-z0-9-]*")
BACKSLASH = chr(92)


def strip_hujson(src: str) -> str:
    """Drop HuJSON's comment and trailing-comma extensions.

    Hand-rolled scanner, not a regex: `//` inside a string value (every https://
    URL) must not start a comment. Two passes, because in the raw source the
    character after a trailing comma is often the `/` of a comment.
    """
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(src):
        c = src[i]
        if in_str:
            out.append(c)
            if c == BACKSLASH:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            i = src.find("\n", i)
            if i == -1:
                break
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            if end == -1:
                raise ValueError("unterminated block comment")
            i = end + 2
            continue
        out.append(c)
        i += 1

    stripped = "".join(out)
    kept: list[str] = []
    in_str = False
    i = 0
    while i < len(stripped):
        c = stripped[i]
        if in_str:
            kept.append(c)
            if c == BACKSLASH:
                kept.append(stripped[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == ",":
            j = i + 1
            while j < len(stripped) and stripped[j].isspace():
                j += 1
            if j < len(stripped) and stripped[j] in "}]":
                i += 1
                continue
        kept.append(c)
        i += 1
    return "".join(kept)


def _strings(node) -> list[str]:
    """Every string in the document, keys included — a tag may appear as either."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _strings(k) + _strings(v)]
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return []


def advertised_routes(root: Path = REPO) -> set[str]:
    """Every CIDR any inventory group/host advertises as a subnet route."""
    routes: set[str] = set()
    for pattern in INVENTORY_GLOBS:
        for path in sorted(root.glob(pattern)):
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(doc, dict):
                continue
            for cidr in doc.get("tailscale_advertise_routes") or []:
                routes.add(str(cidr))
    return routes


def check(root: Path = REPO) -> list[str]:
    path = root / POLICY
    try:
        doc = json.loads(strip_hujson(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        return [f"{POLICY} is not valid HuJSON: {exc}"]
    if not isinstance(doc, dict):
        return [f"{POLICY} must be a JSON object"]

    problems: list[str] = []
    missing = [k for k in REQUIRED_KEYS if k not in doc]
    if missing:
        problems.append(f"{POLICY} is missing top-level key(s): {', '.join(missing)}")
        # The two checks below index those keys; without them there is nothing
        # meaningful left to say.
        return problems

    declared = {t for t in doc["tagOwners"] if isinstance(t, str)}
    referenced = {m for s in _strings(doc) for m in TAG_RE.findall(s)}
    undeclared = sorted(referenced - declared)
    if undeclared:
        problems.append(
            f"{POLICY} references tag(s) with no tagOwners entry: "
            f"{', '.join(undeclared)}. A tag nothing owns can be applied by "
            "nobody, so every rule naming it is dead."
        )

    routes = (doc["autoApprovers"] or {}).get("routes") or {}
    # A route key mapped to an empty approver list passes a key-existence check
    # while approving nothing at failover.
    bad = sorted(
        cidr for cidr, approvers in routes.items()
        if not isinstance(approvers, list) or not approvers
        or any(not isinstance(a, str) or not a for a in approvers)
    )
    if bad:
        problems.append(
            f"{POLICY} autoApprovers.routes maps {', '.join(bad)} to something "
            "other than a nonempty list of approver strings — the route is "
            "named but nothing can auto-approve it."
        )
    approved = set(routes)
    advertised = advertised_routes(root)
    if approved != advertised:
        problems.append(
            f"{POLICY} autoApprovers.routes is {sorted(approved)} but the "
            f"inventory's tailscale_advertise_routes is {sorted(advertised)}. "
            "Un-approved advertised routes need a manual admin approval on every "
            "subnet-router failover; approved-but-unadvertised routes are stale."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo", type=Path, default=REPO)
    args = ap.parse_args(argv)

    problems = check(args.repo)
    if problems:
        print("check-tailscale-policy: FAILED", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    doc = json.loads(strip_hujson((args.repo / POLICY).read_text(encoding="utf-8")))
    print(
        f"check-tailscale-policy: OK — top-level keys "
        f"{', '.join(sorted(doc))}; tags {', '.join(sorted(doc['tagOwners']))}; "
        f"auto-approved routes {', '.join(sorted(doc['autoApprovers']['routes']))}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
