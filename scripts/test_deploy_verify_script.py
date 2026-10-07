#!/usr/bin/env python3
"""Coverage for scripts/deploy-verify.sh itself.

Each helper is extracted by name and driven in a bash subprocess, since the script
needs a live cluster to run whole. test_deploy_verify_lib.py covers the classifiers.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import require_tool

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "deploy-verify.sh"


def extract(name: str) -> str:
    """The text of one shell function, from `name() {` to its closing brace."""
    lines = SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def block(first: str, last: str) -> str:
    """The script text from the line starting with `first` up to `last`."""
    lines = SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(first))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith(last))
    return "\n".join(lines[start:end])


def bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=SCRIPTS.parent, timeout=60)


class TestWaitFor:
    """The retry helper every readiness gate is built on."""

    PRELUDE = extract("wait_for")

    def test_a_command_that_succeeds_returns_immediately(self):
        res = bash(f"{self.PRELUDE}\nwait_for 'thing' 30 5 true; echo rc=$?")
        assert "rc=0" in res.stdout
        assert "timed out" not in res.stdout

    def test_a_command_that_never_succeeds_times_out_and_reports(self):
        # The last attempt's output has to survive: a bare non-zero return here
        # leaves the pipeline with no diagnosis of what was still not ready.
        res = bash(
            f"{self.PRELUDE}\n"
            "probe() { echo 'still pending'; return 1; }\n"
            "wait_for 'thing' 0 1 probe; echo rc=$?"
        )
        assert "rc=1" in res.stdout
        assert "wait_for: thing timed out" in res.stdout
        assert "still pending" in res.stdout


class TestCheckNodesReady:
    """The first gate: an empty node list must never read as "all Ready"."""

    # The real helper, not a stand-in: the gate is the COMPOSITION of the two.
    PRELUDE = f". {SCRIPTS}/deploy-verify-lib.sh\n" + extract("check_nodes_ready")

    def _run(self, node_output: str, tmp_path: Path) -> str:
        stub = tmp_path / "kubectl"
        stub.write_text(f"#!/bin/sh\nprintf '%s' '{node_output}'\n")
        stub.chmod(0o755)
        res = bash(f'PATH="{tmp_path}:$PATH"\n{self.PRELUDE}\n'
                   "check_nodes_ready; echo rc=$?")
        return res.stdout

    def test_all_ready_passes(self, tmp_path):
        assert "rc=0" in self._run("a Ready x\nb Ready x\n", tmp_path)

    def test_one_not_ready_fails(self, tmp_path):
        assert "rc=1" in self._run("a Ready x\nb NotReady x\n", tmp_path)

    def test_an_empty_answer_fails(self, tmp_path):
        assert "rc=1" in self._run("", tmp_path)

    def test_a_cordoned_node_is_not_ready(self, tmp_path):
        assert "rc=1" in self._run("a Ready,SchedulingDisabled x\n", tmp_path)


class TestCheckTopKustomizationsReady:
    """Anything but Ready=True — including a missing object — holds the gate."""

    PRELUDE = extract("ks_ready") + "\n" + extract("check_top_kustomizations_ready")

    def _run(self, ready: str, tmp_path: Path) -> str:
        stub = tmp_path / "kubectl"
        stub.write_text(f"#!/bin/sh\nprintf '%s' '{ready}'\n")
        stub.chmod(0o755)
        res = bash(f'PATH="{tmp_path}:$PATH"\n'
                   'TOP_KUSTOMIZATIONS="apps infrastructure-configs"\n'
                   f"{self.PRELUDE}\ncheck_top_kustomizations_ready; echo rc=$?")
        return res.stdout

    def test_ready_true_passes(self, tmp_path):
        assert "rc=0" in self._run("True", tmp_path)

    def test_ready_false_fails(self, tmp_path):
        assert "rc=1" in self._run("False", tmp_path)

    def test_an_absent_status_fails(self, tmp_path):
        assert "rc=1" in self._run("", tmp_path)


class TestReportPreReconcileState:
    """999 is a sentinel, not a count: a failed snapshot must not relax the
    steady-state ExternalSecret check to a warning."""

    PRELUDE = extract("report_pre_reconcile_state")

    def _run(self, not_ready: str, steady: str) -> subprocess.CompletedProcess:
        return bash(f"{self.PRELUDE}\n"
                    f"report_pre_reconcile_state '{not_ready}' '{steady}'; echo rc=$?")

    def test_steady_state_passes(self):
        res = self._run("0", "true")
        assert "rc=0" in res.stdout
        assert "steady-state" in res.stdout

    def test_an_unavailable_snapshot_is_an_error_not_bootstrap(self):
        res = self._run("999", "false")
        assert "rc=1" in res.stdout
        assert "could not be classified" in res.stdout

    def test_a_real_not_ready_count_is_bootstrap(self):
        res = self._run("3", "false")
        assert "rc=0" in res.stdout
        assert "bootstrap/recovery (3 Kustomization(s)" in res.stdout


class TestSnapshotTakesTheStrictArm:
    """An unclassifiable pre-reconcile snapshot must leave the ExternalSecret
    check STRICT: 999 read as a count would relax every later check."""

    LIB = SCRIPTS / "deploy-verify-lib.sh"

    @pytest.fixture(autouse=True)
    def _jq(self):
        require_tool("jq", "deploy-verify snapshot classifier",
                     "Install jq in the job image.")

    def _run(self, payload: str, rc: int, tmp_path: Path) -> str:
        stub = tmp_path / "kubectl"
        stub.write_text(f"#!/bin/sh\nprintf '%s' {shlex.quote(payload)}\nexit {rc}\n")
        stub.chmod(0o755)
        snapshot = block("KS_ERR=$(mktemp)", 'echo ""')
        res = bash(f'PATH="{tmp_path}:$PATH"\n'
                   f". {self.LIB}\nEXIT=0\n{snapshot}\n"
                   'echo "STEADY=$STEADY_STATE EXIT=$EXIT COUNT=$PRE_KS_NOT_READY"')
        assert res.returncode == 0, res.stderr
        return res.stdout

    def test_unparseable_json_is_strict_and_fails(self, tmp_path):
        out = self._run("not json at all", 0, tmp_path)
        assert "could not be classified" in out
        assert "STEADY=true EXIT=1 COUNT=0" in out

    def test_a_failed_query_is_strict_and_fails(self, tmp_path):
        out = self._run("", 1, tmp_path)
        assert "could not list Kustomizations pre-reconcile" in out
        assert "STEADY=true EXIT=1 COUNT=0" in out

    def test_a_real_count_still_selects_bootstrap(self, tmp_path):
        items = '{"items":[{"status":{"conditions":[{"type":"Ready","status":"False"}]}}]}'
        out = self._run(items, 0, tmp_path)
        assert "bootstrap/recovery (1 Kustomization(s)" in out
        assert "STEADY=false EXIT=0 COUNT=1" in out


class TestRequireEnvsubstVars:
    """An empty FLUX_ENVSUBST_VARS would skip every server-side dry-run, which
    must exit non-zero rather than print a clean verification."""

    PRELUDE = extract("require_envsubst_vars")

    def _run(self, value: str) -> subprocess.CompletedProcess:
        return bash(f"FLUX_ENVSUBST_VARS='{value}'\n{self.PRELUDE}\n"
                    "require_envsubst_vars; echo rc=$?")

    def test_an_empty_allowlist_fails(self):
        res = self._run("")
        assert "rc=1" in res.stdout
        assert "empty FLUX_ENVSUBST_VARS" in res.stderr

    def test_a_populated_allowlist_passes(self):
        assert "rc=0" in self._run("${cluster_internal_domain}").stdout

    def test_the_caller_aborts_on_an_empty_allowlist(self):
        """The guard is only useful if the main flow exits on it."""
        assert "require_envsubst_vars || exit 1" in SCRIPT.read_text()


class TestRequireClusterTools:
    """A missing inspection tool must abort, not report an unreachable cluster
    clean."""

    PRELUDE = extract("require_cluster_tools")

    def _run(self, path: str) -> subprocess.CompletedProcess:
        return bash(f'PATH="{path}"\n{self.PRELUDE}\nrequire_cluster_tools; echo rc=$?')

    def test_a_missing_tool_fails_and_names_it(self, tmp_path):
        res = self._run(str(tmp_path))
        assert "rc=1" in res.stdout
        assert "kubectl" in res.stderr

    def test_all_tools_present_passes(self, tmp_path):
        for tool in ("kubectl", "jq", "flux"):
            stub = tmp_path / tool
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        assert "rc=0" in self._run(str(tmp_path)).stdout

    def test_the_caller_aborts_on_a_missing_tool(self):
        """Exit 2: a missing tool is an operator error, not a cluster finding."""
        assert "require_cluster_tools || exit 2" in SCRIPT.read_text()


class TestApiReachabilityPreflight:
    """Every cluster read below is captured, so an unreachable API reads clean."""

    def _drive(self, tmp_path: Path, kubectl_rc: int) -> subprocess.CompletedProcess:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for tool in ("kubectl", "jq", "flux"):
            stub = bin_dir / tool
            rc = kubectl_rc if tool == "kubectl" else 0
            stub.write_text(f"#!/bin/sh\nexit {rc}\n")
            stub.chmod(0o755)
        body = block("require_cluster_tools() {", "# Tools for server-side dry-run")
        return bash(f'PATH={shlex.quote(str(bin_dir))}\n{body}')

    def test_a_reachable_apiserver_passes(self, tmp_path):
        assert self._drive(tmp_path, 0).returncode == 0

    def test_an_unreachable_apiserver_exits_2(self, tmp_path):
        """Mutation case: without this arm an unauthorized kubectl reads clean."""
        result = self._drive(tmp_path, 1)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "cannot reach the cluster" in result.stderr


class TestDryRunPathFloor:
    """The dry-run is the only pre-apply validation, so zero paths is a failure."""

    def _run(self, ks_paths: str) -> subprocess.CompletedProcess:
        body = block("DRYRUN_PATHS=", "while IFS=")
        return bash(f'KS_PATHS={shlex.quote(ks_paths)}\nEXIT=0\n{body}\necho "EXIT=$EXIT"')

    def test_derived_paths_pass(self):
        res = self._run("sources\tkubernetes/infrastructure/sources\n")
        assert "EXIT=0" in res.stdout, res.stdout + res.stderr

    def test_no_paths_is_a_failure(self):
        """Mutation case: an empty capture would otherwise dry-run nothing."""
        res = self._run("")
        assert "nothing was dry-run" in res.stdout
        assert "EXIT=1" in res.stdout


class TestCheckObsPodsHealthy:
    """A refused lookup must hold the gate, not pass as an empty namespace."""

    PRELUDE = f". {SCRIPTS}/deploy-verify-lib.sh\n" + extract("check_obs_pods_healthy")

    def _run(self, body: str, tmp_path: Path) -> str:
        stub = tmp_path / "kubectl"
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
        res = bash(f'PATH="{tmp_path}:$PATH"\n{self.PRELUDE}\n'
                   "check_obs_pods_healthy; echo rc=$?")
        return res.stdout

    def test_a_failed_lookup_fails(self, tmp_path):
        assert "rc=1" in self._run("echo forbidden >&2; exit 1", tmp_path)

    def test_an_empty_namespace_fails(self, tmp_path):
        assert "rc=1" in self._run("exit 0", tmp_path)

    def test_all_running_and_ready_passes(self, tmp_path):
        assert "rc=0" in self._run(
            "printf '%s\\n' 'loki-0 1/1 Running 0 1d'", tmp_path)

    def test_a_crashlooping_pod_fails(self, tmp_path):
        assert "rc=1" in self._run(
            "printf '%s\\n' 'loki-0 0/1 CrashLoopBackOff 7 1d'", tmp_path)


class TestIngressVipAssertion:
    """A LoadBalancer Service listing proves nothing: a VIP nobody announces is
    total ingress loss, so both VIPs are asserted assigned AND announceable."""

    BLOCK = block("if VIP_CONF=$(", 'echo "Checking GitLab health')

    @pytest.fixture(autouse=True)
    def _jq_present(self) -> None:
        # Fail rather than skip: without jq the extracted block reports
        # "announced by nobody" and the failure reads as a real outage.
        assert shutil.which("jq"), (
            "jq is not installed, so the ingress-VIP assertion is unverified. "
            "Install jq in the job that runs this suite."
        )

    KUBECTL_STUB = (
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"get nodes"*) printf "%s" "$STUB_INGRESS_NODES" ;;\n'
        '  *"traefik traefik -o"*) printf "%s" "$STUB_PUBLIC_IP" ;;\n'
        '  *"traefik traefik-internal -o"*) printf "%s" "$STUB_INTERNAL_IP" ;;\n'
        "  *endpointslices*) printf '%s' \"$STUB_SLICES\" ;;\n"
        "esac\n"
    )

    def _drive(self, tmp_path: Path, *, public_ip: str = "10.0.10.100",
               internal_ip: str = "10.0.10.101", endpoint_node: str = "node-a",
               ingress_nodes: str = "node-a node-b",
               conditions: str | None = '{"ready":true}',
               config_rc: int = 0,
               steady: str = "true") -> subprocess.CompletedProcess:
        config = tmp_path / "cluster-config-value.sh"
        if config_rc:
            config.write_text(
                f"#!/bin/sh\necho 'ERROR: key absent' >&2\nexit {config_rc}\n")
        else:
            config.write_text("#!/bin/sh\necho '10.0.10.100 10.0.10.101 esweiss.com'\n")
        config.chmod(0o755)
        kubectl = tmp_path / "kubectl"
        kubectl.write_text(self.KUBECTL_STUB)
        kubectl.chmod(0o755)
        # An absent `conditions` key is a different jq path from an empty one:
        # EndpointSlice leaves it out when the endpoint is ready.
        endpoint = '{"nodeName":"%s"}' % endpoint_node if conditions is None else (
            '{"nodeName":"%s","conditions":%s}' % (endpoint_node, conditions))
        slices = '{"items":[{"endpoints":[%s]}]}' % endpoint
        script = "\n".join([
            "set -uo pipefail",
            f'PATH={shlex.quote(str(tmp_path))}:$PATH',
            f'_SCRIPT_DIR={shlex.quote(str(tmp_path))}',
            f"STEADY_STATE={steady}",
            f"STUB_PUBLIC_IP={shlex.quote(public_ip)}",
            f"STUB_INTERNAL_IP={shlex.quote(internal_ip)}",
            f"STUB_INGRESS_NODES={shlex.quote(ingress_nodes)}",
            f"STUB_SLICES={shlex.quote(slices)}",
            "export STUB_PUBLIC_IP STUB_INTERNAL_IP STUB_INGRESS_NODES STUB_SLICES",
            "EXIT=0",
            self.BLOCK,
            'echo "EXIT=$EXIT"',
        ])
        return bash(script)

    def test_both_vips_assigned_and_announceable_passes(self, tmp_path):
        result = self._drive(tmp_path)
        assert "EXIT=0" in result.stdout, result.stdout + result.stderr
        assert "10.0.10.100 announced from node-a" in result.stdout

    def test_a_pending_vip_fails(self, tmp_path):
        result = self._drive(tmp_path, public_ip="")
        assert "EXIT=1" in result.stdout, result.stdout + result.stderr
        assert "holds '<pending>'" in result.stdout

    def test_a_ready_endpoint_off_the_ingress_nodes_fails(self, tmp_path):
        """The mutation the gate exists for: both VIPs hold their address while
        every Traefik replica sits where no speaker runs."""
        result = self._drive(tmp_path, endpoint_node="node-z")
        assert "EXIT=1" in result.stdout, result.stdout + result.stderr
        assert "announced by nobody" in result.stdout

    def test_a_bootstrap_cluster_only_warns(self, tmp_path):
        result = self._drive(tmp_path, public_ip="", steady="false")
        assert "EXIT=0" in result.stdout, result.stdout + result.stderr
        assert "WARNING: Service traefik/traefik" in result.stdout

    def test_a_not_ready_endpoint_is_not_an_announcer(self, tmp_path):
        """Mutation case: dropping the ready filter accepts a terminating
        replica as the announcer and passes a verify during an ingress outage."""
        result = self._drive(tmp_path, conditions='{"ready":false}')
        assert "EXIT=1" in result.stdout, result.stdout + result.stderr
        assert "announced by nobody" in result.stdout

    def test_an_endpoint_with_no_ready_condition_counts_as_ready(self, tmp_path):
        """Mutation case: `ready == true` would call a healthy cluster down,
        since a ready endpoint may omit the optional field."""
        result = self._drive(tmp_path, conditions=None)
        assert "EXIT=0" in result.stdout, result.stdout + result.stderr
        assert "10.0.10.100 announced from node-a" in result.stdout

    def test_an_unreadable_cluster_config_fails_without_aborting(self, tmp_path):
        """Mutation case: unwrapped, the absent key kills the script under
        set -e and the Verification Complete summary never prints."""
        result = self._drive(tmp_path, config_rc=1)
        assert "EXIT=1" in result.stdout, result.stdout + result.stderr
        assert "the VIP-announce gate did not run" in result.stdout


def test_a_failed_observability_lookup_is_not_an_empty_namespace():
    """`|| true` / `|| echo '{"items":[]}'` here would report "nothing here yet,
    bootstrap" for a namespace the verifier was simply refused."""
    body = SCRIPT.read_text()
    assert "|| OBS_RC=$?" in body
    assert "|| OBS_HR_RC=$?" in body
    assert "--no-headers 2>/dev/null || true)" not in body
    assert """|| echo '{"items":[]}')""" not in body


