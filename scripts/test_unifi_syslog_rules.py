"""Drift guard for the Loki ruler alerts on the UniFi gateway syslog stream.

The alerts in observability/loki/unifi-syslog.yaml must select the stream label
the alloy-syslog config sets, and carry an actionable annotation set.
"""
from __future__ import annotations

import codecs
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
OBS = REPO / "kubernetes" / "infrastructure" / "observability"
LOKI_DIR = OBS / "loki"
UNIFI_YML = LOKI_DIR / "unifi-syslog.yaml"
HOST_LOG_YML = LOKI_DIR / "host-log-staleness.yaml"
ALLOY_SYSLOG_YML = OBS / "alloy-syslog" / "release.yaml"
MONITORING_YML = OBS / "rules" / "monitoring.yaml"

STALE_ALERT = "UnifiSyslogStale"


# Producer side: the Alloy config that labels the stream


def alloy_syslog_receiver() -> tuple[str, str]:
    """The `loki.source.syslog` component name and the job label it sets."""
    release = yaml.safe_load(ALLOY_SYSLOG_YML.read_text())
    content = release["spec"]["values"]["alloy"]["configMap"]["content"]
    names = re.findall(r'loki\.source\.syslog\s+"([^"]+)"', content)
    jobs = re.findall(r'\bjob\s*=\s*"([^"]+)"', content)
    assert len(names) == 1, (
        f"expected exactly one loki.source.syslog component in "
        f"{ALLOY_SYSLOG_YML.name}, found {names}"
    )
    assert len(jobs) == 1, (
        f"expected exactly one job label in {ALLOY_SYSLOG_YML.name}, found {jobs}"
    )
    return names[0], jobs[0]


# Consumer side: the ruler rule files


def ruler_rule_files() -> dict[str, list[dict]]:
    """Every loki/*.yaml holding ruler rules, keyed by file name."""
    files: dict[str, list[dict]] = {}
    for path in sorted(LOKI_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict) or "groups" not in doc:
            continue
        files[path.name] = [
            rule for group in doc["groups"] for rule in group.get("rules", [])
        ]
    return files


def check_job_selectors(rules: list[dict], job: str) -> None:
    for rule in rules:
        name = rule.get("alert", "<unnamed>")
        selected = re.findall(r'\bjob\s*=\s*"([^"]+)"', rule["expr"])
        assert selected, f"{name} has no job= stream selector"
        for value in selected:
            assert value == job, (
                f'{name} selects job="{value}" but alloy-syslog labels the stream '
                f'job="{job}" — the rule would match an empty stream forever'
            )


def check_actionable(rules: list[dict], source: str) -> None:
    for rule in rules:
        name = rule.get("alert", "<unnamed>")
        assert rule.get("labels", {}).get("severity"), (
            f"{source}: {name} missing labels.severity"
        )
        annotations = rule.get("annotations") or {}
        for key in ("summary", "description", "runbook_url"):
            assert annotations.get(key), f"{source}: {name} missing annotations.{key}"


def check_line_filter_regexes(rules: list[dict], source: str) -> None:
    # `|~` takes a Go double-quoted string, so `\b` must be written `\\b`: the
    # single-backslash form unquotes to a backspace and matches nothing.
    for rule in rules:
        name = rule.get("alert", "<unnamed>")
        for raw in re.findall(r'\|~\s*"((?:[^"\\]|\\.)*)"', rule["expr"]):
            pattern = codecs.decode(raw, "unicode_escape")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise AssertionError(
                    f"{source}: {name} line filter {raw!r} is not a valid regex "
                    f"after LogQL unquoting: {exc}"
                ) from exc
            control = [c for c in pattern if ord(c) < 0x20]
            assert not control, (
                f"{source}: {name} line filter {raw!r} unquotes to a control "
                f"character — double the backslash in the YAML"
            )


# Tests


class TestStreamLabelInSync:
    def test_unifi_rules_select_the_job_alloy_syslog_sets(self):
        _, job = alloy_syslog_receiver()
        check_job_selectors(ruler_rule_files()[UNIFI_YML.name], job)

    def test_unifi_syslog_stale_pins_the_alloy_component_id(self):
        name, _ = alloy_syslog_receiver()
        rules = [
            rule
            for group in yaml.safe_load(MONITORING_YML.read_text())["spec"]["groups"]
            for rule in group.get("rules", [])
            if rule.get("alert") == STALE_ALERT
        ]
        assert len(rules) == 1, f"expected one {STALE_ALERT} rule, found {len(rules)}"
        expected = f'component_id="loki.source.syslog.{name}"'
        assert expected in rules[0]["expr"], (
            f"{STALE_ALERT} does not select {expected} — the receiver-side "
            f"staleness witness watches a component that no longer exists"
        )


class TestRuleShape:
    def test_every_ruler_rule_is_actionable(self):
        for source, rules in ruler_rule_files().items():
            if source == HOST_LOG_YML.name:
                continue  # test_host_log_staleness.py owns that file
            check_actionable(rules, source)

    def test_line_filter_regexes_survive_logql_unquoting(self):
        for source, rules in ruler_rule_files().items():
            check_line_filter_regexes(rules, source)


class TestGateFails:
    """Mutations of the shipped rules, proving each check can go red."""

    def test_mis_spelled_job_label_is_caught(self):
        rules = ruler_rule_files()[UNIFI_YML.name]
        mutated = [dict(rules[0], expr=rules[0]["expr"].replace("unifi-syslog", "ucg"))]
        with pytest.raises(AssertionError, match="empty stream"):
            check_job_selectors(mutated, alloy_syslog_receiver()[1])

    def test_missing_runbook_url_is_caught(self):
        rules = ruler_rule_files()[UNIFI_YML.name]
        annotations = {
            k: v for k, v in rules[0]["annotations"].items() if k != "runbook_url"
        }
        with pytest.raises(AssertionError, match="annotations.runbook_url"):
            check_actionable([dict(rules[0], annotations=annotations)], "mutated")

    def test_single_backslash_line_filter_is_caught(self):
        rules = ruler_rule_files()[UNIFI_YML.name]
        mutated = [dict(rules[0], expr=r'sum(count_over_time({job="x"} |~ "\berr" [1h]))')]
        with pytest.raises(AssertionError, match="control character"):
            check_line_filter_regexes(mutated, "mutated")
