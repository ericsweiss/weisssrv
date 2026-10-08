"""Unit tests for the shell the Taskfile invokes rather than inlines.

Each script runs in a subprocess with stubs on PATH, so its failure arms are
exercised without a cluster.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)



def _run_on_a_tty(typed: str, *extra_args: str) -> int:
    """Run supervised-apply-guard.sh with a real controlling tty on stdin."""
    import pty

    primary, replica = pty.openpty()
    try:
        proc = subprocess.Popen(
            [
                "bash",
                str(SCRIPTS / "supervised-apply-guard.sh"),
                "terraform:unifi-apply",
                "the UniFi network root",
                *extra_args,
            ],
            stdin=replica,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=SCRIPTS.parent,
        )
        # The replica fd stays open until the child is reaped, or the write below
        # races a refusal that never reads stdin and raises EIO. That refusal is
        # the pass this helper asserts, so the write is allowed to fail.
        try:
            os.write(primary, typed.encode())
        except OSError:
            pass
        proc.communicate(timeout=20)
        return proc.returncode
    finally:
        os.close(primary)
        os.close(replica)


@pytest.fixture()
def bin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    return d


def _run(script: str, *args, bin_dir: Path | None = None, **env) -> subprocess.CompletedProcess:
    full = dict(os.environ, **{k: str(v) for k, v in env.items()})
    if bin_dir is not None:
        full["PATH"] = f"{bin_dir}:{full['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        env=full,
        cwd=SCRIPTS.parent,
    )


class TestWaitForReloaderRoll:
    SCRIPT = "wait-for-reloader-roll.sh"

    def test_returns_clean_once_the_generation_moves(self, bin_dir):
        _stub(bin_dir, "kubectl", "echo 8")
        proc = _run(self.SCRIPT, "downloads", "nzbget", "7", "1", bin_dir=bin_dir,
                    RELOADER_POLL_INTERVAL=0)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_an_unchanged_generation_is_a_failure(self, bin_dir):
        """Without this the caller's `rollout status` reports a premature
        success against the already-complete generation."""
        _stub(bin_dir, "kubectl", "echo 7")
        proc = _run(self.SCRIPT, "downloads", "nzbget", "7", "1", "vpn_enabled",
                    bin_dir=bin_dir, RELOADER_POLL_INTERVAL=0)
        assert proc.returncode == 1
        assert "did not roll deployment/nzbget within 1s" in proc.stdout
        assert "The vpn_enabled patch applied" in proc.stdout

    def test_a_missing_deployment_reads_as_unchanged(self, bin_dir):
        _stub(bin_dir, "kubectl", "exit 1")
        proc = _run(self.SCRIPT, "downloads", "nzbget", "0", "1",
                    bin_dir=bin_dir, RELOADER_POLL_INTERVAL=0)
        assert proc.returncode == 1

    def test_an_unreadable_generation_is_not_a_roll(self, bin_dir):
        """Mutation case: a read failure defaulting to 0 differs from any real
        generation, so the guard would report a roll it never observed."""
        _stub(bin_dir, "kubectl", "exit 1")
        proc = _run(self.SCRIPT, "downloads", "nzbget", "7", "1", "vpn_enabled",
                    bin_dir=bin_dir, RELOADER_POLL_INTERVAL=0)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "did not roll deployment/nzbget within 1s" in proc.stdout

    def test_refuses_incomplete_arguments(self, bin_dir):
        _stub(bin_dir, "kubectl", "echo 8")
        assert _run(self.SCRIPT, "downloads", bin_dir=bin_dir).returncode != 0


class TestDownloadsVpn:
    SCRIPT = "downloads-vpn.sh"

    def test_an_unreadable_generation_refuses_to_patch(self, bin_dir, tmp_path):
        """Mutation case: defaulting the generation to 0 would make the roll
        wait return on its first poll and mask a pod that never restarted."""
        log = tmp_path / "kubectl.log"
        _stub(
            bin_dir,
            "kubectl",
            f'echo "$@" >> {log}\n'
            'case "$*" in\n'
            '  *"get deployment"*) exit 1 ;;\n'
            '  *"vpn_enabled"*) echo true ;;\n'
            '  *) echo "" ;;\n'
            'esac\n',
        )
        proc = _run(self.SCRIPT, "nzbget", "off", bin_dir=bin_dir)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "could not read deployment/nzbget's generation" in proc.stdout
        assert "patch" not in log.read_text()


class TestSupervisedApplyGuard:
    SCRIPT = "supervised-apply-guard.sh"

    def test_refuses_a_non_tty(self):
        proc = _run(self.SCRIPT, "terraform:unifi-apply", "the UniFi network root")
        assert proc.returncode == 2
        assert "supervised operator step" in proc.stderr

    def test_typed_confirmation_gates_the_apply(self):
        """A pty so the tty check passes and `read` is reached."""
        assert _run_on_a_tty("apply\n") == 0
        assert _run_on_a_tty("yes\n") == 1

    @pytest.mark.parametrize(
        "spelling", ["-auto-approve", "-auto-approve=true", "--auto-approve"]
    )
    def test_auto_approve_is_refused_on_a_real_tty(self, spelling):
        assert _run_on_a_tty("apply\n", spelling) == 2, spelling


class TestFluxCorpusGates:
    SCRIPT = "flux-corpus-gates.sh"

    def test_refuses_to_run_without_both_arguments(self):
        assert _run(self.SCRIPT).returncode != 0
        assert _run(self.SCRIPT, "/tmp/corpus").returncode != 0

    def test_a_failing_gate_fails_the_script(self, bin_dir, tmp_path):
        """Mutation case: every gate still runs, and one red makes the run red."""
        corpus = tmp_path / "corpus.yaml"
        # A real object: the corpus floor refuses a file of only `---`.
        corpus.write_text("---\nkind: Namespace\n")
        _stub(bin_dir, "kustomize", "printf '%s\\n' '---' 'kind: Namespace'")
        _stub(bin_dir, "python3", 'echo "gate ran: $*" >&2; exit 1')
        # One argument: the stubbed python3 would also fail flux-env.sh, and an
        # unmergeable ConfigMap is an operator error (rc 2), not the finding
        # this case is about. test_flux_corpus_gates.py covers that arm.
        proc = _run(self.SCRIPT, str(corpus), bin_dir=bin_dir)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        # All six python gates ran despite the first one failing.
        for gate in (
            "check-hpa-vpa-invariant.py",
            "check-scrape-netpol.py",
            "check-default-deny-coverage.py",
            "check-secretstore-scope.py",
            "check-pvc-storageclass.py",
            "check-nfs-tls.py",
        ):
            assert gate in proc.stderr, gate

    def test_a_clean_run_exits_zero(self, bin_dir, tmp_path):
        """The one-argument form: every gate passes and the values validation
        is the documented skip, so the script must exit 0."""
        corpus = tmp_path / "corpus.yaml"
        corpus.write_text("---\nkind: Namespace\n")
        _stub(bin_dir, "kustomize", "printf '%s\\n' '---' 'kind: Namespace'")
        _stub(bin_dir, "python3", "exit 0")
        proc = _run(self.SCRIPT, str(corpus), bin_dir=bin_dir)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "=== Checking HPA/VPA invariant ===" in proc.stdout
        assert "Skipping HelmRelease values validation" in proc.stdout

    def test_the_banner_matches_the_ci_spelling(self):
        body = (SCRIPTS / self.SCRIPT).read_text(encoding="utf-8")
        assert "=== Checking PVC storageClassName ===" in body


class TestArgumentValidation:
    """The refusals that keep a live toggle from taking an app down."""

    @pytest.mark.parametrize(
        "args,needle",
        [
            (("bogus", "on"), "APP must be one of: nzbget qbittorrent"),
            (("nzbget", "maybe"), "STATE must be on or off"),
            (("nzbget", "on", "NOPE"), "unexpected argument"),
        ],
    )
    def test_downloads_vpn_refuses_bad_input(self, args, needle, bin_dir):
        _stub(bin_dir, "kubectl", "exit 0")
        proc = _run("downloads-vpn.sh", *args, bin_dir=bin_dir)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert needle in proc.stdout

    @staticmethod
    def _kubectl_stub(bin_dir: Path, log: Path) -> None:
        """A kubectl that answers the reads downloads-vpn.sh makes and records
        every call, so a bypassed refusal shows up as a patch in the log."""
        _stub(
            bin_dir,
            "kubectl",
            f'''printf '%s\\n' "$*" >> {log}
case "$*" in
  *"jsonpath={{.data.vpn_enabled}}"*) echo "$STUB_VPN_ENABLED" ;;
  *"jsonpath={{.data.vpn_provider}}"*) echo "$STUB_VPN_PROVIDER" ;;
  *"get secret vpn-credentials"*"-o name")
    if [ "$STUB_SECRET" = "missing" ]; then
      echo 'secrets "vpn-credentials" not found' >&2
      exit 1
    fi
    echo secret/vpn-credentials ;;
  *"get secret vpn-credentials"*) echo "" ;;
esac
exit 0''',
        )

    def test_downloads_vpn_refuses_an_unknown_provider(self, bin_dir, tmp_path):
        """vpn-credcheck.sh exits 2 on an unknown provider; reading that as
        "nothing missing" would roll the app into CrashLoopBackOff."""
        log = tmp_path / "calls.log"
        self._kubectl_stub(bin_dir, log)
        proc = _run(
            "downloads-vpn.sh", "nzbget", "on", bin_dir=bin_dir,
            STUB_VPN_ENABLED="false", STUB_VPN_PROVIDER="mullvad", STUB_SECRET="present",
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "credential pre-flight failed (rc=2)" in proc.stdout
        assert "patch" not in log.read_text()

    def test_downloads_vpn_refuses_an_unwired_provider(self, bin_dir, tmp_path):
        log = tmp_path / "calls.log"
        self._kubectl_stub(bin_dir, log)
        proc = _run(
            "downloads-vpn.sh", "nzbget", "on", bin_dir=bin_dir,
            STUB_VPN_ENABLED="false", STUB_VPN_PROVIDER="privado", STUB_SECRET="missing",
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "is not fully wired" in proc.stdout
        assert "privadovpn-user" in proc.stdout
        assert "patch" not in log.read_text()

    def test_downloads_vpn_short_circuits_when_already_in_state(self, bin_dir, tmp_path):
        log = tmp_path / "calls.log"
        self._kubectl_stub(bin_dir, log)
        proc = _run(
            "downloads-vpn.sh", "nzbget", "on", bin_dir=bin_dir,
            STUB_VPN_ENABLED="true", STUB_VPN_PROVIDER="privado", STUB_SECRET="present",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "nothing to do" in proc.stdout
        assert "patch" not in log.read_text()

    @pytest.mark.parametrize(
        "args,needle",
        [
            (("bogus", "privadovpn", ""), "APP must be one of: nzbget qbittorrent"),
            (("nzbget", "notaprovider", ""), "unknown/unwired PROVIDER"),
            (("nzbget", "privadovpn", 'Nether"lands'), "unsupported characters"),
            (("nzbget", "privadovpn", "", "States"), "word-split by the '--' form"),
        ],
    )
    def test_downloads_vpn_provider_refuses_bad_input(self, args, needle, bin_dir):
        _stub(bin_dir, "kubectl", "exit 0")
        proc = _run("downloads-vpn-provider.sh", *args, bin_dir=bin_dir)
        assert proc.returncode == 1
        assert needle in proc.stdout

    def test_flux_rotate_secret_needs_an_app(self):
        proc = _run("flux-rotate-secret.sh")
        assert proc.returncode == 1
        assert "Usage: task flux:rotate-secret" in proc.stdout

    def test_flux_rotate_secret_refuses_an_unknown_app(self):
        proc = _run("flux-rotate-secret.sh", "not-an-app")
        assert proc.returncode == 1
        assert "Unknown app: not-an-app" in proc.stdout

    def test_flux_rotate_secret_knows_every_advertised_app(self):
        """The usage line and the case arms are two lists of the same thing."""
        body = (SCRIPTS / "flux-rotate-secret.sh").read_text(encoding="utf-8")
        advertised = body.split("Known apps: ", 1)[1].split('"', 1)[0]
        for app in (a.strip() for a in advertised.split(",")):
            assert f"    {app})" in body or f"| {app})" in body or f"{app} |" in body, app
