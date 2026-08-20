#!/usr/bin/env python3
"""Unit tests for scripts/maintenance-lib.sh.

The library holds the pure parsing / state-machine helpers extracted from the
maintenance scripts that run in production maintenance CI. They used to be inline
awk/grep with zero coverage, so a kubectl column-order change or a regression in
a fail-closed verdict would have failed silently. Each test sources the library
in a bash subprocess and drives one helper with synthetic input.

Run with pytest:
    pytest scripts/test_maintenance_lib.py -v
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent / "maintenance-lib.sh"


def _run(func_call: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Source the library and run a function call, returning the completed proc.

    `func_call` is bash appended after sourcing, e.g. 'not_ready_node_names'
    or 'deployment_replicas_ok 2 2'.
    """
    script = f". {LIB}\n{func_call}\n"
    return subprocess.run(
        ["bash", "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
    )


# not_ready_node_names

class TestNotReadyNodeNames:
    def test_all_ready_prints_nothing(self):
        out = _run("not_ready_node_names", "node-a Ready control-plane 1d v1\n"
                                           "node-b Ready <none> 1d v1\n").stdout.strip()
        assert out == ""

    def test_cordoned_is_ready(self):
        # Ready,SchedulingDisabled is healthy-but-cordoned, must NOT be listed.
        out = _run(
            "not_ready_node_names",
            "node-a Ready,SchedulingDisabled <none> 1d v1\n",
        ).stdout.strip()
        assert out == ""

    def test_notready_names_listed(self):
        # Both a plain NotReady and a cordoned-AND-down node are listed by NAME.
        out = _run(
            "not_ready_node_names",
            "node-a Ready <none> 1d v1\n"
            "node-b NotReady <none> 1d v1\n"
            "node-c NotReady,SchedulingDisabled <none> 1d v1\n",
        ).stdout.split()
        assert out == ["node-b", "node-c"]

    def test_empty_input_empty(self):
        assert _run("not_ready_node_names", "").stdout.strip() == ""


# list_unhealthy_pods

class TestListUnhealthyPods:
    # Columns mirror `kubectl get pods -A --no-headers`:
    #   NAMESPACE NAME READY STATUS RESTARTS AGE
    def test_running_and_ready_is_healthy(self):
        out = _run(
            "list_unhealthy_pods",
            "ns app-1 1/1 Running 0 5m\n"
            "ns app-2 2/2 Running 0 5m\n",
        ).stdout.strip()
        assert out == ""

    def test_ci_job_pods_are_excused_in_any_state_runner_namespaces_only(self):
        out = _run(
            "list_unhealthy_pods",
            "gitlab-runner runner-abc-project-4-concurrent-1 1/1 Terminating 0 3m\n"
            "gitlab-runner-privileged runner-def-concurrent-0 0/2 Pending 0 1m\n"
            "gitlab-runner-privileged runner-ghi-concurrent-2 0/2 ImagePullBackOff 0 9m\n"
            "downloads sonarr-abc 1/1 Terminating 0 4h\n"
            "hermes runner-lookalike 0/1 Pending 0 5m\n",
        ).stdout.strip()
        assert "sonarr" in out
        assert "runner-lookalike" in out
        assert "concurrent" not in out

    def test_runner_manager_pods_stay_covered(self):
        out = _run(
            "list_unhealthy_pods",
            "gitlab-runner gitlab-runner-5db45fffdc-zn4r6 0/1 CrashLoopBackOff 4 10m\n"
            "gitlab-runner-privileged gitlab-runner-privileged-abc 0/1 Pending 0 9m\n",
        ).stdout.strip()
        assert "gitlab-runner-5db45fffdc" in out
        assert "gitlab-runner-privileged-abc" in out

    def test_completed_and_succeeded_skipped(self):
        out = _run(
            "list_unhealthy_pods",
            "ns job-1 0/1 Completed 0 5m\n"
            "ns job-2 0/1 Succeeded 0 5m\n",
        ).stdout.strip()
        assert out == ""

    def test_error_and_failed_batch_pods_skipped(self):
        # Terminal Job/CronJob failures (restartPolicy Never/OnFailure) — e.g. a
        # cloudflare-ddns retry pod that hit a transient egress blip before the
        # run succeeded. Not a maintenance regression; must not be flagged.
        out = _run(
            "list_unhealthy_pods",
            "cloudflare-ddns cloudflare-ddns-29705525-66qkg 0/1 Error 0 5m\n"
            "ns job-3 0/1 Failed 0 5m\n",
        ).stdout.strip()
        assert out == ""

    def test_crashloop_still_flagged_not_confused_with_error(self):
        # A long-running workload that keeps crashing surfaces as
        # CrashLoopBackOff (restartPolicy Always never reaches Error/Failed) and
        # MUST still be flagged.
        out = _run(
            "list_unhealthy_pods",
            "ns app-1 0/1 CrashLoopBackOff 7 20m\n",
        ).stdout
        assert "app-1" in out

    def test_init_error_not_substring_skipped(self):
        # The Error/Failed skip is an EXACT $4 match, not a substring. A pod stuck
        # in Init:Error / Init:CrashLoopBackOff (a failing init container) is a
        # real problem and MUST still be flagged, not swallowed by the skip.
        out = _run(
            "list_unhealthy_pods",
            "ns app-1 0/1 Init:Error 0 5m\n"
            "ns app-2 0/1 Init:CrashLoopBackOff 2 5m\n",
        ).stdout
        assert "app-1" in out
        assert "app-2" in out

    def test_real_failure_statuses_still_flagged(self):
        # Only the terminal BATCH statuses Error/Failed are excused. Evicted,
        # OOMKilled, and NodeLost are real failures with distinct $4 tokens and
        # MUST still be flagged (boundary in the other direction from the skip).
        out = _run(
            "list_unhealthy_pods",
            "ns app-1 0/1 Evicted 0 5m\n"
            "ns app-2 0/1 OOMKilled 3 5m\n"
            "ns app-3 1/1 NodeLost 0 5m\n",
        ).stdout
        assert "app-1" in out
        assert "app-2" in out
        assert "app-3" in out

    def test_non_running_flagged(self):
        out = _run(
            "list_unhealthy_pods",
            "ns app-1 0/1 ContainerCreating 0 5s\n"
            "ns app-2 0/1 CrashLoopBackOff 3 5m\n",
        ).stdout
        assert "app-1" in out
        assert "app-2" in out

    def test_running_but_not_ready_flagged(self):
        # Running but READY a/b with a != b (e.g. failing readiness probe).
        out = _run(
            "list_unhealthy_pods",
            "ns app-1 1/2 Running 0 5m\n",
        ).stdout
        assert "app-1" in out

    def test_mixed(self):
        out = _run(
            "list_unhealthy_pods",
            "ns ok 1/1 Running 0 5m\n"
            "ns done 0/1 Completed 0 5m\n"
            "ns bad 0/1 Pending 0 5m\n",
        ).stdout
        assert "ok" not in out
        assert "done" not in out
        assert "bad" in out


# deployment_replicas_ok

class TestDeploymentReplicasOk:
    def test_equal_ok(self):
        assert _run("deployment_replicas_ok 2 2").returncode == 0

    def test_greater_ok(self):
        assert _run("deployment_replicas_ok 3 2").returncode == 0

    def test_less_fails(self):
        assert _run("deployment_replicas_ok 1 2").returncode != 0

    def test_blank_available_defaults_zero_fails(self):
        # availableReplicas absent (blank) -> defaults to 0, desired 1 -> fail.
        assert _run('deployment_replicas_ok "" 1').returncode != 0

    def test_blank_desired_defaults_one(self):
        assert _run('deployment_replicas_ok 1 ""').returncode == 0

    def test_both_blank_fails(self):
        # avail 0 < desired 1.
        assert _run('deployment_replicas_ok "" ""').returncode != 0

    def test_non_numeric_does_not_falsely_pass(self):
        # A garbage availableReplicas must not be treated as ok.
        assert _run("deployment_replicas_ok foo 2").returncode != 0


# deployment_pod_nodes

class TestDeploymentPodNodes:
    # Input mirrors the verify's jsonpath rows: "<pod-name>\t<nodeName>".
    def test_matches_own_pod_not_vowel_bearing_sibling(self):
        # cert-manager pods (hash = no-vowel SafeEncode) match; cert-manager-webhook
        # / cert-manager-cainjector ('webhook'/'cainjector' contain vowels) do NOT.
        rows = (
            "cert-manager-65bf9cc8d9-abc12\tnode-a\n"
            "cert-manager-webhook-7d9f8c6b5-xkz\tnode-b\n"
            "cert-manager-cainjector-6b8d9f7c5-qq\tnode-c\n"
        )
        out = _run("deployment_pod_nodes cert-manager", rows).stdout.split()
        assert out == ["node-a"]

    def test_coredns_excludes_autoscaler(self):
        rows = (
            "coredns-77ccd6b8f-zzz\tnode-a\n"
            "coredns-autoscaler-5d8b9c7f6-bbb\tnode-b\n"
        )
        out = _run("deployment_pod_nodes coredns", rows).stdout.split()
        assert out == ["node-a"]

    def test_no_match_prints_nothing(self):
        rows = "other-app-7d9f8c6b5-xkz\tnode-a\n"
        assert _run("deployment_pod_nodes cert-manager", rows).stdout.strip() == ""

    def test_unscheduled_pod_emits_sentinel(self):
        # An evicted / not-yet-scheduled pod (empty nodeName) prints "<unscheduled>"
        # so the verify can excuse it during a kured drain instead of ERRORing.
        rows = "metallb-controller-7d9f8c6b5-xkz\t\n"  # empty nodeName after the tab
        assert _run("deployment_pod_nodes metallb-controller", rows).stdout.strip() == "<unscheduled>"


# kured_rebooting_filter

class TestKuredRebootingFilter:
    # Input mirrors the verify's jsonpath rows: "node<TAB>annotation<TAB>unschedulable".
    def test_annotated_and_cordoned_listed(self):
        rows = "node-a\ttrue\ttrue\n"
        assert _run("kured_rebooting_filter", rows).stdout.split() == ["node-a"]

    def test_annotated_but_schedulable_not_listed(self):
        # kured writes the annotation BEFORE its block-check and does not clear
        # it when blocked — annotated-but-schedulable is stale, NOT mid-reboot.
        rows = "node-a\ttrue\t\n"
        assert _run("kured_rebooting_filter", rows).stdout.strip() == ""

    def test_cordoned_but_unannotated_not_listed(self):
        # A manual/op-3 cordon without the kured annotation must not be excused.
        rows = "node-a\t\ttrue\n"
        assert _run("kured_rebooting_filter", rows).stdout.strip() == ""

    def test_mixed_rows(self):
        rows = (
            "node-a\t\t\n"           # healthy
            "node-b\ttrue\ttrue\n"   # actively rebooting
            "node-c\ttrue\t\n"       # annotated, blocked (schedulable)
            "node-d\t\ttrue\n"       # cordoned, no annotation
        )
        assert _run("kured_rebooting_filter", rows).stdout.split() == ["node-b"]

    def test_empty_input_empty(self):
        assert _run("kured_rebooting_filter", "").stdout.strip() == ""


# classify_not_ready_nodes

class TestClassifyNotReadyNodes:
    def _classify(self, kured_names: str, not_ready: str) -> list[str]:
        res = _run(f"classify_not_ready_nodes '{kured_names}'", not_ready)
        assert res.returncode == 0, res.stderr
        return res.stdout.splitlines()

    def test_kured_rebooting_node_excused(self):
        assert self._classify("node-a", "node-a\n") == ["excused node-a"]

    def test_unexcused_node_errors(self):
        assert self._classify("node-a", "node-b\n") == ["error node-b"]

    def test_no_kured_nodes_all_error(self):
        assert self._classify("", "node-a\nnode-b\n") == [
            "error node-a",
            "error node-b",
        ]

    def test_mixed_verdicts(self):
        # needs_grace in the verify is "any error line" — a mix must surface both.
        out = self._classify("node-a", "node-a\nnode-b\n")
        assert out == ["excused node-a", "error node-b"]

    def test_exact_name_match_only(self):
        # Substring/prefix names must not be excused (grep -qxF semantics).
        assert self._classify("node-a", "node-a2\n") == ["error node-a2"]

    def test_reclassification_with_fresh_input_clears_excuse(self):
        # After the grace re-read, a node kured finished with (annotation gone)
        # that is STILL NotReady must flip from excused to error.
        assert self._classify("node-a", "node-a\n") == ["excused node-a"]
        assert self._classify("", "node-a\n") == ["error node-a"]

    def test_empty_input_no_verdicts(self):
        assert self._classify("node-a", "") == []


# rearm_marker_host

class TestRearmMarkerHost:
    def test_plain_hostname(self):
        assert _run("rearm_marker_host", "pve-opt-01\n").stdout.strip() == "pve-opt-01"

    def test_whitespace_stripped(self):
        assert _run("rearm_marker_host", "  pve-opt-02 \n").stdout.strip() == "pve-opt-02"

    def test_only_first_line_used(self):
        out = _run("rearm_marker_host", "pve-opt-03\npve-opt-04\n").stdout.strip()
        assert out == "pve-opt-03"

    def test_blank_marker_prints_nothing(self):
        assert _run("rearm_marker_host", "   \n").stdout.strip() == ""

    def test_empty_marker_prints_nothing(self):
        assert _run("rearm_marker_host", "").stdout.strip() == ""


# rearm_remote_command

class TestRearmRemoteCommand:
    def _cmd(self, *args) -> str:
        res = _run("rearm_remote_command " + " ".join(args))
        assert res.returncode == 0, res.stderr
        return res.stdout

    def test_prompt_armed_before_fallback_teardown(self):
        # The safety guarantee: systemd-run arms the prompt unit BEFORE the
        # fallback timer is touched, so a failed arm leaves the fallback intact.
        cmd = self._cmd("60")
        arm = cmd.index("systemd-run")
        fallback_stop = cmd.index("systemctl stop maintenance-self-reboot.timer")
        assert arm < fallback_stop

    def test_fallback_teardown_gated_on_arm_success(self):
        # The teardown block must be joined with && (short-circuits on a failed
        # systemd-run), not `;` (which would tear down unconditionally).
        cmd = self._cmd("60")
        after_arm = cmd.split("systemctl reboot", 1)[1]
        assert after_arm.lstrip().startswith("&&")

    def test_pre_arm_cleanup_targets_prompt_unit_only(self):
        # Everything before systemd-run may only touch the -prompt units; the
        # fallback units (no -prompt suffix) must appear only after the && gate.
        cmd = self._cmd("60")
        before_arm = cmd.split("systemd-run", 1)[0]
        assert "maintenance-self-reboot.timer" not in before_arm
        assert "maintenance-self-reboot.service" not in before_arm
        assert "maintenance-self-reboot-prompt.timer" in before_arm

    def test_delay_parameter_honored(self):
        assert "--on-active=90s" in self._cmd("90")
        assert "--on-active=60s" in self._cmd()  # default

    def test_arms_the_prompt_unit(self):
        assert "--unit=maintenance-self-reboot-prompt" in self._cmd("60")


# ha_reset_verdict

class TestHaResetVerdict:
    def test_ok(self):
        out = _run("ha_reset_verdict", "pve-opt-01 | CHANGED | rc=0 >>\n"
                                       "vmreset:ok host=pve-opt-01\n").stdout.strip()
        assert out == "ok"

    def test_err(self):
        out = _run("ha_reset_verdict", "vmreset:err host=pve-opt-01\n").stdout.strip()
        assert out == "err"

    def test_notfound_when_no_token(self):
        # No host emitted a token (VM not found / host unreachable).
        out = _run("ha_reset_verdict", "pve-opt-01 | UNREACHABLE!\n").stdout.strip()
        assert out == "notfound"

    def test_fail_closed_both_tokens(self):
        # Split-brain: both ok and err present -> err wins (fail-closed).
        out = _run(
            "ha_reset_verdict",
            "vmreset:ok host=pve-opt-01\nvmreset:err host=pve-opt-02\n",
        ).stdout.strip()
        assert out == "err"

    def test_echoed_command_source_does_not_match(self):
        # An echoed (indented) command source containing the literal 'vmreset:$r'
        # must not match the column-0-anchored token -> notfound.
        echoed = textwrap.dedent(
            """\
            pve-opt-01 | FAILED | rc=2 >>
            non-zero return code
            MODULE_STDERR: + echo "vmreset:$r host=$(hostname)"
            """
        )
        out = _run("ha_reset_verdict", echoed).stdout.strip()
        assert out == "notfound"

    def test_midline_token_does_not_match(self):
        # The marker must be at column 0; a mid-line occurrence is rejected.
        out = _run(
            "ha_reset_verdict",
            "some prefix vmreset:ok host=pve-opt-01\n",
        ).stdout.strip()
        assert out == "notfound"


# ha_observe_step

class TestHaObserveStep:
    def _step(self, streak, went_down, probe):
        out = _run(f"ha_observe_step {streak} {went_down} {probe}").stdout.strip()
        new_streak, new_went_down, verdict = out.split()
        return int(new_streak), new_went_down, verdict

    def test_single_transient_down_does_not_trip(self):
        # One failed probe must NOT latch went_down (debounce needs streak>=2).
        streak, went_down, verdict = self._step(0, "false", "down")
        assert streak == 1
        assert went_down == "false"
        assert verdict == "waiting"

    def test_two_downs_latch_went_down(self):
        streak, went_down, verdict = self._step(1, "false", "down")
        assert streak == 2
        assert went_down == "true"
        assert verdict == "waiting"

    def test_up_after_confirmed_down_is_healthy(self):
        streak, went_down, verdict = self._step(2, "true", "up")
        assert streak == 0
        assert went_down == "true"
        assert verdict == "healthy"

    def test_up_while_never_down_stays_waiting(self):
        # Never-observed-down: up probe with went_down=false must NOT report
        # healthy — ha_settle_verdict owns that fast-reset case.
        streak, went_down, verdict = self._step(0, "false", "up")
        assert streak == 0
        assert went_down == "false"
        assert verdict == "waiting"

    def test_up_resets_streak(self):
        # A single failure then a success resets the streak without latching.
        streak, went_down, verdict = self._step(1, "false", "up")
        assert streak == 0
        assert went_down == "false"
        assert verdict == "waiting"

    def test_full_down_then_up_sequence(self):
        # Walk a realistic restart: up, down, down (latch), down, up (healthy).
        streak, went_down = 0, "false"
        for probe, exp_verdict in [
            ("up", "waiting"),
            ("down", "waiting"),
            ("down", "waiting"),  # streak hits 2 -> went_down latches
            ("down", "waiting"),
            ("up", "healthy"),
        ]:
            streak, went_down, verdict = self._step(streak, went_down, probe)
            assert verdict == exp_verdict, (probe, streak, went_down, verdict)
        assert went_down == "true"


# ha_settle_verdict

class TestHaSettleVerdict:
    def _verdict(self, went_down, streak, elapsed, settle):
        return _run(
            f"ha_settle_verdict {went_down} {streak} {elapsed} {settle}"
        ).stdout.strip()

    def test_never_down_after_settle_is_unverified(self):
        # Never observed down + settle elapsed -> accept as a probable (but
        # UNVERIFIED) fast reset.
        assert self._verdict("false", 0, 60, 60) == "healthy-unverified"

    def test_never_down_before_settle_keeps_waiting(self):
        assert self._verdict("false", 0, 59, 60) == "keep-waiting"

    def test_latched_went_down_not_short_circuited(self):
        # Any observed downtime must defer to the ha_observe_step verdict.
        assert self._verdict("true", 0, 120, 60) == "keep-waiting"

    def test_pending_down_streak_not_short_circuited(self):
        # A single (un-latched) down probe still blocks the never-down path.
        assert self._verdict("false", 1, 120, 60) == "keep-waiting"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
