"""Drift guard for the HostLogShippingStale alert host set and its grouping.

The rules must cover exactly the hosts the `alloy_host` play targets, derived by
the generator that writes them, and reach Discord as one group, not one a host.
"""
from __future__ import annotations

import re
import tempfile
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



class TestDiscordGrouping:
    """The fleet must reach Discord as one group, not one POST per host.

    amtool resolves a receiver from labels alone and prints no grouping, so the
    branch that collapses the fan-out is only assertable from the config itself.
    """

    ALERTNAME = "HostLogShippingStale"

    def _config(self) -> dict:
        """The Alertmanager config, rendered the way the promtool gate renders it."""
        extract = load_script("extract-prometheus-config.py")
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "alertmanager.yaml"
            assert extract.extract_alertmanager(out, REPO / extract.DEFAULT_AM_CONFIG) == 0, (
                "the Alertmanager config did not render"
            )
            return yaml.safe_load(out.read_text())

    def _routes(self, config: dict) -> list[dict]:
        """The root route's child branches, in the order Alertmanager reads them."""
        routes = (config.get("route") or {}).get("routes") or []
        assert routes, "the Alertmanager config declares no child routes"
        return routes

    def _index(self, routes: list[dict]) -> int:
        for position, route in enumerate(routes):
            if f'alertname="{self.ALERTNAME}"' in (route.get("matchers") or []):
                return position
        raise AssertionError(
            f'no route branch matches alertname="{self.ALERTNAME}" — the fleet '
            f"falls through to the severity=warning branch and groups per "
            f"instance again, 23 Discord POSTs inside one group_wait"
        )

    def test_the_fan_out_the_grouping_exists_for_is_real(self):
        rules = _alert_rules()
        assert len(rules) > 1, (
            "only one HostLogShippingStale rule ships, so the grouping branch "
            "guards nothing — drop it, or the generator regressed"
        )
        severities = {r["labels"]["severity"] for r in rules}
        assert severities == {"warning"}, (
            f"the rules carry {sorted(severities)}; the grouping branch is placed "
            f"to intercept them ahead of the severity=warning branch"
        )

    def test_the_branch_groups_on_alertname_alone(self):
        routes = self._routes(self._config())
        branch = routes[self._index(routes)]
        assert branch.get("group_by") == ["alertname"], (
            f"the {self.ALERTNAME} branch groups on {branch.get('group_by')!r}; "
            f"any key that varies per host makes one Discord POST per host "
            f"again, and the webhook 429s on the burst"
        )
        assert branch.get("group_wait"), (
            f"the {self.ALERTNAME} branch inherits the root group_wait — set it "
            f"explicitly so the whole fleet lands in one flush"
        )

    def test_the_branch_precedes_every_severity_branch(self):
        routes = self._routes(self._config())
        position = self._index(routes)
        earlier = [
            index
            for index, route in enumerate(routes[:position])
            if any(m.startswith("severity=") for m in (route.get("matchers") or []))
            and 'severity="none"' not in (route.get("matchers") or [])
        ]
        assert not earlier, (
            f"severity branches at {earlier} precede the {self.ALERTNAME} branch "
            f"at {position}; the first matching child wins, so the grouping is dead"
        )

    def test_the_branch_still_reaches_a_discord_receiver(self):
        config = self._config()
        routes = self._routes(config)
        name = routes[self._index(routes)].get("receiver")
        receivers = {r["name"]: r for r in config.get("receivers") or []}
        assert name in receivers, f"the {self.ALERTNAME} branch names receiver {name!r}"
        assert receivers[name].get("discord_configs"), (
            f"receiver {name!r} has no discord_configs, so the grouped fleet "
            f"alert reaches nobody"
        )
