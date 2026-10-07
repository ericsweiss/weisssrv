"""Drift guard for the HostLogShippingStale alert host set.

The rules in observability/loki/host-log-staleness.yaml must cover exactly the
hosts the `alloy_host` play targets, derived by the generator that writes them.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gen = load_script("generate-host-log-staleness.py")

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


def expected_alloy_host_set() -> set[str]:
    """The alloy_host host set, derived by the generator that writes the file."""
    return set(gen.alloy_hosts(REPO))


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
    """The LokiRulerRulesMissing threshold matches the rule count shipped here."""

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

    def _extra_ruler_rules(self) -> int:
        # Every other rule file the kustomization ships counts toward the total
        # the threshold watches. Counting one it does NOT ship would inflate the
        # threshold and latch the meta-alert permanently.
        loki_dir = ALERT_YML.parent
        kustomization = yaml.safe_load((loki_dir / "kustomization.yaml").read_text())
        shipped_files = {
            f
            for gen in kustomization.get("configMapGenerator", [])
            for f in gen.get("files", [])
        }
        extra = 0
        for f in loki_dir.glob("*.yaml"):
            if f.name in (ALERT_YML.name, "kustomization.yaml"):
                continue
            for doc in yaml.safe_load_all(f.read_text()):
                if isinstance(doc, dict) and "groups" in doc:
                    assert f.name in shipped_files, (
                        f"{f.name} holds ruler rules but no configMapGenerator entry "
                        f"in loki/kustomization.yaml ships it — the sidecar will never "
                        f"deliver it and LokiRulerRulesMissing would fire forever."
                    )
                    extra += sum(len(g.get("rules", [])) for g in doc["groups"])
        return extra

    def test_threshold_equals_shipped_rule_count(self):
        shipped = len(_alert_rules()) + self._extra_ruler_rules()
        assert self._threshold() == shipped, (
            f"{self.THRESHOLD_ALERT} expects {self._threshold()} ruler rules but "
            f"the loki/ rule files ship {shipped}. Update the threshold in "
            f"observability/rules/infrastructure.yaml, or the alert under-detects "
            f"a partially-delivered rules ConfigMap."
        )

    def test_the_dashboard_green_threshold_equals_the_same_count(self):
        """The `Ruler rules loaded` stat panel is read the same way the alert
        is, so a shipped rule that moves one must move the other."""
        import json

        dashboard = json.loads(
            (
                REPO / "kubernetes/infrastructure/observability/dashboards"
                / "alerts-overview.json"
            ).read_text()
        )

        def walk(panels):
            for panel in panels:
                yield panel
                yield from walk(panel.get("panels") or [])

        matches = [
            panel
            for panel in walk(dashboard.get("panels") or [])
            if any(
                "loki_prometheus_rule_group_rules" in str(target.get("expr", ""))
                for target in (panel.get("targets") or [])
            )
        ]
        assert len(matches) == 1, (
            f"expected one loki_prometheus_rule_group_rules panel, found {len(matches)}"
        )
        steps = (
            (matches[0].get("fieldConfig") or {}).get("defaults", {})
            .get("thresholds", {})
            .get("steps")
            or []
        )
        green = [s["value"] for s in steps if s.get("color") == "green"]
        assert len(green) == 1, f"expected one green threshold step, found {green}"
        assert green[0] == self._threshold(), (
            f"the dashboard turns green at {green[0]} ruler rules but "
            f"{self.THRESHOLD_ALERT} alerts below {self._threshold()} — the panel "
            "would read healthy while the alert fires."
        )
