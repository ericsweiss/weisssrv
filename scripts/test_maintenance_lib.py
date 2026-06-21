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

    `func_call` is bash appended after sourcing, e.g. 'count_not_ready_nodes'
    or 'deployment_replicas_ok 2 2'.
    """
    script = f". {LIB}\n{func_call}\n"
    return subprocess.run(
        ["bash", "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
    )


# --- count_not_ready_nodes -------------------------------------------------

class TestCountNotReadyNodes:
    def test_all_ready(self):
        out = _run("count_not_ready_nodes", "node-a Ready control-plane 1d v1\n"
                                            "node-b Ready <none> 1d v1\n").stdout.strip()
        assert out == "0"

    def test_cordoned_is_ready(self):
        # Ready,SchedulingDisabled is healthy-but-cordoned, must NOT count.
        out = _run(
            "count_not_ready_nodes",
            "node-a Ready,SchedulingDisabled <none> 1d v1\n",
        ).stdout.strip()
        assert out == "0"

    def test_notready_counted(self):
        out = _run(
            "count_not_ready_nodes",
            "node-a Ready <none> 1d v1\n"
            "node-b NotReady <none> 1d v1\n"
            "node-c NotReady,SchedulingDisabled <none> 1d v1\n",
        ).stdout.strip()
        assert out == "2"

    def test_empty_input_zero(self):
        assert _run("count_not_ready_nodes", "").stdout.strip() == "0"


# --- list_unhealthy_pods ---------------------------------------------------

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

    def test_completed_and_succeeded_skipped(self):
        out = _run(
            "list_unhealthy_pods",
            "ns job-1 0/1 Completed 0 5m\n"
            "ns job-2 0/1 Succeeded 0 5m\n",
        ).stdout.strip()
        assert out == ""

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


# --- deployment_replicas_ok ------------------------------------------------

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


# --- ha_reset_verdict ------------------------------------------------------

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


# --- ha_observe_step -------------------------------------------------------

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


# --- ha_settle_verdict -----------------------------------------------------

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