def test_the_informational_listings_cannot_abort_the_run():
    """Under `set -e` an unguarded listing ends the script before every check
    that matters, which is exactly the bootstrap/recovery run that needs them."""
    body = SCRIPT.read_text()
    for line in ("flux check || EXIT=1",
                 "flux get kustomizations -A || true",
                 "flux get helmreleases -A || true",
                 'kubectl get externalsecrets -A || echo "(no ExternalSecret CRD yet)"'):
        assert f"\n{line}\n" in body, f"lost its guard: {line}"


def test_the_gated_stage_list_is_derived_not_hand_written():
    """A hand-listed stage set is the drift flux-child-kustomizations.py exists
    to prevent."""
    assert "flux-child-kustomizations.py" in SCRIPT.read_text()


def test_the_version_pins_are_asserted_before_anything_runs():
    """Run outside its CI job the script must abort, not download an unpinned
    kustomize."""
    body = SCRIPT.read_text()
    for var in ("KUSTOMIZE_VERSION", "KUSTOMIZE_SHA256", "PYYAML_VERSION"):
        assert f'{var}="${{{var}:?' in body, f"{var} lost its fail-loud binding"
    assert "sha256sum -c -" in body


def test_the_snapshot_query_is_split_from_the_count():
    """A failed `kubectl get` must reach the 999 sentinel, not a count of zero."""
    assert 'if PRE_KS_JSON=$(kubectl get kustomizations' in SCRIPT.read_text()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
