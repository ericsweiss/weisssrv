"""Failure-path tests for the four locally-maintained CI gates.

Each of these runs in the pipeline and each reports success by printing a
sentence. A gate that stops SEEING its subject — a parser that resolves nothing,
a job-name convention that changed, a directory that moved — keeps printing that
sentence, and a real regression ships behind a green pipeline. The happy path is
already exercised by the pipeline itself on every MR; what is missing is proof
that the gate can still FAIL.

So each case builds a minimal fixture carrying the exact defect the gate exists
to catch and asserts a non-zero exit, plus a companion case proving the clean
fixture passes (otherwise a gate that fails on everything would look healthy
here).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _run(argv: list[str], cwd: Path | None = None, env: dict | None = None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        argv, capture_output=True, text=True, cwd=str(cwd or REPO), env=full_env
    )


# check-collection-pin-trigger.py
# A deploy job that runs a playbook but does not trigger on
# ansible/requirements.yml keeps deploying the pre-bump roles forever, and
# nothing else in the pipeline can see it.

PIN = "ansible/requirements.yml"


def _ci_with_deploy_job(changes: list[str]) -> str:
    return yaml.safe_dump(
        {
            "deploy-ansible-base": {
                "stage": "deploy",
                "script": ["ansible-playbook ansible/playbooks/base.yml"],
                "rules": [{"changes": changes}],
            }
        }
    )


class TestCollectionPinTrigger:
    GATE = SCRIPTS / "check-collection-pin-trigger.py"

    def test_clean_fixture_passes(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci_with_deploy_job(["ansible/playbooks/base.yml", PIN]))
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 0, run.stdout + run.stderr

    def test_playbook_job_without_the_pin_fails(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci_with_deploy_job(["ansible/playbooks/base.yml"]))
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 1, "a playbook job missing the collection pin must fail"
        assert "deploy-ansible-base" in run.stdout

    def test_the_dict_form_of_changes_is_also_read(self, tmp_path):
        """`changes: {paths: [...]}` is valid GitLab and must not read as empty
        — an unparsed rule set turns the gate into a no-op."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            yaml.safe_dump(
                {
                    "deploy-ansible-base": {
                        "stage": "deploy",
                        "rules": [{"changes": {"paths": ["ansible/playbooks/base.yml"]}}],
                    }
                }
            )
        )
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 1, run.stdout + run.stderr

    def test_a_job_inheriting_its_stage_is_still_inspected(self, tmp_path):
        """`extends:` a base job for `stage: deploy` is the repo's own idiom;
        keying on a literal stage would silently skip such a job."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            yaml.safe_dump(
                {
                    ".deploy-base": {"stage": "deploy"},
                    "deploy-ansible-certs": {
                        "extends": ".deploy-base",
                        "rules": [{"changes": ["ansible/playbooks/certs.yml"]}],
                    },
                }
            )
        )
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 1, (
            "a deploy- job that inherits its stage must still be inspected:\n"
            + run.stdout
            + run.stderr
        )

    def test_rules_inherited_through_extends_are_resolved(self, tmp_path):
        """A job whose `rules:` live entirely in an `extends:` parent still
        deploys on those paths; leaving them unresolved lets it slip the pin
        check. Last parent wins, matching GitLab precedence."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            yaml.safe_dump(
                {
                    ".rules-pinless": {
                        "rules": [{"changes": ["ansible/playbooks/base.yml"]}],
                    },
                    ".rules-clean": {
                        "rules": [{"changes": ["ansible/playbooks/base.yml", PIN]}],
                    },
                    "deploy-ansible-inherited": {
                        "stage": "deploy",
                        "extends": [".rules-clean", ".rules-pinless"],
                    },
                }
            )
        )
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 1, (
            "the LAST parent's pinless rules are the effective ones:\n"
            + run.stdout
            + run.stderr
        )
        assert "deploy-ansible-inherited" in run.stdout

    def test_the_gate_reports_when_it_inspected_nothing(self, tmp_path):
        """Zero jobs inspected is an operator error, not a pass — otherwise a
        renamed job convention retires the gate invisibly."""
        ci = tmp_path / "ci.yml"
        ci.write_text(yaml.safe_dump({"lint": {"stage": "lint", "script": ["true"]}}))
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 2, (
            "a pipeline with no deploy job inspected must not report success:\n"
            + run.stdout
            + run.stderr
        )

    def test_a_referenced_changes_list_is_resolved(self, tmp_path):
        """`changes: !reference [.paths-x, changes]` is this repo's own idiom;
        a loader that maps the tag to None sees no paths and passes."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            ".paths-ansible:\n"
            "  changes:\n"
            "    - ansible/playbooks/base.yml\n"
            "deploy-ansible-base:\n"
            "  stage: deploy\n"
            "  rules:\n"
            "    - changes: !reference [.paths-ansible, changes]\n"
        )
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 1, (
            "a referenced changes list must still be inspected:\n" + run.stdout + run.stderr
        )

    def test_a_referenced_changes_list_including_the_pin_passes(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(
            ".paths-ansible:\n"
            "  changes:\n"
            "    - ansible/playbooks/base.yml\n"
            f"    - {PIN}\n"
            "deploy-ansible-base:\n"
            "  stage: deploy\n"
            "  rules:\n"
            "    - changes: !reference [.paths-ansible, changes]\n"
        )
        run = _run([sys.executable, str(self.GATE), str(ci)])
        assert run.returncode == 0, run.stdout + run.stderr


# check-ci-pin-parity.sh
# `include:` resolves before `variables:` exists, so each pin literal is copied
# next to every include that needs it. The gate keeps the copies equal.

class TestCiPinParity:
    GATE = SCRIPTS / "check-ci-pin-parity.sh"

    CLEAN = (
        'variables:\n'
        '  KUSTOMIZE_VERSION: "5.4.3"\n'
        '  KUSTOMIZE_SHA256: "deadbeef"\n'
        '  PYYAML_VERSION: "6.0.2"\n'
        '  PYTEST_VERSION: "8.3.4"\n'
        'include:\n'
        '  - component: x\n'
        '    inputs:\n'
        '      kustomize_version: "5.4.3"\n'
        '      kustomize_sha256: "deadbeef"\n'
        '      pyyaml_version: "6.0.2"\n'
        '      pytest_version: "8.3.4"\n'
    )

    def test_clean_fixture_passes(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(self.CLEAN)
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 0, run.stdout + run.stderr

    def test_a_drifted_input_fails(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(self.CLEAN.replace('kustomize_version: "5.4.3"', 'kustomize_version: "5.5.0"'))
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 1, "a drifted include input must fail"
        assert "DRIFT" in run.stdout

    def test_one_drifted_copy_among_several_fails(self, tmp_path):
        """The values are `sort -u`'d, so N copies compare equal only when ALL
        of them match — the case a single-copy check would miss."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            self.CLEAN
            + '  - component: y\n    inputs:\n      pyyaml_version: "6.0.1"\n'
        )
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 1, run.stdout + run.stderr

    def test_a_missing_side_fails_rather_than_passing_vacuously(self, tmp_path):
        """An include that stops passing a pin (or a variables: rename) leaves
        one side empty; equality on two empties would be a false pass."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            'variables:\n'
            '  KUSTOMIZE_VERSION: "5.4.3"\n'
            '  KUSTOMIZE_SHA256: "deadbeef"\n'
            '  PYYAML_VERSION: "6.0.2"\n'
            '  PYTEST_VERSION: "8.3.4"\n'
        )
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 1, run.stdout + run.stderr
        assert "no longer sees it" in run.stdout, (
            "an undetectable pin must be reported as a broken derivation, not "
            "as a clean pipeline:\n" + run.stdout
        )

    def test_a_new_pin_is_derived_without_being_listed(self, tmp_path):
        """The whole point of deriving: a pin added to both sides is checked
        with no edit to this gate."""
        ci = tmp_path / "ci.yml"
        ci.write_text(
            self.CLEAN.replace(
                '  PYTEST_VERSION: "8.3.4"\n',
                '  PYTEST_VERSION: "8.3.4"\n  KUBECONFORM_VERSION: "0.6.7"\n',
            ).replace(
                '      pytest_version: "8.3.4"\n',
                '      pytest_version: "8.3.4"\n      kubeconform_version: "0.6.6"\n',
            )
        )
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 1, (
            "a drifted pin nobody added to this script must still fail:\n"
            + run.stdout
            + run.stderr
        )
        assert "kubeconform_version" in run.stdout


# check-integration-matrix-coverage.sh
# An integration-test directory with no parallel:matrix entry is a suite that
# silently never runs.

class TestIntegrationMatrixCoverage:
    GATE = SCRIPTS / "check-integration-matrix-coverage.sh"

    @staticmethod
    def _fixture(tmp_path: Path, dirs: list[str], matrix: list[str]) -> dict:
        it = tmp_path / "integration-tests"
        for name in dirs:
            scenario = it / name / "molecule" / "default"
            scenario.mkdir(parents=True)
            (scenario / "molecule.yml").write_text("---\n")
        ci = tmp_path / "ci.yml"
        ci.write_text(
            yaml.safe_dump(
                {"integration-tests": {"parallel": {"matrix": [{"TEST": matrix}]}}}
            )
        )
        return {
            "CI_FILE": str(ci),
            "INTEGRATION_DIR": str(it),
            "INTEGRATION_JOB": "integration-tests",
        }

    def test_clean_fixture_passes(self, tmp_path):
        env = self._fixture(tmp_path, ["dns-stack", "mail-stack"], ["dns-stack", "mail-stack"])
        run = _run(["bash", str(self.GATE)], env=env)
        assert run.returncode == 0, run.stdout + run.stderr

    def test_an_unmatrixed_suite_fails(self, tmp_path):
        env = self._fixture(tmp_path, ["dns-stack", "mail-stack"], ["dns-stack"])
        run = _run(["bash", str(self.GATE)], env=env)
        assert run.returncode == 1, "a suite with no matrix entry must fail"
        assert "mail-stack" in run.stderr

    def test_a_missing_job_is_an_operator_error(self, tmp_path):
        env = self._fixture(tmp_path, ["dns-stack"], ["dns-stack"])
        env["INTEGRATION_JOB"] = "renamed-job"
        run = _run(["bash", str(self.GATE)], env=env)
        assert run.returncode != 0, (
            "a renamed job must fail loudly, not report full coverage of nothing"
        )

    def test_a_missing_directory_is_an_operator_error(self, tmp_path):
        env = self._fixture(tmp_path, ["dns-stack"], ["dns-stack"])
        env["INTEGRATION_DIR"] = str(tmp_path / "moved-away")
        run = _run(["bash", str(self.GATE)], env=env)
        assert run.returncode != 0, run.stdout + run.stderr


# check-alertmanager-behaviour.py
# The script itself is vendored (its exhaustive suite lives in weisssrv-lib);
# what this repo owns is the site config it reads. A ROUTE_CASE naming a
# receiver the config no longer defines, or an alertname no rule defines, is the
# local failure mode.

class TestAlertmanagerBehaviourConfig:
    CONFIG = SCRIPTS / "alertmanager-behaviour.yaml"

    @pytest.fixture(scope="class")
    def doc(self) -> dict:
        return yaml.safe_load(self.CONFIG.read_text())

    def test_every_route_case_declares_a_receiver_and_labels(self, doc):
        cases = doc.get("route_cases") or []
        assert cases, "an empty route-case set makes the routing gate vacuous"
        for case in cases:
            assert case.get("receiver"), f"{case} has no receiver"
            labels = case.get("labels") or []
            assert labels, f"{case} has no labels to route on"
            assert all("=" in label for label in labels), f"{case} has a non-matcher label"

    def test_synthetic_alertnames_are_a_subset_of_the_route_cases(self, doc):
        named = {
            label.split("=", 1)[1]
            for case in doc.get("route_cases") or []
            for label in case.get("labels") or []
            if label.startswith("alertname=")
        }
        orphans = sorted(set(doc.get("synthetic_route_alerts") or []) - named)
        assert not orphans, (
            f"synthetic_route_alerts names alerts no route case uses: {orphans} — "
            "the exemption covers nothing"
        )

    def test_upstream_alert_claims_are_not_local_rules(self, doc, tmp_path):
        """An upstream_alerts entry claims the name comes from a CHART. Listing
        a locally-defined alert there would mask it being deleted."""
        out = tmp_path / "rules.yaml"
        run = _run(
            [sys.executable, str(SCRIPTS / "extract-prometheus-config.py"), "rules", str(out)]
        )
        assert run.returncode == 0, run.stdout + run.stderr
        local = {
            rule["alert"]
            for group in (yaml.safe_load(out.read_text()) or {}).get("groups") or []
            for rule in group.get("rules") or []
            if rule.get("alert")
        }
        overlap = sorted(set(doc.get("upstream_alerts") or []) & local)
        assert not overlap, (
            f"declared upstream but defined locally: {overlap} — drop the entries"
        )


# check-netpol-except-parity.py config
# The vendored gate's built-in allowlist is EMPTY (fail-closed), so this file is
# what keeps the two deliberately peer-less egress rules from failing lint — and
# what a reviewer reads to see they are still deliberate.

class TestNetpolExceptConfig:
    CONFIG = SCRIPTS / "netpol-except.yaml"

    @pytest.fixture(scope="class")
    def doc(self) -> dict:
        return yaml.safe_load(self.CONFIG.read_text())

    def test_every_exemption_is_namespaced_and_reasoned(self, doc):
        entries = doc.get("unrestricted_egress_ok") or {}
        assert entries, "an empty allowlist means the two live exemptions fail lint"
        for key, reason in entries.items():
            assert key.count("/") == 1, f"{key!r} is not <namespace>/<policy-name>"
            assert len(reason.split()) >= 8, f"{key} needs a real reason, not {reason!r}"

    def test_exempted_policies_still_exist(self, doc):
        """A stale exemption silently permits a policy name that could be
        reintroduced later with different intent."""
        live = set()
        for path in (REPO / "kubernetes").rglob("*.yaml"):
            text = path.read_text()
            if "kind: NetworkPolicy" not in text:
                continue
            try:
                docs = list(yaml.safe_load_all(text))
            except yaml.YAMLError:
                continue
            for netpol in docs:
                if isinstance(netpol, dict) and netpol.get("kind") == "NetworkPolicy":
                    meta = netpol.get("metadata") or {}
                    live.add(f"{meta.get('namespace')}/{meta.get('name')}")
        stale = sorted(set(doc.get("unrestricted_egress_ok") or {}) - live)
        assert not stale, f"allowlist names policies that no longer exist: {stale}"

    def test_fence_networks_and_canonical_lists_parse_as_cidrs(self, doc):
        import ipaddress

        for cidr in doc.get("fence_networks") or []:
            ipaddress.ip_network(cidr)
        for name, entries in (doc.get("canonical_except_lists") or {}).items():
            assert entries, f"canonical list {name} is empty"
            for cidr in entries:
                ipaddress.ip_network(cidr)
