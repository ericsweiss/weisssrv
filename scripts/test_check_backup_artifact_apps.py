"""Tests for scripts/check-backup-artifact-apps.py.

The collector's app list (Ansible role defaults) and BackupArtifactStale's
per-app absent() arms (Flux-reconciled PrometheusRule) are edited on separate
lifecycles; this gate is what keeps them paired. Exercises both drift
directions plus a smoke check that the real repo is in sync today.

Run via `pytest scripts/` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "check-backup-artifact-apps.py"

_spec = importlib.util.spec_from_file_location("check_backup_artifact_apps", _SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


DEFAULTS = """
nas_backup_artifact_apps:
  - name: authentik
    pattern: "authentik-*.sql.gz"
  - name: gitlab
    pattern: "*_gitlab_backup.tar"
"""

RULES = """
              - alert: BackupArtifactStale
                expr: >-
                  (time() - backup_artifact_last_mtime_seconds > 180000)
                  or absent(backup_artifact_last_mtime_seconds{app="authentik"})
                  or absent(backup_artifact_last_mtime_seconds{app="gitlab"})
                for: 1h
                labels:
                  severity: warning
              - alert: SomethingElse
                expr: absent(backup_artifact_last_mtime_seconds{app="not-an-arm"})
                for: 1h
"""


def test_collector_apps_reads_the_name_key():
    assert mod.collector_apps(DEFAULTS) == {"authentik", "gitlab"}


def test_alert_arms_are_scoped_to_the_alert_block():
    """A later rule mentioning the same metric must not be read as an arm."""
    assert mod.alert_arm_apps(RULES) == {"authentik", "gitlab"}


def test_app_without_an_arm_is_drift():
    defaults = DEFAULTS + '  - name: pve-cluster\n    pattern: "etc-pve-*.tar.gz"\n'
    assert mod.collector_apps(defaults) - mod.alert_arm_apps(RULES) == {"pve-cluster"}


def test_arm_without_an_app_is_drift():
    rules = RULES.replace(
        '                  for: 1h', '                  for: 1h', 1
    ).replace(
        '                  or absent(backup_artifact_last_mtime_seconds{app="gitlab"})',
        '                  or absent(backup_artifact_last_mtime_seconds{app="gitlab"})\n'
        '                  or absent(backup_artifact_last_mtime_seconds{app="retired"})',
    )
    assert mod.alert_arm_apps(rules) - mod.collector_apps(DEFAULTS) == {"retired"}


def test_repo_is_in_sync():
    """Smoke: the committed defaults and alert agree right now."""
    assert mod.main() == 0
