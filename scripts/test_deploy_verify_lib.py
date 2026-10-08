"""Unit tests for the deploy-verify-lib.sh classifiers that gate the
deploy-verify CI job. Each test sources the library in a bash subprocess and
drives one helper with recorded kubectl fixtures. jq is required, and asserted.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import source_and_run

LIB = Path(__file__).resolve().parent / "deploy-verify-lib.sh"


@pytest.fixture(autouse=True)
def _jq_present() -> None:
    # Fail rather than skip: without jq every classifier returns its fail-closed
    # sentinel and the suite reads as a logic bug, not a missing binary.
    assert shutil.which("jq"), (
        "jq is not installed, so the deploy-verify classifiers are unverified. "
        "Install jq in the job that runs this suite."
    )


def _run(func_call: str, stdin: str = "") -> subprocess.CompletedProcess:
    return source_and_run(LIB, func_call, stdin)


def _ready(status: str) -> dict:
    return {"status": {"conditions": [{"type": "Ready", "status": status}]}}


def _items(*objs) -> str:
    return json.dumps({"items": list(objs)})


# count_not_ready


class TestCountNotReady:
    def test_all_ready_zero(self):
        out = _run("count_not_ready", _items(_ready("True"), _ready("True"))).stdout.strip()
        assert out == "0"

    def test_some_not_ready(self):
        out = _run("count_not_ready", _items(_ready("True"), _ready("False"))).stdout.strip()
        assert out == "1"

    def test_missing_ready_condition_counts(self):
        # No Ready condition at all -> not ready (length == 0 branch).
        out = _run("count_not_ready", _items({"status": {"conditions": []}})).stdout.strip()
        assert out == "1"

    def test_no_conditions_key_counts(self):
        out = _run("count_not_ready", _items({"status": {}})).stdout.strip()
        assert out == "1"

    def test_empty_items_zero(self):
        assert _run("count_not_ready", _items()).stdout.strip() == "0"

    def test_garbage_input_fails_closed_999(self):
        # Non-JSON on stdin -> jq errors -> fail-closed 999 (treat as not ready).
        # Mirrors the caller's `... | jq 2>/dev/null || echo 999` guard.
        assert _run("count_not_ready", "not json at all").stdout.strip() == "999"

    def test_empty_input_yields_empty(self):
        # jq exits 0 with no output on empty stdin, so `|| echo 999` does not
        # fire: only malformed JSON trips the fail-closed default. kubectl
        # returns valid JSON or nothing, and steady_state("") -> "false".
        assert _run("count_not_ready", "").stdout.strip() == ""


# not_ready_ns_names


class TestNotReadyNsNames:
    def _obj(self, ns, name, status):
        o = _ready(status)
        o["metadata"] = {"namespace": ns, "name": name}
        return o

    def test_lists_only_not_ready(self):
        out = _run(
            "not_ready_ns_names",
            _items(self._obj("a", "ok", "True"), self._obj("b", "bad", "False")),
        ).stdout
        assert "  b/bad" in out
        assert "ok" not in out

    def test_all_ready_empty(self):
        out = _run("not_ready_ns_names", _items(self._obj("a", "ok", "True"))).stdout.strip()
        assert out == ""

    def test_a_jq_failure_is_loud_not_an_empty_list(self):
        """Empty output must mean "nothing not-Ready", never "the query broke"."""
        res = _run("not_ready_ns_names", "not json at all")
        assert res.returncode != 0
        assert res.stdout.strip() == ""
        assert "jq failed" in res.stderr


# steady_state


class TestSteadyState:
    def test_zero_is_steady(self):
        assert _run("steady_state 0").stdout.strip() == "true"

    def test_nonzero_is_bootstrap(self):
        assert _run("steady_state 3").stdout.strip() == "false"

    def test_fail_closed_999_is_bootstrap(self):
        # count_not_ready emits 999 on a probe failure -> must NOT read as steady.
        assert _run("steady_state 999").stdout.strip() == "false"

    def test_blank_is_bootstrap(self):
        assert _run('steady_state ""').stdout.strip() == "false"

    def test_missing_arg_is_bootstrap(self):
        assert _run("steady_state").stdout.strip() == "false"


# nodes_not_ready_count


class TestNodesNotReadyCount:
    def test_all_ready_zero(self):
        rows = "node-a Ready control-plane 1d v1\nnode-b Ready <none> 1d v1\n"
        assert _run("nodes_not_ready_count", rows).stdout.strip() == "0"

    def test_counts_not_ready(self):
        rows = "node-a Ready <none> 1d v1\nnode-b NotReady <none> 1d v1\n"
        assert _run("nodes_not_ready_count", rows).stdout.strip() == "1"

    def test_cordoned_counts_as_not_ready(self):
        # deploy-verify's node gate is an EXACT "Ready" match (distinct from
        # post-maintenance's `^Ready` prefix), so Ready,SchedulingDisabled counts.
        rows = "node-a Ready,SchedulingDisabled <none> 1d v1\n"
        assert _run("nodes_not_ready_count", rows).stdout.strip() == "1"

    def test_empty_zero(self):
        assert _run("nodes_not_ready_count", "").stdout.strip() == "0"


# pods_not_running_or_completed


class TestPodsNotRunningOrCompleted:
    # Columns mirror `kubectl get pods -n <ns> --no-headers`: NAME READY STATUS ...
    def test_running_and_completed_excluded(self):
        rows = "ok 1/1 Running 0 5m\ndone 0/1 Completed 0 5m\n"
        assert _run("pods_not_running_or_completed", rows).stdout.strip() == ""

    def test_bad_included(self):
        rows = "ok 1/1 Running 0 5m\nbad 0/1 CrashLoopBackOff 3 5m\npend 0/1 Pending 0 5m\n"
        out = _run("pods_not_running_or_completed", rows).stdout
        assert "bad" in out
        assert "pend" in out
        assert "ok" not in out


# pods_non_transient


class TestPodsNonTransient:
    def test_transient_states_dropped(self):
        rows = (
            "a 0/1 Pending 0 5s\n"
            "b 0/1 ContainerCreating 0 5s\n"
            "c 0/1 PodInitializing 0 5s\n"
            "d 1/1 Terminating 0 5s\n"
            "e 0/1 Init:1/2 0 5s\n"
        )
        assert _run("pods_non_transient", rows).stdout.strip() == ""

    def test_real_failures_kept(self):
        rows = (
            "a 0/1 CrashLoopBackOff 3 5m\n"
            "b 0/1 ImagePullBackOff 0 5m\n"
            "c 0/1 InvalidImageName 0 5m\n"
            "d 0/1 Init:Error 0 5m\n"
        )
        out = _run("pods_non_transient", rows).stdout
        for p in ("a", "b", "c", "d"):
            assert p in out

    def test_init_error_not_confused_with_init_progress(self):
        # Init:N/M is transient (progressing); Init:Error / Init:CrashLoopBackOff
        # are real failures and must NOT be dropped.
        rows = "prog 0/1 Init:0/2 0 5s\nfail 0/1 Init:CrashLoopBackOff 2 5m\n"
        out = _run("pods_non_transient", rows).stdout
        assert "fail" in out
        assert "prog" not in out


# pods_running_unready


class TestPodsRunningUnready:
    def test_ready_running_excluded(self):
        rows = "a 1/1 Running 0 5m\nb 2/2 Running 0 5m\n"
        assert _run("pods_running_unready", rows).stdout.strip() == ""

    def test_running_but_unready_included(self):
        rows = "a 1/2 Running 0 5m\nb 0/1 Running 0 5m\n"
        out = _run("pods_running_unready", rows).stdout
        assert "a" in out
        assert "b" in out

    def test_non_running_ignored(self):
        # This helper only flags Running-but-unready; a Pending pod is another
        # check's job (pods_not_running_or_completed).
        rows = "a 0/1 Pending 0 5m\n"
        assert _run("pods_running_unready", rows).stdout.strip() == ""


# helmreleases_not_ready_names


class TestHelmreleasesNotReadyNames:
    def _hr(self, name, status):
        o = _ready(status)
        o["metadata"] = {"name": name}
        return o

    def test_lists_not_ready(self):
        out = _run(
            "helmreleases_not_ready_names",
            _items(self._hr("ok", "True"), self._hr("bad", "False")),
        ).stdout
        assert "bad" in out
        assert "ok" not in out

    def test_all_ready_empty(self):
        out = _run("helmreleases_not_ready_names", _items(self._hr("ok", "True"))).stdout.strip()
        assert out == ""

    def test_a_jq_failure_is_loud_not_an_empty_list(self):
        res = _run("helmreleases_not_ready_names", "not json at all")
        assert res.returncode != 0
        assert res.stdout.strip() == ""
        assert "jq failed" in res.stderr


# helmreleases_hard_failed


class TestHelmreleasesHardFailed:
    def _hr(self, name, status, reason=None, failures=None):
        cond = {"type": "Ready", "status": status}
        if reason is not None:
            cond["reason"] = reason
        st = {"conditions": [cond]}
        if failures is not None:
            st["failures"] = failures
        return {"metadata": {"name": name}, "status": st}

    def test_terminal_reason_is_hard_failed(self):
        for reason in ("InstallFailed", "UpgradeFailed", "TestFailed", "RollbackFailed"):
            out = _run(
                "helmreleases_hard_failed",
                _items(self._hr("x", "False", reason=reason)),
            ).stdout
            assert "x" in out, reason

    def test_nonzero_failures_is_hard_failed(self):
        # Ready reason not on the allowlist, but the controller has retried
        # (.status.failures > 0) -> still a hard failure.
        out = _run(
            "helmreleases_hard_failed",
            _items(self._hr("x", "False", reason="Reconciling", failures=2)),
        ).stdout
        assert "x" in out

    def test_reconciling_first_pass_not_hard_failed(self):
        # Not-Ready, benign reason, zero failures (a fresh install still
        # reconciling) -> NOT a hard failure (warned, not errored, in bootstrap).
        out = _run(
            "helmreleases_hard_failed",
            _items(self._hr("x", "False", reason="Progressing", failures=0)),
        ).stdout.strip()
        assert out == ""

    def test_ready_hr_never_hard_failed(self):
        out = _run(
            "helmreleases_hard_failed",
            _items(self._hr("x", "True")),
        ).stdout.strip()
        assert out == ""

    def test_mixed(self):
        out = _run(
            "helmreleases_hard_failed",
            _items(
                self._hr("ok", "True"),
                self._hr("reconciling", "False", reason="Progressing", failures=0),
                self._hr("failed", "False", reason="UpgradeFailed"),
            ),
        ).stdout
        assert "failed" in out
        assert "ok" not in out
        assert "reconciling" not in out

    def test_a_jq_failure_is_loud_not_an_empty_list(self):
        res = _run("helmreleases_hard_failed", "not json at all")
        assert res.returncode != 0
        assert res.stdout.strip() == ""
        assert "jq failed" in res.stderr


# gitlab_health_code: internal-first, external only on a connection-level 000
# Shared by deploy-verify.sh and post-maintenance-verify.sh, so a regression here
# would silently change what both scripts call "GitLab healthy".

def _gitlab_code(tmp_path: Path, responses: dict[str, str]) -> tuple[str, list[str]]:
    """Drive gitlab_health_code with a stub `curl` that maps URL -> status code.

    Returns (printed code, the list of URLs the stub was asked for).
    """
    calls = tmp_path / "calls.txt"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    table = "\n".join(f'  {url!r}) echo "{code}" ;;' for url, code in responses.items())
    (stub_dir / "curl").write_text(
        "#!/usr/bin/env bash\n"
        'url="${@: -1}"\n'
        f'echo "$url" >> {calls}\n'
        'case "$url" in\n'
        f"{table}\n"
        "  *) echo 000 ;;\n"
        "esac\n"
    )
    (stub_dir / "curl").chmod(0o755)
    script = (
        f'export PATH="{stub_dir}:$PATH"\n'
        "export GITLAB_HEALTH_INTERNAL=http://internal\n"
        "export GITLAB_HEALTH_EXTERNAL=http://external\n"
        f". {LIB}\ngitlab_health_code /-/readiness\n"
    )
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    seen = calls.read_text().split() if calls.exists() else []
    return res.stdout.strip(), seen


class TestGitlabHealthCode:
    def test_internal_200_short_circuits(self, tmp_path):
        code, seen = _gitlab_code(tmp_path, {"http://internal/-/readiness": "200"})
        assert code == "200"
        assert seen == ["http://internal/-/readiness"]

    def test_real_http_status_is_trusted_not_masked_by_external(self, tmp_path):
        # A 503 means GitLab answered; falling through to an external 200 would
        # hide an internal outage.
        code, seen = _gitlab_code(
            tmp_path,
            {"http://internal/-/readiness": "503", "http://external/-/readiness": "200"},
        )
        assert code == "503"
        assert seen == ["http://internal/-/readiness"]

    def test_connection_level_000_falls_back_to_external(self, tmp_path):
        code, seen = _gitlab_code(
            tmp_path,
            {"http://internal/-/readiness": "000", "http://external/-/readiness": "200"},
        )
        assert code == "200"
        assert seen == ["http://internal/-/readiness", "http://external/-/readiness"]

    def test_both_down_reports_000(self, tmp_path):
        code, _ = _gitlab_code(tmp_path, {})
        assert code == "000"
