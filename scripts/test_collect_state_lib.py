#!/usr/bin/env python3
"""Unit tests for scripts/collect-state-lib.sh.

The library holds collect-state.sh's pure logic: the secret-redaction guard
that scrubs CLUSTER_STATUS.txt (a regression here would silently ship
tokens/passwords into an artifact agents and operators read) and the tri-state
health classifiers behind the regular (OK/PARTIAL/FAILED) and --json
(healthy/degraded/catastrophic) verdicts. Each test sources the library in a
bash subprocess and drives one helper with synthetic input.

Run with pytest:
    pytest scripts/test_collect_state_lib.py -v
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent / "collect-state-lib.sh"


def _run(func_call: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Source the library and run a function call, returning the completed proc."""
    script = f". {LIB}\n{func_call}\n"
    return subprocess.run(
        ["bash", "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
    )


def _redact(text: str, tmp_path: Path) -> str:
    """Run redact_file over `text` and return the redacted output."""
    infile = tmp_path / "in.txt"
    outfile = tmp_path / "out.txt"
    infile.write_text(text)
    res = _run(f"redact_file {infile} {outfile}")
    assert res.returncode == 0, res.stderr
    return outfile.read_text()


# --- redact_file: secrets must come out redacted -----------------------------

class TestRedactSecrets:
    @pytest.mark.parametrize(
        "line,leak",
        [
            ("password: hunter2", "hunter2"),
            ("Password= Sup3rSecret!", "Sup3rSecret"),
            ("token: glpat-abc123def456", "glpat-abc123def456"),
            ("access_token: ya29.a0Af", "ya29.a0Af"),
            ("secret: s3cr3tvalue", "s3cr3tvalue"),
            ("client_secret: oidc-secret-value", "oidc-secret-value"),
            ("api_key: 32aa4d3c9ff04a1", "32aa4d3c9ff04a1"),
            ("apikey=32aa4d3c9ff04a1", "32aa4d3c9ff04a1"),
            ("API_KEY: 32AA4D3C9FF04A1", "32AA4D3C9FF04A1"),
            ("Authorization: Bearer eyFOOBARtoken123", "eyFOOBARtoken123"),
            ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
            ("CF_Token=cf-token-value", "cf-token-value"),
            ("OPENVPN_PASSWORD=vpnpass", "vpnpass"),
            ("WIREGUARD_PRIVATE_KEY=wOOfoo42=", "wOOfoo42"),
            ("WIREGUARD_PRESHARED_KEY=pskvalue42=", "pskvalue42"),
            ("runner token glrt-AbC_123-xyz", "glrt-AbC_123-xyz"),
            ("gh token ghp_" + "a1" * 20, "ghp_" + "a1" * 20),
            ("op sa ops_" + "b2" * 25, "ops_" + "b2" * 25),
            (
                "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-part_here",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-part_here",
            ),
            (
                "hook https://discord.com/api/webhooks/1234567890/secret_hook-token",
                "secret_hook-token",
            ),
        ],
    )
    def test_secret_shapes_redacted(self, tmp_path, line, leak):
        out = _redact(line + "\n", tmp_path)
        assert leak not in out, f"leaked through redaction: {out!r}"

    def test_pem_private_key_block_collapsed(self, tmp_path):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA7the8keymaterial\n"
            "morekeymaterial==\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        out = _redact(pem, tmp_path)
        assert "keymaterial" not in out
        assert "<PRIVATE_KEY_REDACTED>" in out


# --- redact_file: benign text must come out untouched ------------------------

class TestRedactBenign:
    @pytest.mark.parametrize(
        "line",
        [
            "K3s nodes ready: 9/9",
            "Basic configuration options are documented in docs/01",
            "tokens: 3 loaded identities",
            "unit tokenizer.service loaded active",
            "eyeball the output before shipping",
            "the secretary approved the change",
        ],
    )
    def test_benign_lines_untouched(self, tmp_path, line):
        out = _redact(line + "\n", tmp_path)
        assert out == line + "\n"


# --- classifiers -------------------------------------------------------------
# classify_regular <pve_reachable> <k3s_api_ok> <k3s_ready> <k3s_total>
#                  <hosts_ok> <hosts_total> <coverage_pct> <coverage_floor>
#                  <flux_not_ready> <zfs_degraded> <gitlab_ok>
# classify_json    <pve_up> <pve_total> <k3s_api_ok> <k3s_ready> <k3s_total>
#                  <flux_not_ready> <zfs_degraded> <gitlab_ok>

def _regular(pve, api, ready, total, hosts_ok, hosts_total, pct, floor,
             flux, zfs, gitlab) -> str:
    res = _run(
        f"classify_regular {pve} {api} {ready} {total} {hosts_ok} "
        f"{hosts_total} {pct} {floor} {flux} {zfs} {gitlab}"
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def _json(pve_up, pve_total, api, ready, total, flux, zfs, gitlab) -> str:
    res = _run(
        f"classify_json {pve_up} {pve_total} {api} {ready} {total} "
        f"{flux} {zfs} {gitlab}"
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


class TestClassifyRegular:
    def test_all_green_is_ok(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, 1) == "OK"

    def test_flux_not_ready_degrades(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 1, 0, 1) == "PARTIAL"

    def test_zfs_degraded_degrades(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 1, 1) == "PARTIAL"

    def test_gitlab_unhealthy_degrades(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, 0) == "PARTIAL"

    def test_one_host_down_degrades(self):
        assert _regular(5, "true", 9, 9, 18, 19, 94, 50, 0, 0, 1) == "PARTIAL"

    def test_no_pve_host_is_failed(self):
        assert _regular(0, "true", 9, 9, 3, 19, 15, 50, 0, 0, 1) == "FAILED"

    def test_api_ok_zero_ready_is_failed(self):
        # API answered and reported zero Ready nodes: catastrophic.
        assert _regular(6, "true", 0, 9, 19, 19, 100, 50, 0, 0, 1) == "FAILED"

    def test_kubectl_unreachable_is_partial_not_failed(self):
        # Collector-side kubeconfig problem (probe defaults 0/0) must degrade,
        # not read as a catastrophic cluster failure — and never promote to OK.
        assert _regular(6, "false", 0, 0, 19, 19, 100, 50, 0, 0, 1) == "PARTIAL"

    def test_coverage_below_floor_is_failed(self):
        assert _regular(6, "true", 9, 9, 9, 19, 47, 50, 0, 0, 1) == "FAILED"

    def test_probe_failure_defaults_never_promote(self):
        # gitlab probe failure (000 -> gitlab_ok=0) with everything else green
        # yields PARTIAL, never OK.
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, 0) == "PARTIAL"


class TestClassifyJson:
    def test_all_green_is_healthy(self):
        assert _json(6, 6, "true", 9, 9, 0, 0, 1) == "healthy"

    def test_flux_not_ready_degrades(self):
        assert _json(6, 6, "true", 9, 9, 1, 0, 1) == "degraded"

    def test_zfs_degraded_degrades(self):
        assert _json(6, 6, "true", 9, 9, 0, 1, 1) == "degraded"

    def test_gitlab_unhealthy_degrades(self):
        assert _json(6, 6, "true", 9, 9, 0, 0, 0) == "degraded"

    def test_one_host_down_degrades(self):
        assert _json(5, 6, "true", 9, 9, 0, 0, 1) == "degraded"

    def test_no_pve_host_is_catastrophic(self):
        assert _json(0, 6, "true", 9, 9, 0, 0, 1) == "catastrophic"

    def test_api_ok_zero_ready_is_catastrophic(self):
        assert _json(6, 6, "true", 0, 9, 0, 0, 1) == "catastrophic"

    def test_kubectl_unreachable_is_degraded(self):
        # API unreachable (collector-side): degraded, not catastrophic.
        assert _json(6, 6, "false", 0, 0, 0, 0, 1) == "degraded"


class TestClassifierParity:
    """Regular and --json verdicts must agree on shared signals: OK <=> healthy,
    PARTIAL <=> degraded, FAILED <=> catastrophic (with full host coverage, the
    regular-only difference documented in the collect-state.sh header)."""

    PARITY = {"OK": "healthy", "PARTIAL": "degraded", "FAILED": "catastrophic"}

    @pytest.mark.parametrize(
        "pve,api,ready,total,flux,zfs,gitlab",
        [
            (6, "true", 9, 9, 0, 0, 1),   # green
            (6, "true", 9, 9, 1, 0, 1),   # flux stuck
            (6, "true", 9, 9, 0, 2, 1),   # zfs degraded
            (6, "true", 9, 9, 0, 0, 0),   # gitlab down
            (5, "true", 9, 9, 0, 0, 1),   # one pve host down
            (6, "true", 8, 9, 0, 0, 1),   # one k3s node not ready
            (6, "false", 0, 0, 0, 0, 1),  # kubectl unreachable
            (0, "true", 0, 0, 0, 0, 0),   # nothing reachable
            (6, "true", 0, 9, 0, 0, 1),   # api ok, zero nodes ready
        ],
    )
    def test_same_signals_map_to_paired_verdicts(self, pve, api, ready, total,
                                                 flux, zfs, gitlab):
        # Full host coverage so the regular-only coverage gate is neutral;
        # hosts_ok tracks pve reachability for the one-host-down case.
        hosts_total = 19
        hosts_ok = hosts_total if pve == 6 else (0 if pve == 0 else 18)
        pct = hosts_ok * 100 // hosts_total
        reg = _regular(pve, api, ready, total, hosts_ok, hosts_total, pct, 50,
                       flux, zfs, gitlab)
        js = _json(pve, 6, api, ready, total, flux, zfs, gitlab)
        assert self.PARITY[reg] == js, (
            f"verdict mismatch: regular={reg} json={js} for "
            f"pve={pve} api={api} ready={ready}/{total} flux={flux} "
            f"zfs={zfs} gitlab={gitlab}"
        )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
