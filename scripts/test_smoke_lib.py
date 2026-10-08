"""Tests that smoke-lib.sh classifies endpoint probes correctly, driven in bash with stub curl and nc."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import source_and_run

SCRIPTS = Path(__file__).resolve().parent
LIB = SCRIPTS / "smoke-lib.sh"
VERIFY_SCRIPTS = (
    "verify-gitlab.sh",
    "verify-nextcloud.sh",
    "verify-immich.sh",
    "verify-immich-ml.sh",
    "verify-windows.sh",
)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(script: str, bin_dir: Path | None = None, **env) -> subprocess.CompletedProcess:
    full_env = dict(os.environ, **{k: str(v) for k, v in env.items()})
    if bin_dir is not None:
        full_env["PATH"] = f"{bin_dir}:{full_env['PATH']}"
    return source_and_run(LIB, script, env=full_env)


@pytest.fixture()
def curl_stub(tmp_path: Path):
    """A `curl` whose HEAD response line is whatever STUB_STATUS says."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def make(status: str | None) -> Path:
        if status is None:
            _stub(bin_dir, "curl", "exit 7")
        else:
            _stub(bin_dir, "curl", f'echo "HTTP/2 {status} "')
        return bin_dir

    return make


class TestHttpStatus:
    def test_reports_the_code(self, curl_stub):
        out = _run('http_status https://example.invalid', curl_stub("200")).stdout.strip()
        assert out == "200"

    def test_unreachable_endpoint_is_000(self, curl_stub):
        out = _run('http_status https://example.invalid', curl_stub(None)).stdout.strip()
        assert out == "000"

    def test_never_aborts_an_errexit_caller(self, curl_stub):
        """The bug this replaced: a bare pipeline killed the task mid-run."""
        proc = _run(
            'set -e\ns=$(http_status https://example.invalid)\necho "after=$s"',
            curl_stub(None),
        )
        assert proc.returncode == 0, proc.stderr
        assert "after=000" in proc.stdout


