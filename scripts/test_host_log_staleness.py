#!/usr/bin/env python3
"""Drift guard for the HostLogShippingStale alert host set.

kubernetes/infrastructure/observability/loki/host-log-staleness.yaml hand-lists
one `HostLogShippingStale` rule per host that runs the `alloy_host` role (host
journald → Loki). That list must stay in lockstep with the hosts the
`alloy_host` play in ansible/playbooks/site.yml actually targets — a host added
to the play but not the alert silently loses its "shipping stopped" page.

This test derives the expected host set the SAME WAY site.yml does — nothing is
hardcoded:

  1. Load site.yml, find the play whose `roles` includes `alloy_host`, read that
     play's `hosts:` group-union expression (Ansible's `:` operator).
  2. Load the prod inventory (hosts.yml) and recursively expand each group token
     (groups-of-groups → children → concrete inventory_hostnames).
  3. Assert that set exactly equals the set of `host` label values across the
     alert rules.

home-assistant/HAOS and the Windows VM are excluded purely by group
non-membership (they carry `ansible_connection: local` and cannot run
alloy_host) — the derivation drops them for free because the play's `hosts:`
expression never references their groups.

Run with pytest:
    pytest scripts/test_host_log_staleness.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SITE_YML = REPO / "ansible" / "playbooks" / "site.yml"
HOSTS_YML = REPO / "ansible" / "inventories" / "prod" / "hosts.yml"
ALERT_YML = (
    REPO
    / "kubernetes"
    / "infrastructure"
    / "observability"
    / "loki"
    / "host-log-staleness.yaml"
)
RULES_INFRASTRUCTURE_YML = (
    REPO
    / "kubernetes"
    / "infrastructure"
    / "observability"
    / "rules"
    / "infrastructure.yaml"
)

ALLOY_HOST_ROLE = "alloy_host"


# Derivation helpers (no hardcoded host / group names)

def _role_names(play: dict) -> list[str]:
    """Short role names in a play's `roles:` list (dict or bare-string form).

    Roles ship FQCN'd from the weisssrv.infra collection, so the namespace is
    stripped — the alert list is keyed on the bare role name.
    """
    names: list[str] = []
    for entry in play.get("roles") or []:
        role = entry.get("role") if isinstance(entry, dict) else entry
        if isinstance(role, str) and role:
            names.append(role.rsplit(".", 1)[-1])
    return names


def alloy_host_target_groups(site_yml: Path) -> list[str]:
    """The group tokens the alloy_host play targets, from its `hosts:` union.

    Finds the single play whose roles include `alloy_host` and splits its
    `hosts:` expression on Ansible's `:` group-union operator.

    `!group` exclusion tokens are dropped: the only one in play is
    `!deploy_skipped`, a ledger group that _reachability-probe.yml fills at
    runtime and that is always empty in the static inventory. It subtracts
    hosts that a given run could not reach, which is exactly the set the
    HostLogShippingStale alert still has to cover — so the expected set is the
    unexcluded union.
    """
    plays = yaml.safe_load(site_yml.read_text())
    matches = [
        p for p in plays
        if isinstance(p, dict) and ALLOY_HOST_ROLE in _role_names(p)
    ]
    assert matches, f"no play in {site_yml.name} applies the {ALLOY_HOST_ROLE} role"
    assert len(matches) == 1, (
        f"expected exactly one {ALLOY_HOST_ROLE} play in {site_yml.name}, "
        f"found {len(matches)}"
    )
    hosts_expr = matches[0]["hosts"]
    # `hosts:` is a string here (a `:`-joined union); tolerate a YAML list too.
    tokens: list[str] = []
    parts = hosts_expr if isinstance(hosts_expr, list) else [hosts_expr]
    for part in parts:
        tokens.extend(
            t for t in str(part).split(":") if t and not t.startswith("!")
        )
    return tokens


def _build_group_index(inventory: dict) -> dict[str, dict]:
    """Flat map of group name → its definition (the occurrence carrying content).

    Ansible defines each group once (with `hosts`/`children`) and elsewhere
    references it by name with a null value; we index only the definitional
    occurrence so a null reference never shadows the real one.
    """
    index: dict[str, dict] = {}

    def walk(name: str, defn) -> None:
        if not isinstance(defn, dict):
            return
        if defn.get("hosts") or defn.get("children"):
            index[name] = defn
        for child_name, child_def in (defn.get("children") or {}).items():
            walk(child_name, child_def)

    root = inventory.get("all", {})
    for child_name, child_def in (root.get("children") or {}).items():
        walk(child_name, child_def)
    return index


def _resolve_hosts(name: str, index: dict[str, dict], seen: set[str] | None = None) -> set[str]:
    """Concrete inventory_hostnames under a group, expanding children recursively."""
    seen = seen if seen is not None else set()
    if name in seen:
        return set()
    seen.add(name)
    defn = index.get(name, {})
    hosts = set((defn.get("hosts") or {}).keys())
    for child in (defn.get("children") or {}):
        hosts |= _resolve_hosts(child, index, seen)
    return hosts


def expected_alloy_host_set() -> set[str]:
    """The alloy_host host set, derived from site.yml + hosts.yml."""
    groups = alloy_host_target_groups(SITE_YML)
    inventory = yaml.safe_load(HOSTS_YML.read_text())
    index = _build_group_index(inventory)
    hosts: set[str] = set()
    for group in groups:
        resolved = _resolve_hosts(group, index)
        assert resolved, f"group {group!r} resolved to no hosts (inventory drift?)"
        hosts |= resolved
    return hosts


# Alert-file parsing

def _alert_rules() -> list[dict]:
    doc = yaml.safe_load(ALERT_YML.read_text())
    rules: list[dict] = []
    for group in doc.get("groups", []):
        rules.extend(group.get("rules", []))
    return rules


def alert_host_labels() -> set[str]:
    return {r["labels"]["host"] for r in _alert_rules()}


# Tests

class TestHostSetInSync:
    def test_alert_hosts_equal_alloy_host_inventory_set(self):
        expected = expected_alloy_host_set()
        actual = alert_host_labels()
        assert actual == expected, (
            "host-log-staleness.yaml host set drifted from the alloy_host play.\n"
            f"  missing rules (in play, not alerted): {sorted(expected - actual)}\n"
            f"  stale rules (alerted, not in play):   {sorted(actual - expected)}"
        )

    def test_one_rule_per_host_no_duplicates(self):
        hosts = [r["labels"]["host"] for r in _alert_rules()]
        dupes = sorted({h for h in hosts if hosts.count(h) > 1})
        assert not dupes, f"duplicate HostLogShippingStale rules for: {dupes}"


class TestRuleShape:
    def test_every_rule_has_expr_for_and_severity(self):
        for rule in _alert_rules():
            host = rule.get("labels", {}).get("host", "<no host label>")
            assert rule.get("expr"), f"rule for {host} missing expr"
            assert rule.get("for"), f"rule for {host} missing for:"
            assert rule.get("labels", {}).get("severity"), (
                f"rule for {host} missing labels.severity"
            )

    def test_expr_selector_matches_host_label(self):
        # The absent_over_time selector must target the same host it labels.
        for rule in _alert_rules():
            host = rule["labels"]["host"]
            assert f'host="{host}"' in rule["expr"], (
                f'rule labelled host={host} does not select host="{host}" in its expr'
            )

    def test_every_rule_is_actionable(self):
        # Same convention as every custom alert in kube-prometheus-stack: a
        # runbook link and a description saying what to do, not just what broke.
        for rule in _alert_rules():
            host = rule["labels"]["host"]
            annotations = rule.get("annotations") or {}
            assert annotations.get("runbook_url"), f"rule for {host} missing runbook_url"
            assert annotations.get("description"), f"rule for {host} missing description"


class TestRulerMetaAlertThreshold:
    """LokiRulerRulesMissing counts these rules, and lives in another file.

    The Loki ruler pushes these alerts straight to Alertmanager, so Prometheus
    cannot see them fail; the only watchdog is a Prometheus alert comparing the
    ruler's loaded rule count against the number shipped here. That threshold is
    a literal in observability/rules/infrastructure.yaml — this test is what
    keeps it from silently under-detecting when a host is added.
    """

    THRESHOLD_ALERT = "LokiRulerRulesMissing"

    def _threshold(self) -> int:
        rules_cr = yaml.safe_load(RULES_INFRASTRUCTURE_YML.read_text())
        for group in rules_cr["spec"]["groups"]:
            for rule in group.get("rules", []):
                if rule.get("alert") == self.THRESHOLD_ALERT:
                    match = re.search(
                        r"loki_prometheus_rule_group_rules\{[^}]*\}[)\s]*<\s*(\d+)",
                        rule["expr"],
                    )
                    assert match, (
                        f"{self.THRESHOLD_ALERT} no longer compares "
                        f"loki_prometheus_rule_group_rules against a literal count"
                    )
                    return int(match.group(1))
        raise AssertionError(
            f"{self.THRESHOLD_ALERT} is missing from "
            f"{RULES_INFRASTRUCTURE_YML.name} — the Loki ruler alert path would "
            f"have no meta-monitoring at all"
        )

    def test_threshold_equals_shipped_rule_count(self):
        shipped = len(_alert_rules())
        assert self._threshold() == shipped, (
            f"{self.THRESHOLD_ALERT} expects {self._threshold()} ruler rules but "
            f"host-log-staleness.yaml ships {shipped}. Update the threshold in "
            f"observability/rules/infrastructure.yaml, or the alert under-detects "
            f"a partially-delivered rules ConfigMap."
        )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
