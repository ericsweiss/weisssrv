"""The upstream-rule mirror gate must fail when a mirror falls behind its chart.

Covers both subjects: the test-dir mirrors and the in-tree replacements for the
alerts the chart's `defaultRules.disabled` list turns off.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from script_loader import load_path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "scripts" / "check-upstream-rule-mirror.py"


def _load():
    return load_path(GATE)


gate = _load()

VARS = 'helm_chart_versions:\n  kube_prometheus_stack: "91.4.1"\n'
MIRROR = "---\n# Taken from kube-prometheus-stack 91.4.1\ngroups: []\n"
RELEASE = (
    "apiVersion: helm.toolkit.fluxcd.io/v2\n"
    "kind: HelmRelease\n"
    "spec:\n"
    "  values:\n"
    "    defaultRules:\n"
    "      disabled:\n"
    "        KubeCPUOvercommit: true\n"
    "        NodeSystemdServiceFailed: true\n"
    "        NodeSystemdServiceCrashlooping: true\n"
)


def _rule(marker: str | None, alert: str = "KubeCPUOvercommit") -> str:
    header = f"        # {marker}\n" if marker else ""
    return (
        "apiVersion: monitoring.coreos.com/v1\n"
        "kind: PrometheusRule\n"
        "spec:\n"
        "  groups:\n"
        "    - name: homelab\n"
        "      rules:\n"
        f"{header}"
        f"        - alert: {alert}\n"
        "          expr: vector(1) > 0\n"
    )


def _tree(
    root: Path,
    vars_text: str = VARS,
    mirror_text: str = MIRROR,
    rule_text: str = _rule("Taken from kube-prometheus-stack 91.4.1"),
    release_text: str = RELEASE,
) -> Path:
    (root / "ansible/inventories/prod/group_vars").mkdir(parents=True)
    (root / "ansible/inventories/prod/group_vars/all.yml").write_text(vars_text)
    (root / "scripts/prometheus-rule-tests").mkdir(parents=True)
    (root / "scripts/prometheus-rule-tests/upstream-etcd.rules.yaml").write_text(mirror_text)
    (root / gate.RULES_DIR).mkdir(parents=True)
    (root / gate.RULES_DIR / "kubernetes-resources.yaml").write_text(rule_text)
    release = root / gate.CHART_RELEASE
    release.parent.mkdir(parents=True)
    release.write_text(release_text)
    return root


def test_a_mirror_at_the_pinned_version_passes(tmp_path):
    assert gate.check(_tree(tmp_path)) == []


def test_a_mirror_left_behind_a_chart_bump_fails(tmp_path):
    root = _tree(tmp_path, vars_text='helm_chart_versions:\n  kube_prometheus_stack: "92.0.0"\n')
    problems = gate.check(root)
    assert problems and "re-take the expr" in problems[0]


def test_a_mirror_with_no_taken_from_line_fails(tmp_path):
    root = _tree(tmp_path, mirror_text="---\ngroups: []\n")
    problems = gate.check(root)
    assert problems and "Taken from" in problems[0]


def test_an_unknown_chart_name_fails_rather_than_passing_silently(tmp_path):
    root = _tree(tmp_path, mirror_text="---\n# Taken from some-other-chart 1.0.0\ngroups: []\n")
    problems = gate.check(root)
    assert problems and "CHART_KEYS" in problems[0]


def test_an_in_tree_copy_with_no_marker_fails(tmp_path):
    """Mutation case: the local copy of a disabled upstream alert, unmarked."""
    root = _tree(tmp_path, rule_text=_rule(None))
    problems = gate.check(root)
    assert problems and "KubeCPUOvercommit" in problems[0]
    assert "no `# Taken from" in problems[0]


def test_an_in_tree_copy_at_a_stale_version_fails(tmp_path):
    root = _tree(tmp_path, rule_text=_rule("Taken from kube-prometheus-stack 90.0.0"))
    problems = gate.check(root)
    assert problems and "re-take the expr" in problems[0]


@pytest.mark.parametrize("relative", ["upstream/kube.yaml", "kube.yml"])
def test_a_copy_in_a_subdirectory_or_a_yml_file_is_still_checked(tmp_path, relative):
    """The in-tree walk is recursive and covers both YAML suffixes, so a copy
    cannot escape the marker rule by moving or by changing extension."""
    root = _tree(tmp_path, rule_text="---\ngroups: []\n")
    copy = root / gate.RULES_DIR / relative
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(_rule(None))
    assert any("no `# Taken from" in problem for problem in gate.check(root))


def test_a_not_mirrored_alert_needs_no_marker(tmp_path):
    """The NodeSystemd replacements select a different job, so they track nothing."""
    root = _tree(tmp_path, rule_text=_rule(None, alert="NodeSystemdServiceFailed"))
    assert gate.check(root) == []


def test_a_not_mirrored_entry_the_chart_re_enabled_fails(tmp_path):
    root = _tree(
        tmp_path,
        release_text=(
            "spec:\n  values:\n    defaultRules:\n      disabled:\n"
            "        KubeCPUOvercommit: true\n"
        ),
    )
    problems = gate.check(root)
    assert problems and "NOT_MIRRORED" in problems[0]


def test_an_empty_mirror_dir_is_a_could_not_inspect_error(tmp_path):
    (tmp_path / "ansible/inventories/prod/group_vars").mkdir(parents=True)
    (tmp_path / "ansible/inventories/prod/group_vars/all.yml").write_text(VARS)
    (tmp_path / "scripts/prometheus-rule-tests").mkdir(parents=True)
    with pytest.raises(gate.OperatorError) as excinfo:
        gate.check(tmp_path)
    assert "vacuously" in str(excinfo.value)


def test_a_release_that_disables_nothing_is_a_could_not_inspect_error(tmp_path):
    root = _tree(tmp_path, release_text="spec:\n  values: {}\n")
    with pytest.raises(gate.OperatorError) as excinfo:
        gate.check(root)
    assert "vacuously" in str(excinfo.value)


def test_a_disabled_set_fully_exempt_is_a_could_not_inspect_error(tmp_path):
    """Mutation case: growing NOT_MIRRORED until it swallows the disabled set
    retires the in-tree arm, and the gate would pass over nothing."""
    release = RELEASE.replace("        KubeCPUOvercommit: true\n", "")
    with pytest.raises(gate.OperatorError) as caught:
        gate.check(_tree(tmp_path, release_text=release))
    assert "pass vacuously" in str(caught.value)


def test_a_malformed_release_exits_two_rather_than_reading_as_a_finding(tmp_path):
    """rc 1 here would claim a mirror fell behind its chart; nothing was read."""
    root = _tree(tmp_path, release_text="spec:\n  values: {\n")
    assert gate.main(["--repo-root", str(root)]) == 2


def test_an_unreadable_version_file_exits_two(tmp_path):
    root = _tree(tmp_path)
    (root / gate.VARS_FILE).unlink()
    assert gate.main(["--repo-root", str(root)]) == 2


def test_the_live_repo_is_in_step():
    run = subprocess.run([sys.executable, str(GATE)], capture_output=True, text=True, cwd=REPO)
    assert run.returncode == 0, run.stdout + run.stderr
