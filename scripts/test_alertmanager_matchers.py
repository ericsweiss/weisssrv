"""Parity guards for the Alertmanager inhibit-rule matchers.

check-alertmanager-behaviour.py resolves alertnames behind `=` and `=~` only,
and nothing compares a matcher's regex against the rule it shadows.
"""
from __future__ import annotations

import functools
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
OBS = REPO / "kubernetes" / "infrastructure" / "observability"
ALERTMANAGER_YML = OBS / "kube-prometheus-stack" / "alertmanager-config.yaml"
RULES_DIR = OBS / "rules"
MONITORING_YML = RULES_DIR / "monitoring.yaml"
BLACKBOX_YML = OBS / "exporters" / "blackbox-exporter.yaml"
BEHAVIOUR_YML = REPO / "scripts" / "alertmanager-behaviour.yaml"
DNS_VARS = REPO / "ansible" / "inventories" / "prod" / "group_vars" / "dns.yml"
CLUSTER_CONFIG = REPO / "kubernetes" / "infrastructure" / "sources" / "cluster-config.yaml"

AGGREGATE_ALERT = "ExternalIngressDown"


# Sources


def alertmanager_config() -> str:
    """The rendered alertmanager.yaml body out of the ExternalSecret template."""
    doc = yaml.safe_load(ALERTMANAGER_YML.read_text())
    return doc["spec"]["target"]["template"]["data"]["alertmanager.yaml"]


def external_domain() -> str:
    return yaml.safe_load(CLUSTER_CONFIG.read_text())["data"]["cluster_external_domain"]


