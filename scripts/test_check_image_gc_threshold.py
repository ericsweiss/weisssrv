"""Tests that check-image-gc-threshold.py fails on drift at each of the three sites, on an inhibitor that mutes the alert for good, and never passes vacuously."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-image-gc-threshold.py"

K3S_VARS = """\
k3s_flannel_backend: "wireguard-native"

k3s_kubelet_args:
  - "image-gc-high-threshold=70"
  - "image-gc-low-threshold=50"
"""

RULES = """\
---
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: homelab-storage
spec:
  groups:
    - name: homelab.storage
      rules:
        - alert: DiskUsageWarning
          expr: node_filesystem_used_percent > 80
        # The k3s node root fs is also the kubelet image filesystem. Above the
        # 70% image-gc-high-threshold the kubelet GCs continuously, which
        # DiskUsageWarning at 80% catches too late.
        - alert: KubeletImageGCIneffective
          expr: >-
            100 * (1 - node_filesystem_avail_bytes{mountpoint="/"}
            / node_filesystem_size_bytes{mountpoint="/"}) > 70
          for: 30m
          annotations:
            description: >-
              Root filesystem is {{ $value | printf "%.1f" }}% used, above the
              kubelet image-gc-high-threshold (70%).
"""

AM_CONFIG = """\
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: alertmanager-config
spec:
  target:
    template:
      data:
        alertmanager.yaml: |
          global:
            smtp_auth_password: {{ .smtpPassword | quote }}
          inhibit_rules:
            - source_matchers:
                - alertname="DiskUsageWarning"
              target_matchers:
                - alertname="KubeletImageGCIneffective"
              equal:
                - instance
                - mountpoint
"""

RULES_PATH = "kubernetes/infrastructure/observability/rules/storage.yaml"
VARS_PATH = "ansible/inventories/prod/group_vars/k3s.yml"
AM_PATH = (
    "kubernetes/infrastructure/observability/kube-prometheus-stack/alertmanager-config.yaml"
)


def _write(
    root: Path, k3s_vars: str = K3S_VARS, rules: str = RULES, am_config: str = AM_CONFIG
) -> Path:
    for rel, body in ((VARS_PATH, k3s_vars), (RULES_PATH, rules), (AM_PATH, am_config)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(root)],
        capture_output=True, text=True,
    )


def test_in_sync_passes(tmp_path):
    result = _run(_write(tmp_path))
    assert result.returncode == 0, result.stderr


def test_kubelet_watermark_bumped_alone_fails(tmp_path):
    result = _run(_write(tmp_path, k3s_vars=K3S_VARS.replace("threshold=70", "threshold=75")))
    assert result.returncode == 1
    assert "75" in result.stdout


def test_expr_threshold_bumped_alone_fails(tmp_path):
    result = _run(_write(tmp_path, rules=RULES.replace(") > 70", ") > 75")))
    assert result.returncode == 1
    assert "KubeletImageGCIneffective" in result.stdout


def test_description_left_behind_fails(tmp_path):
    """The expr and the watermark agree, but the prose still names the old number."""
    rules = RULES.replace("image-gc-high-threshold (70%)", "image-gc-high-threshold (65%)")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 1
    assert "65%" in result.stdout


def test_comment_left_behind_fails(tmp_path):
    rules = RULES.replace("# 70% image-gc-high-threshold", "# 65% image-gc-high-threshold")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 1
    assert "65%" in result.stdout


def test_missing_kubelet_arg_exits_two(tmp_path):
    k3s_vars = K3S_VARS.replace('  - "image-gc-high-threshold=70"\n', "")
    result = _run(_write(tmp_path, k3s_vars=k3s_vars))
    assert result.returncode == 2


def test_missing_alert_exits_two(tmp_path):
    rules = RULES.replace("alert: KubeletImageGCIneffective", "alert: SomethingElse")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 2


def test_expr_without_a_comparison_exits_two(tmp_path):
    rules = RULES.replace('/ node_filesystem_size_bytes{mountpoint="/"}) > 70', "vector(1)")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 2


def test_prose_mention_dropped_exits_two(tmp_path):
    """One prose site left means a half-done bump could no longer be caught."""
    rules = RULES.replace("image-gc-high-threshold (70%)", "its high watermark")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 2


def test_missing_rules_file_exits_two(tmp_path):
    root = _write(tmp_path)
    (root / RULES_PATH).unlink()
    result = _run(root)
    assert result.returncode == 2


def test_an_inhibitor_at_the_same_threshold_fails(tmp_path):
    """DiskUsageWarning muting at the GC watermark means the GC alert can never
    notify: the inhibitor fires whenever it does."""
    rules = RULES.replace("node_filesystem_used_percent > 80", "node_filesystem_used_percent > 70")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 1
    assert "can never notify" in result.stdout


def test_an_inhibitor_below_the_threshold_fails(tmp_path):
    rules = RULES.replace("node_filesystem_used_percent > 80", "node_filesystem_used_percent > 65")
    result = _run(_write(tmp_path, rules=rules))
    assert result.returncode == 1
    assert "DiskUsageWarning" in result.stdout


def test_dropping_the_inhibit_pair_clears_the_arm(tmp_path):
    """The pair is the only reason the thresholds have to be ordered."""
    am_config = AM_CONFIG.replace(
        'alertname="KubeletImageGCIneffective"', 'alertname="InodeUsageWarning"'
    )
    rules = RULES.replace("node_filesystem_used_percent > 80", "node_filesystem_used_percent > 65")
    result = _run(_write(tmp_path, rules=rules, am_config=am_config))
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_alertmanager_config_with_no_inhibit_rules_exits_two(tmp_path):
    am_config = AM_CONFIG.replace("          inhibit_rules:", "          route:")
    result = _run(_write(tmp_path, am_config=am_config))
    assert result.returncode == 2


def test_a_missing_alertmanager_config_exits_two(tmp_path):
    root = _write(tmp_path)
    (root / AM_PATH).unlink()
    result = _run(root)
    assert result.returncode == 2


def test_real_repo_is_in_sync():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
