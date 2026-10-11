"""Every alert's runbook section must name the alert it is linked from, before
the next heading of the same or higher level. EXEMPT lists the alerts whose
runbook does not name them yet, OFF_BASE those answered outside this repo.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
LOKI_RULES = REPO / "kubernetes" / "infrastructure" / "observability" / "loki"


def _load(name: str):
    return load_script(name)


# The heading slugger and the substitution base are the runbook gate's, so the
# two cannot disagree about which section a runbook_url resolves to.
anchors = _load("check-runbook-anchors.py")

# (alert, doc) pairs whose runbook target never names the alert. Keyed by the
# doc alone, so re-anchoring a URL neither silences the gate nor churns this
# list. Write the section, then delete the entry.
EXEMPT: set[tuple[str, str]] = {
    ("AuthentikFlowLatencyHigh", "40-authentik-terraform.md"),
    ("AuthentikWorkerDown", "40-authentik-terraform.md"),
    ("BackupRestoreDrillFailing", "42-offsite-backup.md"),
    ("BackupRestoreDrillProvedTooLittle", "42-offsite-backup.md"),
    ("EtcdQuorumAtRisk", "19-k3s-deployment.md"),
    ("GitLabAgentDown", "29-flux-operations.md"),
    ("GpuExporterDown", "43-gpu-passthrough.md"),
    ("GpuTempCritical", "43-gpu-passthrough.md"),
    ("GpuTempWarning", "43-gpu-passthrough.md"),
    ("HindsightLlamaMemoryNearLimit", "43-gpu-passthrough.md"),
    ("HostBondingDegraded", "34-bond-mac-flapping.md"),
    ("HostConntrackEntriesHigh", "11-firewall.md"),
    ("HostMemAvailableLow", "06-zfs.md"),
    ("HostNetworkInterfaceFlapping", "34-bond-mac-flapping.md"),
    ("HostNetworkReceiveErrs", "34-bond-mac-flapping.md"),
    ("HostNetworkTransmitErrs", "34-bond-mac-flapping.md"),
    ("NASSwapNotClearing", "06-zfs.md"),
    ("NodeExporterHealthcheckRestartLoop", "12-runbooks.md"),
    ("PodLogShippingStale", "31-observability.md"),
    ("ResticOffsitePruneNeverRan", "42-offsite-backup.md"),
    ("SwapCleanGuestRestartFailed", "06-zfs.md"),
    ("SwapCleanStoppedGuests", "06-zfs.md"),
    ("TailscaleOperatorDown", "19-k3s-deployment.md"),
    ("TailscaleProxyDown", "19-k3s-deployment.md"),
    ("WgEasyEndpointVipMissing", "38-wireguard-vpn.md"),
    ("WgEasyMetricsMissing", "38-wireguard-vpn.md"),
    ("ZfsEncryptedMountStuck", "12-runbooks.md"),
    ("ZfsEncryptedMountStuckCritical", "12-runbooks.md"),
}

# Alerts whose runbook_url deliberately points outside this repo, with what
# answers them. Anything else off the placeholder base is unchecked prose: the
# carries-a-URL gate passes it and the names-the-alert gate never sees it.
OFF_BASE: dict[str, str] = {
    "KubeCPUOvercommit":
        "the upstream kube-prometheus-stack runbook, which this rule only retunes",
    "AlertmanagerClusterFailedToSendAlerts":
        "the upstream kube-prometheus-stack runbook for the chart's own alert",
}


def _alerts_in(doc: object) -> list[tuple[str, str]]:
    """(alert name, runbook_url) for every alert in one `groups:` document."""
    if not isinstance(doc, dict):
        return []
    return [
        (rule["alert"], (rule.get("annotations") or {}).get("runbook_url", ""))
        for group in doc.get("groups") or []
        for rule in group.get("rules") or []
        if isinstance(rule, dict) and rule.get("alert")
    ]


def alerts() -> list[tuple[str, str]]:
    """(alert name, runbook_url) across the whole shipped rule corpus.

    Prometheus rules come through the extractor's entry point, the one
    `task lint:prometheus-config` uses; the Loki ruler files sit outside it.
    """
    extract = _load("extract-prometheus-config.py")
    with tempfile.TemporaryDirectory() as scratch:
        rules_file = Path(scratch) / "rules.yaml"
        assert extract.extract_rules(
            rules_file, REPO / extract.DEFAULT_RELEASE, REPO / extract.DEFAULT_RULES_DIR
        ) == 0, "the rule extractor found no rule groups"
        found = _alerts_in(yaml.safe_load(rules_file.read_text()) or {})
    assert found, "parsed no alerts out of the rule corpus"
    for path in sorted(LOKI_RULES.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            found.extend(_alerts_in(doc))
    return found


def sections(text: str) -> dict[str, str]:
    """{anchor: the heading and everything under it}.

    A section runs to the next heading of the same or higher level, so the body
    of `#### Alert` under `## Backup` belongs to both.
    """
    lines = text.splitlines()
    seen: dict[str, int] = {}
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    for number, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = anchors._HEADING_RE.match(line)
        if not match:
            continue
        base = anchors.slug(match.group("text"))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        headings.append(
            (number, len(match.group("hashes")), base if count == 0 else f"{base}-{count}")
        )

    found = {}
    for index, (start, level, anchor) in enumerate(headings):
        end = len(lines)
        for later_start, later_level, _ in headings[index + 1:]:
            if later_level <= level:
                end = later_start
                break
        found[anchor] = "\n".join(lines[start:end])
    return found


def _names(alert: str, text: str) -> bool:
    """Whole-name match: `HostSlabLeakSuspected` must not cover `HostSlabLeak`."""
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(alert) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None


def uncovered(
    corpus: list[tuple[str, str]], docs: Path
) -> tuple[list[tuple[str, str]], list[str]]:
    """(alert, doc target) for every runbook that does not name its alert, plus
    the alerts whose runbook_url is not a `${cluster_runbook_base_url}/` target."""
    cache: dict[Path, dict[str, str]] = {}
    found = []
    off_base: list[str] = []
    for alert, url in corpus:
        if not url.startswith(anchors.BASE_PLACEHOLDER):
            if url:
                off_base.append(f"{alert}: {url}")
            continue
        target, _, anchor = url[len(anchors.BASE_PLACEHOLDER):].partition("#")
        doc = docs / target
        if not doc.is_file():
            found.append((alert, target))
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        if not anchor:
            if not _names(alert, text):
                found.append((alert, target))
            continue
        if doc not in cache:
            cache[doc] = sections(text)
        body = cache[doc].get(anchor)
        if body is None or not _names(alert, body):
            found.append((alert, f"{target}#{anchor}"))
    return found, off_base


@pytest.fixture(scope="module")
def report() -> tuple[list[tuple[str, str]], list[str]]:
    """(uncovered runbooks, runbook_urls that are not a repo doc target)."""
    return uncovered(alerts(), DOCS)


@pytest.fixture(scope="module")
def missing(report) -> list[tuple[str, str]]:
    return report[0]


def _doc(target: str) -> str:
    return target.split("#", 1)[0]


def test_every_runbook_names_the_alert_that_points_at_it(missing):
    new = sorted(
        (alert, target)
        for alert, target in missing
        if (alert, _doc(target)) not in EXEMPT
    )
    assert not new, (
        f"runbook targets that never name their alert: {new} — write the section, "
        "or point runbook_url at the doc that already covers it. An operator "
        "following the link has to find the alert on the page."
    )


def test_exemptions_are_still_uncovered(missing):
    """An exemption outliving its gap hides the next alert to lose its runbook."""
    live = {(alert, _doc(target)) for alert, target in missing}
    stale = sorted(EXEMPT - live)
    assert not stale, (
        f"these runbooks now name their alert: {stale} — drop them from EXEMPT."
    )


def test_every_runbook_url_uses_the_substituted_base(report):
    """A hand-written absolute URL satisfies the carries-a-URL gate and is then
    invisible to the names-the-alert gate, with nothing recording the decision."""
    unlisted = sorted(
        entry for entry in report[1] if entry.split(":", 1)[0] not in OFF_BASE
    )
    assert not unlisted, (
        "alerts whose runbook_url is not a ${cluster_runbook_base_url} target, so "
        "the page that answers them is unchecked — use the placeholder, or add an "
        "OFF_BASE entry naming what answers the alert:\n  " + "\n  ".join(unlisted)
    )


def test_every_off_base_entry_is_still_off_base(report):
    """An entry outliving its URL hides the next alert to leave the repo docs."""
    live = {entry.split(":", 1)[0] for entry in report[1]}
    stale = sorted(set(OFF_BASE) - live)
    assert not stale, (
        f"these alerts now point at a repo doc: {stale} — drop them from OFF_BASE."
    )
    for alert, reason in OFF_BASE.items():
        assert len(reason.split()) >= 6, f"{alert} needs a real reason"


def test_every_alert_carries_a_runbook_url():
    absent = sorted(alert for alert, url in alerts() if not url)
    assert not absent, f"alerts with no runbook_url annotation: {absent}"


def test_the_collector_reads_the_anchored_section(tmp_path):
    """Mutation case: an anchor pointing at a neighbouring section must fail."""
    (tmp_path / "doc.md").write_text(
        "# Runbooks\n\n"
        "## Backup and Recovery\n\n"
        "#### ArchiveBackupFailed / ArchiveBackupStale\n\n"
        "Re-run the archive job.\n\n"
        "## Networking\n\n"
        "Nothing about backups here.\n"
    )
    base = anchors.BASE_PLACEHOLDER
    good = [("ArchiveBackupStale", f"{base}doc.md#backup-and-recovery")]
    wrong = [("ArchiveBackupStale", f"{base}doc.md#networking")]
    absent = [("ArchiveBackupStale", f"{base}doc.md#nowhere")]
    assert uncovered(good, tmp_path) == ([], [])
    assert uncovered(wrong, tmp_path) == ([("ArchiveBackupStale", "doc.md#networking")], [])
    assert uncovered(absent, tmp_path) == ([("ArchiveBackupStale", "doc.md#nowhere")], [])


def test_a_runbook_url_off_the_placeholder_base_is_collected(tmp_path):
    """Mutation case: an absolute URL must not leave the corpus reporting nothing."""
    (tmp_path / "doc.md").write_text("# Runbooks\n")
    off = [("ArchiveBackupStale", "https://runbooks.example/archive")]
    assert uncovered(off, tmp_path) == (
        [], ["ArchiveBackupStale: https://runbooks.example/archive"]
    )
    assert uncovered([("ArchiveBackupStale", "")], tmp_path) == ([], [])


def test_a_longer_alert_name_does_not_cover_the_shorter_one(tmp_path):
    """Mutation case: a half-finished rename leaves only the longer name."""
    (tmp_path / "doc.md").write_text(
        "# Runbooks\n\n"
        "## Host Health\n\n"
        "#### HostSlabLeakSuspected\n\n"
        "Reboot the host on the weekly window.\n"
    )
    base = anchors.BASE_PLACEHOLDER
    short = [("HostSlabLeak", f"{base}doc.md#host-health")]
    exact = [("HostSlabLeakSuspected", f"{base}doc.md#host-health")]
    assert uncovered(short, tmp_path) == ([("HostSlabLeak", "doc.md#host-health")], [])
    assert uncovered(exact, tmp_path) == ([], [])
