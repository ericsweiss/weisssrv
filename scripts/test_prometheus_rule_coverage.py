"""Every alert rule must have a promtool unit test, or a declared exemption.

The corpus is `scripts/extract-prometheus-config.py` plus the Loki ruler rule
files; UNTESTED and UNTESTED_LOGQL are the only hand-maintained sets.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
TESTS_DIR = SCRIPTS / "prometheus-rule-tests"
KUBERNETES = REPO / "kubernetes"
LOKI_RULES = REPO / "kubernetes" / "infrastructure" / "observability" / "loki"

# Alerts shipping without a unit test. An entry claims the expression is a bare
# threshold, or `== 0`/absent() on one series, with no join and no window.
# Windowed expressions and joined threshold ladders do not belong here.
UNTESTED = {
    # up == 0 / absent() on a single exporter target, no join, no window.
    "BlackboxExporterDown",
    "CiCacheDown",
    "GpuExporterDown",
    "HindsightDown",
    "NFSServerDown",
    "OnePasswordConnectDown",
    "PostfixDown",
    "TailscaleOperatorDown",
    "VPNDown",
    "VPNExporterDown",
    # Single ratio/threshold on one series, both arms of the same ladder.
    "DiskUsageWarning",
    "DiskUsageCritical",
    "InodeUsageWarning",
    "InodeUsageCritical",
    "PVCUsageWarning",
    "PVCUsageCritical",
    "ZFSPoolSpaceWarning",
    "ZFSPoolSpaceCritical",
    "PostfixQueueBacklog",
    "NodeMemoryPressure",
    "NodeStuckCordoned",
    "MaintenanceRebootDeferred",
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

# LogQL rule files the Loki ruler loads, which promtool can neither parse nor
# evaluate, so coverage for them needs a logcli/cortextool suite.
UNTESTED_LOGQL = {
    "GitlabRunnerReaperPartialSweep",
    "HostLogShippingStale",
    "UnifiGatewayErrorBurst",
    "UnifiIdsEngineFailure",
    "UnifiIpsBlockFailed",
}

EXEMPT = UNTESTED | UNTESTED_LOGQL


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> dict[str, str]:
    """alertname -> group name, across the whole shipped rule corpus.

    Prometheus rules come through the extraction the lint script uses; the Loki
    ruler files sit outside it and are walked directly.
    """
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
    for path in sorted(LOKI_RULES.glob("*.yaml")):
        for loki_doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(loki_doc, dict):
                continue
            for group in loki_doc.get("groups") or []:
                for rule in group.get("rules") or []:
                    if isinstance(rule, dict) and rule.get("alert"):
                        alerts.setdefault(
                            rule["alert"], group.get("name", "<unnamed>")
                        )
    return alerts


@pytest.fixture(scope="module")
def tested() -> set[str]:
    """Every alertname a *.test.yaml asserts FIRING at least once."""
    names: set[str] = set()
    for path in sorted(TESTS_DIR.glob("*.test.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for case in doc.get("tests") or []:
            for assertion in case.get("alert_rule_test") or []:
                if assertion.get("alertname") and assertion.get("exp_alerts"):
                    names.add(assertion["alertname"])
    return names


def test_every_alert_is_tested_or_declared_untested(corpus, tested):
    uncovered = sorted(set(corpus) - tested - EXEMPT)
    assert not uncovered, (
        "alerts with no promtool unit test and no UNTESTED entry:\n  "
        + "\n  ".join(f"{name} ({corpus[name]})" for name in uncovered)
        + "\n\nAdd a case to scripts/prometheus-rule-tests/<area>.test.yaml, or "
        "add the name to UNTESTED in this file with the reason in review."
    )


def test_untested_entries_still_name_a_live_alert(corpus):
    """A stale exemption silently covers nothing and hides the next omission."""
    stale = sorted(EXEMPT - set(corpus))
    assert not stale, (
        f"UNTESTED names alerts the corpus no longer defines: {stale} — drop them"
    )


def test_untested_does_not_cover_an_alert_that_now_has_a_test(corpus, tested):
    """The exemption list must shrink as tests land, not linger."""
    redundant = sorted(EXEMPT & tested)
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


# Metric families and what declares their collection. An alert selecting a
# family with no entry here is permanently absent: it never fires and nothing
# else reports that the series is not being scraped.
_KPS = "infrastructure/observability/kube-prometheus-stack/release.yaml"
_NODE_HOST = "infrastructure/observability/exporters/node-exporter-host.yaml"
METRIC_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "up": ((_KPS, "kube-prometheus-stack"),),
    "process_start_time_seconds": ((_KPS, "kube-prometheus-stack"),),
    "alertmanager_": ((_KPS, "alertmanager:"),),
    "container_": ((_KPS, "kubelet:"),),
    "kube_": ((_KPS, "kube-state-metrics:"),),
    "gotk_": ((_KPS, "customResourceState"),),
    "node_": ((_KPS, "nodeExporter:"), (_NODE_HOST, "node-exporter-host")),
    "backup_artifact_": ((_NODE_HOST, "node-exporter-host"),),
    "certmanager_": (("infrastructure/controllers/cert-manager/release.yaml", "prometheus:"),),
    "controller_runtime_": (
        ("infrastructure/observability/service-monitors/flux-system.yaml", "PodMonitor"),
    ),
    "externalsecret_": (
        ("infrastructure/controllers/external-secrets/release.yaml", "serviceMonitor:"),
    ),
    "gluetun_": (("apps/download-clients/qbittorrent/resources.yaml", "gluetun-exporter"),),
    "loki_": (
        ("infrastructure/observability/loki/release.yaml", "serviceMonitor"),
        ("infrastructure/observability/alloy/release.yaml", "loki.write"),
        ("infrastructure/observability/alloy-syslog/release.yaml", "loki.source.syslog"),
    ),
    "pg_": (("infrastructure/observability/service-monitors/postgres.yaml", "ServiceMonitor"),),
    "probe_": (("infrastructure/observability/exporters/blackbox-exporter.yaml",
                "serviceMonitor:"),),
    "pve_": (("infrastructure/observability/exporters/proxmox-exporter.yaml",
              "ServiceMonitor"),),
    "traefik_": (("infrastructure/controllers/traefik/release.yaml", "serviceMonitor:"),),
    "unbound_": (("infrastructure/observability/exporters/unbound-exporter.yaml",
                  "ServiceMonitor"),),
}

# PromQL words that can precede a `{`, so the selector pattern reads them as
# metric names. Aggregations take `{...}` label lists and `on`/`ignoring` take
# label sets.
_PROMQL_WORDS = frozenset({
    "and", "atan2", "avg", "bool", "bottomk", "by", "count", "count_values",
    "group", "group_left", "group_right", "ignoring", "limitk", "limit_ratio",
    "max", "min", "offset", "on", "or", "quantile", "stddev", "stdvar", "sum",
    "topk", "unless", "without",
})
_SELECTOR = re.compile(r"\b[a-z_][a-z0-9_]*(?=\s*\{)")


def selected_metrics(expr: str) -> set[str]:
    """Metric names one PromQL expression selects by label matcher."""
    return {name for name in _SELECTOR.findall(expr) if name not in _PROMQL_WORDS}


def declared_source(metric: str) -> tuple[tuple[str, str], ...] | None:
    """The METRIC_SOURCES entry covering a metric: exact name, else its family."""
    if metric in METRIC_SOURCES:
        return METRIC_SOURCES[metric]
    family = max(
        (key for key in METRIC_SOURCES if key.endswith("_") and metric.startswith(key)),
        key=len,
        default=None,
    )
    return METRIC_SOURCES[family] if family else None


@pytest.fixture(scope="module")
def promql_metrics(tmp_path_factory) -> dict[str, set[str]]:
    """metric name -> the alertnames selecting it, over the Prometheus corpus."""
    out = tmp_path_factory.mktemp("metrics") / "rules.yaml"
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "extract-prometheus-config.py"), "rules", str(out)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert run.returncode == 0, f"rule extraction failed:\n{run.stdout}{run.stderr}"
    doc = yaml.safe_load(out.read_text()) or {}
    selected: dict[str, set[str]] = {}
    for group in doc.get("groups") or []:
        for rule in group.get("rules") or []:
            name = rule.get("alert") or rule.get("record")
            for metric in selected_metrics(str(rule.get("expr") or "")):
                selected.setdefault(metric, set()).add(str(name))
    assert len(selected) > 40, (
        f"only {len(selected)} metric selectors harvested — the pattern stopped matching"
    )
    return selected


def test_every_selected_metric_has_a_declared_scrape_source(promql_metrics):
    uncovered = sorted(m for m in promql_metrics if declared_source(m) is None)
    assert not uncovered, (
        "alerts select these metrics, and no METRIC_SOURCES entry says what "
        "scrapes them, so each is permanently absent:\n  "
        + "\n  ".join(f"{m} ({sorted(promql_metrics[m])})" for m in uncovered)
    )


def test_every_declared_source_still_exists(promql_metrics):
    """A deleted exporter or a renamed Helm value must red the table, not the
    alert at 3am."""
    gone = []
    for family, sources in sorted(METRIC_SOURCES.items()):
        for relpath, token in sources:
            path = KUBERNETES / relpath
            if not path.is_file():
                gone.append(f"{family}: {relpath} does not exist")
            elif token not in path.read_text():
                gone.append(f"{family}: {relpath} no longer carries {token!r}")
    assert not gone, f"METRIC_SOURCES names scrape sources that moved: {gone}"


def test_every_declared_family_is_still_selected(promql_metrics):
    """A stale entry hides the next omission behind a family nothing alerts on."""
    stale = sorted(
        family for family in METRIC_SOURCES
        if not any(declared_source(m) is METRIC_SOURCES[family] for m in promql_metrics)
    )
    assert not stale, f"METRIC_SOURCES covers families no alert selects: {stale} — drop them"


def test_an_alert_on_an_unscraped_metric_is_detected():
    """Mutation case: the live corpus is covered, so the arm is proven inline."""
    assert selected_metrics('widget_total{job="x"} == 0') == {"widget_total"}
    assert declared_source("widget_total") is None
    assert declared_source("node_filesystem_avail_bytes") is not None


def test_promql_words_are_not_read_as_metric_names():
    expr = 'sum by (node) (max{job="a"} or node_network_up{device="nic0"})'
    assert selected_metrics(expr) == {"node_network_up"}
