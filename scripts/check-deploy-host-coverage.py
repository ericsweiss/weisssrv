#!/usr/bin/env python3
"""Assert every role a CI-deployed playbook declares actually reaches every host
that playbook declares it for.

check-deploy-coverage.sh answers "does SOME deploy job trigger on this path?".
That is a weaker question than it looks: a job can be credited for a role while
running it on a fraction of the hosts the inventory says need it. Two real
incidents came out of exactly that gap:

  * encrypted_swap was credited to deploy-ansible-base but missing from its
    `--tags` list, so the job went green on every role change while the role
    never executed anywhere (found only when a NAS reboot activated nothing).
  * nfs_tls is enabled on all six Proxmox hosts (group_vars/proxmox.yml) and is
    applied to the five non-NAS ones by site.yml, but no deploy job ran that
    tag. The path-level gate passed because deploy-ansible-storage lists the
    role — and storage.yml targets pve-nas-01 alone, one host of six.

So this gate computes, per role:

    declared = every host of every play that includes the role, across the
               playbooks CI actually deploys
    covered  = the hosts reached by deploy-stage invocations that select the
               role's tag (or run untagged)

and fails on a non-empty `declared - covered`.

Roles ship from the weisssrv.infra collection, so a role edit is a
`ansible/requirements.yml` bump rather than a path under this repo — the
"does the job trigger on the role's path" half moved to
check-deploy-coverage.sh's view of that file, and what remains here is the
host-reachability half (the nfs_tls shape: a job that triggers but --limit or
--tags away most of the hosts).

Deliberately static: it parses hosts.yml, the playbooks, and .gitlab-ci.yml. No
inventory plugin, no ansible, no cluster.

Exit codes: 0 covered, 1 gaps found, 2 the input could not be analysed (an
unparseable host pattern or a playbook a deploy job references but that does not
exist) — never a silent pass.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

# --repo exists so the unit tests can drive the gate against a fixture tree;
# every real invocation uses the default (the repo this script lives in).
REPO = Path(__file__).resolve().parent.parent
if "--repo" in sys.argv:
    REPO = Path(sys.argv[sys.argv.index("--repo") + 1]).resolve()
INVENTORY = REPO / "ansible/inventories/prod/hosts.yml"
PLAYBOOK_DIR = REPO / "ansible/playbooks"
CI_FILE = REPO / ".gitlab-ci.yml"

# Roles whose host-set gap is a deliberate, documented operating decision. Same
# contract as check-deploy-coverage.sh's INTENTIONALLY_UNMAPPED_* lists: every
# entry needs a rationale naming what deploys it instead. Keyed by role, valued
# with the reason (printed when the gate reports a skip).
ACKNOWLEDGED_GAPS = {
    # Node lifecycle (rolling cordon/upgrade, kernel reboots via kured) is
    # human-in-the-loop by design — `task k3s:deploy`. Already on
    # check-deploy-coverage.sh's INTENTIONALLY_UNMAPPED_ROLES.
    "k3s": "k3s node lifecycle is manual (task k3s:deploy)",
    "proxmox_vm": "VM provisioning is manual (task k3s:provision-vms and friends)",
    "proxmox_lxc": "LXC provisioning is manual (same reasoning as proxmox_vm)",
    "proxmox_ha": "HA rules / replication are manual (task proxmox:ha)",
    "zfs_encryption": "ZFS passphrase activation is a manual cold-boot operation",
}

# Runtime-only ledger groups. _reachability-probe.yml fills them by group_by with
# whatever a given run could not reach, and the deploy plays subtract them
# (`base_managed:!deploy_skipped`); hosts.yml declares them empty so the pattern
# does not warn. Excluding one must NOT shrink the declared host set: a host that
# one run happened to skip still needs a deploy job covering it. Every other
# exclusion/intersection still dies rather than being modelled wrongly.
RUNTIME_LEDGER_GROUPS = frozenset({"deploy_skipped", "deploy_reached", "deploy_lost"})


def die(message: str) -> None:
    """Exit 2 — the input could not be analysed, which is never a pass."""
    print(message, file=sys.stderr)
    raise SystemExit(2)


def load_yaml(path: Path):
    with path.open() as handle:
        return yaml.safe_load(handle)


def build_inventory() -> dict[str, set[str]]:
    """Map every group AND host name to the set of hosts it expands to.

    Two passes, because a group can be REFERENCED with an empty body before or
    after it is defined (`base_managed: {children: {proxmox:, dns:, mail:}}`
    names three groups defined further up). A single recursive walk would
    resolve those references to the empty set — and silently under-report the
    declared host set, which is the one thing this gate must never do.
    """
    data = load_yaml(INVENTORY)
    direct_hosts: dict[str, set[str]] = {}
    child_names: dict[str, set[str]] = {}

    def collect(name: str, body) -> None:
        body = body or {}
        direct_hosts.setdefault(name, set())
        child_names.setdefault(name, set())
        direct_hosts[name] |= set(body.get("hosts") or {})
        for child_name, child_body in (body.get("children") or {}).items():
            child_names[name].add(child_name)
            collect(child_name, child_body)

    collect("all", data.get("all", {}))

    resolved: dict[str, set[str]] = {}

    def resolve(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if name in resolved:
            return resolved[name]
        if name in seen:
            die(f"ERROR: inventory group cycle involving {name!r}")
        hosts = set(direct_hosts.get(name, set()))
        for child in child_names.get(name, set()):
            hosts |= resolve(child, seen | {name})
        resolved[name] = hosts
        return hosts

    groups = {name: resolve(name) for name in direct_hosts}
    for host in groups["all"]:
        groups.setdefault(host, {host})
    return groups


def expand(pattern: str | None, groups: dict[str, set[str]]) -> set[str]:
    """Expand an Ansible host pattern. Only the forms this repo uses."""
    if pattern is None:
        return set(groups["all"])
    hosts: set[str] = set()
    for token in re.split(r"[:,]", pattern):
        token = token.strip()
        if not token:
            continue
        if token == "localhost":
            # home-assistant.yml runs against the controller and delegates.
            hosts.add("localhost")
            continue
        if token.startswith("!") and token[1:] in RUNTIME_LEDGER_GROUPS:
            continue
        if token.startswith("!") or token.startswith("&"):
            die(
                f"ERROR: host pattern {pattern!r} uses an exclusion/intersection "
                f"this gate does not model. Teach it the form or split the play."
            )
        if token not in groups:
            die(f"ERROR: host pattern {pattern!r} references unknown group/host {token!r}")
        hosts |= groups[token]
    return hosts


def parse_playbook(rel_path: str) -> list[dict]:
    """Return [{hosts, roles: {role: tags}}] for one playbook."""
    path = PLAYBOOK_DIR / rel_path
    if not path.exists():
        die(f"ERROR: deploy job references {path}, which does not exist")
    plays = []
    for play in load_yaml(path) or []:
        if not isinstance(play, dict) or "hosts" not in play:
            continue
        play_tags = set(as_list(play.get("tags")))
        roles: dict[str, set[str]] = {}
        for entry in play.get("roles") or []:
            if isinstance(entry, str):
                name, tags = entry, set()
            elif isinstance(entry, dict) and "role" in entry:
                name, tags = entry["role"], set(as_list(entry.get("tags")))
            else:
                continue
            roles.setdefault(name, set())
            roles[name] |= tags | play_tags
        if roles:
            plays.append({"hosts": str(play["hosts"]), "roles": roles})
    return plays


def as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def parse_ci() -> list[dict]:
    """Return one entry per deploy-stage ansible-playbook invocation."""

    def passthrough(loader, tag_suffix, node):
        # `!reference [job, key]` appears throughout rules:; returning None makes
        # the entry a non-dict the walker skips (same trick as
        # check-deploy-coverage.sh) instead of failing the safe loader.
        return None

    yaml.SafeLoader.add_multi_constructor("!", passthrough)
    ci = yaml.safe_load(CI_FILE.read_text())

    invocations = []
    for job_name, job in ci.items():
        if not isinstance(job, dict) or not job_name.startswith("deploy-"):
            continue
        if job.get("stage") != "deploy":
            continue
        changes: set[str] = set()
        for rule in job.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            rule_changes = rule.get("changes") or []
            if isinstance(rule_changes, dict):
                rule_changes = rule_changes.get("paths") or []
            changes |= {c for c in rule_changes if isinstance(c, str)}
        for line in job.get("script") or []:
            if not isinstance(line, str) or "ansible-playbook" not in line:
                continue
            match = re.search(r"playbooks/(\S+\.ya?ml)", line)
            if not match:
                continue
            limit = re.search(r"--limit\s+(\S+)", line)
            tags = re.search(r"--tags\s+(\S+)", line)
            invocations.append(
                {
                    "job": job_name,
                    "playbook": match.group(1),
                    "limit": limit.group(1) if limit else None,
                    "tags": set(tags.group(1).split(",")) if tags else None,
                    "changes": changes,
                }
            )
    return invocations


def main() -> int:
    groups = build_inventory()
    invocations = parse_ci()
    if not invocations:
        print("ERROR: no deploy-stage ansible-playbook invocations found in .gitlab-ci.yml", file=sys.stderr)
        return 2

    playbooks = {inv["playbook"] for inv in invocations}
    parsed = {name: parse_playbook(name) for name in sorted(playbooks)}

    declared: dict[str, set[str]] = {}
    covered: dict[str, set[str]] = {}
    untriggered: dict[str, set[str]] = {}

    for plays in parsed.values():
        for play in plays:
            play_hosts = expand(play["hosts"], groups)
            for role in play["roles"]:
                declared.setdefault(role, set())
                declared[role] |= play_hosts

    for inv in invocations:
        for play in parsed[inv["playbook"]]:
            play_hosts = expand(play["hosts"], groups)
            # No --limit means the play runs on everything it targets, including
            # the controller-only `hosts: localhost` plays that no inventory
            # group expands to.
            reached = play_hosts if inv["limit"] is None else play_hosts & expand(inv["limit"], groups)
            for role, tags in play["roles"].items():
                if inv["tags"] is not None and not (inv["tags"] & tags):
                    continue
                covered.setdefault(role, set())
                covered[role] |= reached

    failures = []
    for role in sorted(declared):
        if role in ACKNOWLEDGED_GAPS:
            continue
        gap = declared[role] - covered.get(role, set())
        if gap:
            failures.append((role, gap, sorted(untriggered.get(role, set()))))

    if failures:
        print("ERROR: roles that CI deploys to only part of the host set they are declared for:\n", file=sys.stderr)
        for role, gap, _jobs in failures:
            print(f"  {role} — unreached hosts: {', '.join(sorted(gap))}", file=sys.stderr)
            print(
                "      (no deploy-stage invocation selects it for those hosts —"
                " check the job's --limit and --tags)",
                file=sys.stderr,
            )
        print(
            "\nResolution: extend the relevant deploy job's --tags/--limit,"
            "\nor add the role to ACKNOWLEDGED_GAPS in scripts/check-deploy-host-coverage.py with"
            "\na rationale naming what deploys it instead.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(declared)} roles in CI-deployed playbooks reach every host they are declared for.")
    for role, reason in sorted(ACKNOWLEDGED_GAPS.items()):
        if role in declared:
            print(f"  (skipped {role}: {reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
