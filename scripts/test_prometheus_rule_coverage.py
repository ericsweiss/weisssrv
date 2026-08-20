"""Every alert rule must have a promtool unit test, or a declared exemption.

`task lint:prometheus-config` runs promtool over whatever `*.test.yaml` happens
to be present and explicitly SKIPS when there are none, so an alert that ships
with no test is indistinguishable from one that ships with a passing test. That
matters most for the families whose failure mode is silence: an `absent()` arm
whose series was renamed, a `time() - <gauge>` staleness arm pointed at a metric
the collector stopped emitting, or a windowed `increase()` rewritten as a bare
comparison. All of those look exactly like a healthy fleet.

The corpus is the same one the lint script validates — extracted with
`scripts/extract-prometheus-config.py`, which unions the HelmRelease with the
standalone PrometheusRule manifests under `observability/rules/`. Nothing is
listed here by hand; UNTESTED is the only hand-maintained set, and adding to it
is a deliberate, reviewable act.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
TESTS_DIR = SCRIPTS / "prometheus-rule-tests"

# Alerts shipping without a unit test, each accepted deliberately. An entry is a
# claim that the alert's logic is simple enough to read (a bare `up == 0`, a
# single threshold on a series with no join) OR that a test would only restate
# the expression. The families this gate exists for — absent()/staleness arms,
# windowed increase(), joined threshold ladders — do NOT belong here.
#
# Shrinking this set is the point; nothing may be added without a reason in
# review.
UNTESTED = {
    # up == 0 / absent() on a single exporter target, no join, no window.
    "BlackboxExporterDown",
    "GpuExporterDown",
    "HindsightDown",
    "ImmichDown",
    "NextcloudDown",
    "NFSServerDown",
    "OnePasswordConnectDown",
    "PostfixDown",
    "ProxmoxHostDown",
    "TailscaleOperatorDown",
    "VPNDown",
    "VPNExporterDown",
    "DNSResolutionDown",
    "EndpointDownCritical",
    "HAInfraGuestDown",
    # Single ratio/threshold on one series, both arms of the same ladder.
    "DiskUsageWarning",
    "DiskUsageCritical",
    "InodeUsageWarning",
    "InodeUsageCritical",
    "PVCUsageWarning",
    "PVCUsageCritical",
    "ZFSPoolSpaceWarning",
    "ZFSPoolSpaceCritical",
    "PostgresConnectionsHigh",
    "PostfixQueueBacklog",
    "KubeCPUOvercommit",
    "NodeMemoryPressure",
    "NodeStuckCordoned",
    "CorosyncWedged",
    "MaintenanceRebootDeferred",
    "LokiRequestFailures",
    "RegistryCacheDown",
    "CiCacheDown",
    "ExternalSecretSyncFailure",
    # Certificate expiry: a plain `<time> - now` threshold per certificate.
    "CertExpiringWarning",
    "CertExpiringCritical",
    "CertExpiringSoon",
    "CertExpiringSoonCritical",
    # Per-app backup freshness/failure arms. The SHAPE is covered by the
    # backup*.test.yaml suites; these are per-app repetitions of it against a
    # different textfile metric.
    "ArchiveBackupFailedProlonged",
    "GitLabBackupStale",
    "GitLabBackupStaleCritical",
    "ImmichBackupFailed",
    "ImmichBackupStale",
    "MediaMoverFailed",
    "MediaMoverStale",
    "NextcloudBackupFailed",
    "NextcloudBackupStale",
    "PveClusterBackupStale",
    "ResticOffsiteVerifyStaleCritical",
    "VzdumpBackupFailed",
}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> dict[str, str]:
    """alertname -> group name, from the same extraction the lint script uses."""
    out = tmp_path_factory.mktemp("rules") / "rules.yaml"
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "extract-prometheus-config.py"), "rules", str(out)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert run.returncode == 0, f"rule extraction failed:\n{run.stdout}{run.stderr}"
    doc = yaml.safe_load(out.read_text()) or {}
    alerts: dict[str, str] = {}
    for group in doc.get("groups") or []:
        for rule in group.get("rules") or []:
            if rule.get("alert"):
                alerts.setdefault(rule["alert"], group.get("name", "<unnamed>"))
    assert alerts, "the extracted corpus holds no alerts — the gate would be vacuous"
    return alerts


@pytest.fixture(scope="module")
def tested() -> set[str]:
    """Every alertname a *.test.yaml asserts FIRING at least once.

    `exp_alerts: []` is a real and valuable assertion — it pins the silent half
    of a threshold — but on its own it is indistinguishable from an alert that
    can never fire at all: a typo'd matcher on an `absent()` arm or a flipped
    comparison satisfies every negative case in the suite. Counting only
    positive coverage is what makes "tested" mean the rule was proved to fire.
    """
    names: set[str] = set()
    for path in sorted(TESTS_DIR.glob("*.test.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for case in doc.get("tests") or []:
            for assertion in case.get("alert_rule_test") or []:
                if assertion.get("alertname") and assertion.get("exp_alerts"):
                    names.add(assertion["alertname"])
    return names


def test_every_alert_is_tested_or_declared_untested(corpus, tested):
    uncovered = sorted(set(corpus) - tested - UNTESTED)
    assert not uncovered, (
        "alerts with no promtool unit test and no UNTESTED entry:\n  "
        + "\n  ".join(f"{name} ({corpus[name]})" for name in uncovered)
        + "\n\nAdd a case to scripts/prometheus-rule-tests/<area>.test.yaml, or "
        "add the name to UNTESTED in this file with the reason in review."
    )


def test_untested_entries_still_name_a_live_alert(corpus):
    """A stale exemption silently covers nothing and hides the next omission."""
    stale = sorted(UNTESTED - set(corpus))
    assert not stale, (
        f"UNTESTED names alerts the corpus no longer defines: {stale} — drop them"
    )


def test_untested_does_not_cover_an_alert_that_now_has_a_test(corpus, tested):
    """The exemption list must shrink as tests land, not linger."""
    redundant = sorted(UNTESTED & tested)
    assert not redundant, (
        f"these are exempt AND tested: {redundant} — remove them from UNTESTED"
    )


def test_every_test_file_asserts_on_a_live_alert(corpus, tested):
    """A test naming an alert the corpus dropped passes vacuously forever."""
    orphans = sorted(tested - set(corpus))
    # upstream-etcd.rules.yaml ships its own rules alongside the tests, so its
    # alertnames are legitimately absent from the extracted corpus.
    upstream = {
        rule.get("alert")
        for path in TESTS_DIR.glob("*.rules.yaml")
        for group in (yaml.safe_load(path.read_text()) or {}).get("groups") or []
        for rule in group.get("rules") or []
        if rule.get("alert")
    }
    orphans = [name for name in orphans if name not in upstream]
    assert not orphans, (
        f"unit tests assert on alertnames no rule defines: {orphans}"
    )