@functools.cache
def shipped_alertnames() -> frozenset[str]:
    """Every alertname an inhibit matcher may name: the extractor's corpus (the
    standalone PrometheusRules AND the stack HelmRelease's own rules) plus the
    chart inventory in scripts/alertmanager-behaviour.yaml."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rules.yaml"
        run = subprocess.run(
            [sys.executable, str(SCRIPTS / "extract-prometheus-config.py"), "rules", str(out)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert run.returncode == 0, f"rule extraction failed:\n{run.stdout}{run.stderr}"
        doc = yaml.safe_load(out.read_text()) or {}
    names = {
        rule["alert"]
        for group in doc.get("groups") or []
        for rule in group.get("rules") or []
        if rule.get("alert")
    }
    assert names, "the extracted corpus holds no alerts — the gate would be vacuous"
    names.update(yaml.safe_load(BEHAVIOUR_YML.read_text())["upstream_alerts"])
    return frozenset(names)


# Negated alertname matchers


NEGATED_RE = re.compile(r'alertname!([~=])"([^"]*)"')


def negated_alertname_members(config: str) -> list[tuple[str, str]]:
    """(matcher, alertname) for every member of an `alertname!~`/`!=` matcher."""
    members: list[tuple[str, str]] = []
    for operator, value in NEGATED_RE.findall(config):
        matcher = f'alertname!{operator}"{value}"'
        parts = value.split("|") if operator == "~" else [value]
        members.extend((matcher, part) for part in parts)
    return members


def check_negated_alertnames_resolve(config: str, known: set[str]) -> None:
    members = negated_alertname_members(config)
    assert members, "no negated alertname matcher found — did the config change shape?"
    for matcher, name in members:
        assert name in known, (
            f"{matcher} exempts {name!r}, which is neither an alert in "
            f"observability/rules/ nor an entry in alertmanager-behaviour.yaml "
            f"upstream_alerts — an unmatched member is an inert matcher, so the "
            f"alert it was meant to exempt is silently inhibited"
        )


# ExternalIngressDown exclusion set


SELECTOR_RE = re.compile(r'instance(=|!)~"([^"]*)"')
ALTERNATION_RE = re.compile(r"\(([^)]*)\)")


def selector_pair(text: str, source: str) -> tuple[str, str]:
    """The `instance=~` / `instance!~` regex pair, as written."""
    found = SELECTOR_RE.findall(text)
    include = [value for operator, value in found if operator == "="]
    exclude = [value for operator, value in found if operator == "!"]
    assert len(include) == 1 and len(exclude) == 1, (
        f"{source}: expected one instance=~ and one instance!~ selector, "
        f"found {found}"
    )
    return include[0], exclude[0]


def excluded_hosts(exclude_regex: str, domain: str) -> set[str]:
    match = ALTERNATION_RE.search(exclude_regex)
    assert match, f"no alternation in {exclude_regex!r}"
    suffix = domain.replace(".", r"\\.")
    assert exclude_regex.endswith(f"{suffix}"), (
        f"{exclude_regex!r} does not end in the escaped external domain"
    )
    return {member.replace(r"\\.", ".") for member in match.group(1).split("|")}


def aggregate_expr() -> str:
    for group in yaml.safe_load(MONITORING_YML.read_text())["spec"]["groups"]:
        for rule in group.get("rules", []):
            if rule.get("alert") == AGGREGATE_ALERT:
                return rule["expr"]
    raise AssertionError(f"{AGGREGATE_ALERT} is gone from {MONITORING_YML.name}")


def inhibit_selector_block() -> str:
    pattern = re.compile(
        r'- source_matchers:\n\s+- alertname="' + AGGREGATE_ALERT + r'"\n'
        r"\s+target_matchers:\n((?:[ \t]+- .*\n)+)"
    )
    match = pattern.search(alertmanager_config())
    assert match, (
        f"no inhibit rule sourced on {AGGREGATE_ALERT} in {ALERTMANAGER_YML.name} — "
        f"EndpointDown would page alongside the aggregate"
    )
    return match.group(1)


def rewritten_external_hosts(domain: str) -> set[str]:
    """Hosts under the external domain that AdGuard answers with an internal
    address, so their blackbox probe never leaves the LAN."""
    rewrites = yaml.safe_load(DNS_VARS.read_text())["adguard_home_rewrites"]
    hosts = set()
    for entry in rewrites:
        name = entry["domain"]
        if name.startswith("*.") or not name.endswith(f".{domain}"):
            continue
        hosts.add(name[: -len(f".{domain}")])
    return hosts


def probed_external_hosts(domain: str) -> set[str]:
    escaped = re.escape("${cluster_external_domain}")
    pattern = re.compile(r"url: https://([A-Za-z0-9.-]+)\." + escaped)
    return set(pattern.findall(BLACKBOX_YML.read_text()))


def expected_exclusions(domain: str) -> set[str]:
    return rewritten_external_hosts(domain) & probed_external_hosts(domain)


# Tests


class TestNegatedAlertnamesResolve:
    def test_every_exempted_alertname_is_a_shipped_alert(self):
        check_negated_alertnames_resolve(alertmanager_config(), shipped_alertnames())


class TestExternalIngressDownExclusions:
    def test_aggregate_excludes_exactly_the_split_horizon_hosts(self):
        domain = external_domain()
        _, exclude = selector_pair(aggregate_expr(), MONITORING_YML.name)
        assert excluded_hosts(exclude, domain) == expected_exclusions(domain), (
            f"{AGGREGATE_ALERT} excludes a different set than the AdGuard rewrites "
            f"under {domain} that blackbox also probes — a rewritten host left in "
            f"the selector never counts toward the >= 3 storm threshold"
        )

    def test_inhibit_rule_reproduces_the_aggregate_selectors_verbatim(self):
        expected = selector_pair(aggregate_expr(), MONITORING_YML.name)
        actual = selector_pair(inhibit_selector_block(), ALERTMANAGER_YML.name)
        assert actual == expected, (
            f"the {AGGREGATE_ALERT} inhibit rule no longer reproduces the "
            f"aggregate's own selector, so the suppression outruns it"
        )


class TestGateFails:
    """Mutations of the shipped files, proving each check can go red."""

    def test_renamed_exempted_alertname_is_caught(self):
        mutated = alertmanager_config().replace(
            "SwapCleanStoppedGuests", "SwapCleanStoppedGuest"
        )
        with pytest.raises(AssertionError, match="inert matcher"):
            check_negated_alertnames_resolve(mutated, shipped_alertnames())

    def test_dropped_alternation_member_is_caught(self):
        domain = external_domain()
        _, exclude = selector_pair(aggregate_expr(), MONITORING_YML.name)
        mutated = exclude.replace(r"auth|", "")
        assert excluded_hosts(mutated, domain) != expected_exclusions(domain)

    def test_drifted_inhibit_copy_is_caught(self):
        expected = selector_pair(aggregate_expr(), MONITORING_YML.name)
        mutated = inhibit_selector_block().replace("photos", "images")
        assert selector_pair(mutated, "mutated") != expected
