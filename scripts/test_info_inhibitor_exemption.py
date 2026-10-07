"""Drift guard for the InfoInhibitor exemption in the Alertmanager config.

`equal: [namespace]` mutes info alerts that carry no namespace, so those
alertnames sit in an `alertname!~` alternation no other gate validates.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
LOKI_DIR = REPO / "kubernetes" / "infrastructure" / "observability" / "loki"
ALERTMANAGER_YML = (
    REPO
    / "kubernetes"
    / "infrastructure"
    / "observability"
    / "kube-prometheus-stack"
    / "alertmanager-config.yaml"
)

# Info alerts that are deliverable under `equal: [namespace]` because their
# namespace always has a firing warning or critical beside them. Every other
# info alert must be in the InfoInhibitor alternation.
NAMESPACE_CARRYING: set[str] = set()

_MATCHER = re.compile(r'alertname!~"([^"]+)"')


def _alert_groups(doc: dict) -> list:
    """The rule groups of a PrometheusRule or of a bare Loki ruler document."""
    return (doc.get("spec") or {}).get("groups") or doc.get("groups") or []


def _extracted_groups() -> list:
    """Rule groups from the canonical extractor: the standalone PrometheusRules
    AND additionalPrometheusRulesMap in the kube-prometheus-stack HelmRelease."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rules.yaml"
        run = subprocess.run(
            [sys.executable, str(SCRIPTS / "extract-prometheus-config.py"), "rules", str(out)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert run.returncode == 0, f"rule extraction failed:\n{run.stdout}{run.stderr}"
        return (yaml.safe_load(out.read_text()) or {}).get("groups") or []


def _info_alertnames() -> set[str]:
    """Every `severity: info` alertname the extractor sees, plus loki/.

    The extractor does not read loki/, whose ruler alerts reach the same
    Alertmanager, so the alternation has to cover them too.
    """
    docs = [{"groups": _extracted_groups()}]
    docs += [
        yaml.safe_load(path.read_text()) or {}
        for path in sorted(LOKI_DIR.glob("*.yaml"))
        if path.name != "kustomization.yaml"
    ]
    names: set[str] = set()
    for doc in docs:
        for group in _alert_groups(doc):
            for rule in group.get("rules") or []:
                if rule.get("alert") and (rule.get("labels") or {}).get("severity") == "info":
                    names.add(rule["alert"])
    return names


def _inhibit_blocks(text: str) -> list[str]:
    """Each inhibit rule as raw text, split on its `- source_matchers:` key.

    The alertmanager.yaml body is a Go-templated block scalar, so it is read as
    text rather than parsed.
    """
    starts = [m.start() for m in re.finditer(r"- source_matchers:", text)]
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def exempted_alertnames(text: str) -> set[str]:
    """Alternation members of the InfoInhibitor rule's `alertname!~` matcher."""
    blocks = [b for b in _inhibit_blocks(text) if 'alertname="InfoInhibitor"' in b]
    assert len(blocks) == 1, f"expected one InfoInhibitor inhibit rule, found {len(blocks)}"
    found = _MATCHER.search(blocks[0])
    assert found, "the InfoInhibitor rule has no alertname!~ exemption matcher"
    return set(found.group(1).split("|"))


@pytest.fixture(scope="module")
def info_alerts() -> set[str]:
    names = _info_alertnames()
    assert names, "no severity: info alerts found — the gate would be vacuous"
    return names


@pytest.fixture(scope="module")
def exempted() -> set[str]:
    return exempted_alertnames(ALERTMANAGER_YML.read_text())


def test_every_exempted_name_is_a_live_info_alert(info_alerts, exempted):
    """A renamed alert leaves an inert matcher that re-mutes it cluster-wide."""
    stale = sorted(exempted - info_alerts)
    assert not stale, (
        f"InfoInhibitor exempts alertnames no severity: info rule defines: {stale}"
        " — the matcher is inert and those alerts are muted again"
    )


def test_every_info_alert_is_exempted_or_carries_a_namespace(info_alerts, exempted):
    """A new namespace-less info alert is muted the moment any namespace fires."""
    unclassified = sorted(info_alerts - exempted - NAMESPACE_CARRYING)
    assert not unclassified, (
        "severity: info alerts neither exempted from InfoInhibitor nor declared "
        f"namespace-carrying: {unclassified} — add the name to the alertname!~ "
        "alternation in alertmanager-config.yaml, or to NAMESPACE_CARRYING in this "
        "file if a warning or critical always fires in the same namespace"
    )


def test_namespace_carrying_list_names_live_alerts(info_alerts):
    """A stale entry silently excuses the next namespace-less info alert."""
    stale = sorted(NAMESPACE_CARRYING - info_alerts)
    assert not stale, f"NAMESPACE_CARRYING names alerts that are no longer info: {stale}"


def test_dropping_a_member_from_the_alternation_fails(info_alerts, exempted):
    """Mutation case: a shortened alternation must red the coverage assertion."""
    dropped = sorted(exempted)[0]
    mutated = exempted - {dropped}
    assert sorted(info_alerts - mutated - NAMESPACE_CARRYING) == [dropped]


def test_a_loki_shaped_info_alert_reaches_the_coverage_check(info_alerts, exempted):
    """Mutation case: a bare `groups:` document must red the coverage assertion."""
    doc = yaml.safe_load(
        "groups:\n"
        "  - name: unifi\n"
        "    rules:\n"
        "      - alert: UnifiSyslogQuiet\n"
        "        labels:\n"
        "          severity: info\n"
    )
    collected = {
        rule["alert"]
        for group in _alert_groups(doc)
        for rule in group["rules"]
        if (rule.get("labels") or {}).get("severity") == "info"
    }
    assert collected == {"UnifiSyslogQuiet"}
    grown = info_alerts | collected
    assert sorted(grown - exempted - NAMESPACE_CARRYING) == ["UnifiSyslogQuiet"]