class TestPredicates:
    @pytest.mark.parametrize("status,expected", [("200", 0), ("302", 0), ("404", 1), ("500", 1)])
    def test_check_http_ok(self, curl_stub, status, expected):
        assert _run("check_http_ok https://x", curl_stub(status)).returncode == expected

    @pytest.mark.parametrize("status,expected", [("401", 0), ("200", 0), ("500", 1)])
    def test_check_registry_ok(self, curl_stub, status, expected):
        assert _run("check_registry_ok https://x", curl_stub(status)).returncode == expected

    @pytest.mark.parametrize("status,expected", [("404", 0), ("200", 0), ("502", 1)])
    def test_check_http_below_500(self, curl_stub, status, expected):
        assert _run("check_http_below_500 https://x", curl_stub(status)).returncode == expected

    def test_check_http_below_500_rejects_no_answer(self, curl_stub):
        """000 is below 500 numerically — it must still not pass."""
        assert _run("check_http_below_500 https://x", curl_stub(None)).returncode == 1

    def test_check_tcp_port(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(bin_dir, "nc", 'exit 0')
        assert _run("check_tcp_port 10.0.0.1 22", bin_dir).returncode == 0
        _stub(bin_dir, "nc", 'exit 1')
        assert _run("check_tcp_port 10.0.0.1 22", bin_dir).returncode == 1


class TestNeedlesAreLiteral:
    """A regex metacharacter in a needle must not match a near miss: a probe
    that passes on the wrong output is worse than no probe."""

    @pytest.fixture()
    def ssh_stub(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        def make(output: str) -> Path:
            _stub(bin_dir, "ssh", f"printf '%s\\n' {output!r}")
            return bin_dir

        return make

    def test_a_metacharacter_needle_does_not_match_a_near_miss(self, ssh_stub):
        # As a regex, the dots in `1.2.3` match the X's in `1X2X3`.
        res = _run("smoke_ssh_contains host cmd 'immich 1.2.3'; echo rc=$?",
                   ssh_stub("immich 1X2X3"))
        assert "rc=1" in res.stdout

    def test_the_literal_needle_still_matches_itself(self, ssh_stub):
        res = _run("smoke_ssh_contains host cmd 'immich 1.2.3'; echo rc=$?",
                   ssh_stub("immich 1.2.3"))
        assert "rc=0" in res.stdout

    def test_an_anchored_needle_needs_the_regex_variant(self, ssh_stub):
        bin_dir = ssh_stub("nextcloud_up 1")
        assert "rc=0" in _run("smoke_ssh_matches host cmd '^nextcloud_up'; echo rc=$?",
                              bin_dir).stdout
        assert "rc=1" in _run("smoke_ssh_contains host cmd '^nextcloud_up'; echo rc=$?",
                              bin_dir).stdout

    def test_url_needles_are_literal_too(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(bin_dir, "curl", """printf '%s\\n' 'gitlab 1X2X3'""")
        res = _run("smoke_url_contains https://x.invalid 'gitlab 1.2.3'; echo rc=$?",
                   bin_dir)
        assert "rc=1" in res.stdout


class TestAccounting:
    def test_summary_counts_and_exits_clean(self):
        proc = _run("smoke_check ok true\nsmoke_check also-ok true\nsmoke_summary")
        assert proc.returncode == 0
        assert "ok... PASS" in proc.stdout
        assert "=== Results: 2 passed, 0 failed ===" in proc.stdout

    def test_a_failure_is_counted_and_exits_non_zero(self):
        proc = _run("smoke_check good true\nsmoke_check bad false\nsmoke_summary")
        assert proc.returncode == 1
        assert "bad... FAIL" in proc.stdout
        assert "=== Results: 1 passed, 1 failed ===" in proc.stdout

    def test_every_probe_runs_even_after_a_failure(self):
        """The crash this replaced skipped 5 of 8 probes and the summary."""
        proc = _run("smoke_check first false\nsmoke_check second true\nsmoke_summary")
        assert "first... FAIL" in proc.stdout
        assert "second... PASS" in proc.stdout
        assert "=== Results: 1 passed, 1 failed ===" in proc.stdout

    def test_optional_probe_neither_passes_nor_fails(self):
        proc = _run("smoke_check real true\nsmoke_optional maybe 'not configured' false\nsmoke_summary")
        assert proc.returncode == 0
        assert "maybe... SKIP (not configured)" in proc.stdout
        assert "=== Results: 1 passed, 0 failed ===" in proc.stdout


class TestVerifyScripts:
    @pytest.mark.parametrize("name", VERIFY_SCRIPTS)
    def test_refuses_to_run_without_its_host_variable(self, name, monkeypatch):
        """hosts.env is loaded by the task; a bare run must fail loudly, not
        probe an empty hostname and report PASS."""
        env = {k: v for k, v in os.environ.items() if not k.endswith("_IP")}
        proc = subprocess.run(
            ["bash", str(SCRIPTS / name)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode != 0
        assert "_IP is not set" in proc.stderr

    @pytest.mark.parametrize("name", VERIFY_SCRIPTS)
    def test_is_executable_and_sources_the_library(self, name):
        path = SCRIPTS / name
        assert os.access(path, os.X_OK), f"{name} is not executable"
        assert "smoke-lib.sh" in path.read_text(encoding="utf-8")

    # Probe count per script: the summary line pins it, so a probe that is
    # dropped or silently always-passes shows up as a changed count.
    SSH_PROBED = (
        ("verify-nextcloud.sh", "NEXTCLOUD_IP", 4),
        ("verify-immich.sh", "IMMICH_IP", 6),
        ("verify-immich-ml.sh", "IMMICH_ML_IP", 3),
    )

    def _ssh_probed(
        self, tmp_path: Path, name: str, var: str, ok: bool
    ) -> subprocess.CompletedProcess:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        if ok:
            # The metrics arm emits more than a pipe buffer: a probe that pipes
            # into `grep -q` SIGPIPEs its writer under pipefail and reds here.
            _stub(
                bin_dir,
                "ssh",
                'case "$*" in\n'
                "  *'php occ status'*) echo '{\"installed\":true}' ;;\n"
                "  *9205/metrics*) echo 'nextcloud_up 1' ;;\n"
                "  *8081/metrics*) seq 1 20000 | sed 's/^/immich_metric_/;s/$/ 1/' ;;\n"
                "  *pg_isready*) echo 'accepting connections' ;;\n"
                "  *'ps --status running'*) echo running ;;\n"
                "esac",
            )
            _stub(bin_dir, "curl", 'case "$*" in *-sI*) echo "HTTP/2 200 " ;; *) echo pong ;; esac')
        else:
            _stub(bin_dir, "ssh", "exit 1")
            _stub(bin_dir, "curl", "exit 1")
        return subprocess.run(
            ["bash", str(SCRIPTS / name)],
            capture_output=True,
            text=True,
            env=dict(os.environ, **{var: "10.0.10.99"}, PATH=f"{bin_dir}:{os.environ['PATH']}"),
        )

    @pytest.mark.parametrize("name,var,probes", SSH_PROBED)
    def test_every_probe_fails_when_nothing_answers(self, tmp_path, name, var, probes):
        proc = self._ssh_probed(tmp_path, name, var, ok=False)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "... FAIL" in proc.stdout
        assert f"=== Results: 0 passed, {probes} failed ===" in proc.stdout

    @pytest.mark.parametrize("name,var,probes", SSH_PROBED)
    def test_every_probe_passes_when_the_estate_answers(self, tmp_path, name, var, probes):
        proc = self._ssh_probed(tmp_path, name, var, ok=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert f"=== Results: {probes} passed, 0 failed ===" in proc.stdout

    def test_windows_verify_fails_when_rdp_is_closed(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(bin_dir, "nc", "exit 1")
        proc = subprocess.run(
            ["bash", str(SCRIPTS / "verify-windows.sh")],
            capture_output=True,
            text=True,
            env=dict(os.environ, WINDOWS_IP="10.0.10.155", PATH=f"{bin_dir}:{os.environ['PATH']}"),
        )
        assert proc.returncode == 1
        assert "RDP 3389 (10.0.10.155)... FAIL" in proc.stdout
        assert "=== Results: 0 passed, 1 failed ===" in proc.stdout

    def test_windows_verify_passes_when_rdp_answers(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(bin_dir, "nc", "exit 0")
        proc = subprocess.run(
            ["bash", str(SCRIPTS / "verify-windows.sh")],
            capture_output=True,
            text=True,
            env=dict(os.environ, WINDOWS_IP="10.0.10.155", PATH=f"{bin_dir}:{os.environ['PATH']}"),
        )
        assert proc.returncode == 0
        assert "=== Results: 1 passed, 0 failed ===" in proc.stdout

    def _gitlab_http_only(self, tmp_path: Path) -> subprocess.CompletedProcess:
        """--http-only with no working nc and no GITLAB_IP: a netcat-less caller."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(
            bin_dir,
            "curl",
            'case "$*" in *-sI*) echo "HTTP/2 200 " ;; *) echo \'{"status":"ok"}\' ;; esac',
        )
        _stub(bin_dir, "nc", "exit 127")
        clean = {k: v for k, v in os.environ.items() if not k.endswith("_IP")}
        return subprocess.run(
            ["bash", str(SCRIPTS / "verify-gitlab.sh"), "--http-only"],
            capture_output=True,
            text=True,
            env=dict(clean, PATH=f"{bin_dir}:{clean['PATH']}"),
        )

    def test_gitlab_http_only_needs_neither_nc_nor_the_host_variable(self, tmp_path):
        proc = self._gitlab_http_only(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Git SSH port" not in proc.stdout
        assert "GITLAB_IP is not set" not in proc.stderr

    def test_gitlab_http_only_still_runs_the_http_probes(self, tmp_path):
        proc = self._gitlab_http_only(tmp_path)
        assert "GitLab Web UI (git.esweiss.com)... PASS" in proc.stdout
        assert "=== Results: 6 passed, 0 failed ===" in proc.stdout

    def test_gitlab_rejects_an_unknown_flag(self, tmp_path):
        """A typo must not read as the default run against a live estate."""
        proc = subprocess.run(
            ["bash", str(SCRIPTS / "verify-gitlab.sh"), "--http"],
            capture_output=True,
            text=True,
            env=dict(os.environ, GITLAB_IP="10.0.10.153"),
        )
        assert proc.returncode == 2
        assert "usage:" in proc.stderr
