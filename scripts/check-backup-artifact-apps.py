#!/usr/bin/env python3
"""Assert the backup-artifact app list and its alert arms stay paired.

The COLLECTOR's app list lives in Ansible role defaults
(`nas_backup_artifact_apps` in ansible/roles/nas_storage/defaults/main.yml); the
matching `absent(backup_artifact_last_mtime_seconds{app="..."})` arms are
hand-enumerated in the BackupArtifactStale rule in
kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml. The
two live in different lifecycles (Ansible deploy vs Flux reconcile) and nothing
tied them together:

  - adding an app with no absent() arm means a landing dir that is never created
    emits NO series at all, so the freshness arm has nothing to fire on — the
    exact "no dump ever arrived" hole the enumerated arms exist to close;
  - removing an app but leaving its arm behind leaves BackupArtifactStale
    firing forever on a series that will never come back.

Both directions are silent today. This check makes them a lint failure.

Exit 0 when the two sets match, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULTS = REPO / "ansible/roles/nas_storage/defaults/main.yml"
RULES = REPO / "kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml"
ALERT = "BackupArtifactStale"
ARM_RE = re.compile(r'absent\(\s*backup_artifact_last_mtime_seconds\{app="([^"]+)"\}\s*\)')


def collector_apps(defaults_text: str) -> set[str]:
    """The app names the NAS-side mtime collector is rendered with."""
    data = yaml.safe_load(defaults_text) or {}
    apps = data.get("nas_backup_artifact_apps") or []
    return {a["name"] for a in apps if isinstance(a, dict) and a.get("name")}


def alert_arm_apps(rules_text: str) -> set[str]:
    """The app labels named by BackupArtifactStale's absent() arms.

    Read as text rather than through the YAML tree: the rule lives inside a
    HelmRelease `values:` blob whose PrometheusRule groups are several levels
    deep and carry Go-template `{{ $labels }}` strings, so a structural walk
    buys nothing over scoping to the alert's own block.
    """
    lines = rules_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if f"alert: {ALERT}" in ln)
    except StopIteration:
        raise SystemExit(f"ERROR: no `alert: {ALERT}` rule found in {RULES}")
    # The expr block ends at the alert's `for:` key, at the same indentation as
    # the `expr:` that opened it.
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if re.match(r"\s*for:\s", ln):
            break
        body.append(ln)
    return set(ARM_RE.findall("\n".join(body)))


def main() -> int:
    collector = collector_apps(DEFAULTS.read_text())
    arms = alert_arm_apps(RULES.read_text())

    if collector == arms:
        print(
            f"backup-artifact apps in sync: {len(collector)} app(s) "
            f"({', '.join(sorted(collector))})"
        )
        return 0

    print(f"ERROR: nas_backup_artifact_apps and {ALERT}'s absent() arms disagree.")
    missing_arm = sorted(collector - arms)
    orphan_arm = sorted(arms - collector)
    if missing_arm:
        print(
            "  Collected but NOT alerted on (a landing dir that is never created "
            "emits no series, so nothing fires): " + ", ".join(missing_arm)
        )
        print(
            f"    Fix: add `or absent(backup_artifact_last_mtime_seconds{{app=\"<name>\"}})` "
            f"to {ALERT} in\n    {RULES.relative_to(REPO)}"
        )
    if orphan_arm:
        print(
            "  Alerted on but NOT collected (the arm can never be satisfied, so "
            "it fires forever): " + ", ".join(orphan_arm)
        )
        print(
            f"    Fix: drop that arm, or re-add the app to nas_backup_artifact_apps in\n"
            f"    {DEFAULTS.relative_to(REPO)}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
