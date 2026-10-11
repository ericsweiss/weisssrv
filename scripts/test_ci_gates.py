"""The CI gates this repo owns, and the CI bookkeeping nothing else reads.

Failure-path classes assert a gate rejects a fixture carrying the defect it
catches; parity classes hold docs/13 and `task lint` equal to .gitlab-ci.yml.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import taskfile_tree
from script_loader import load_script
from test_vendored_byte_identity import load_ci_doc, parse_ci_doc

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _run(argv: list[str], cwd: Path | None = None, env: dict | None = None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        argv, capture_output=True, text=True, cwd=str(cwd or REPO), env=full_env
    )


@functools.lru_cache(maxsize=None)
def _lib_paths(prefix: str = "") -> frozenset[str]:
    """Repo-relative file paths the library ships at the ref .gitlab-ci.yml pins.

    Falls back to the checkout's working tree only when the ref is not in it
    yet, as the vendored-copy gate does.
    """
    from test_vendored_byte_identity import _lib_root, _pinned_ref, _ref_available

    lib = _lib_root()
    ref = _pinned_ref()
    if _ref_available(lib, ref):
        argv = ["git", "-C", str(lib), "ls-tree", "-r", "--name-only", ref]
        if prefix:
            argv += ["--", prefix]
        out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
        return frozenset(line for line in out.splitlines() if line)
    base = lib / prefix if prefix else lib
    if not base.is_dir():
        return frozenset()
    return frozenset(
        path.relative_to(lib).as_posix() for path in base.rglob("*") if path.is_file()
    )

# check-collection-pin-trigger.py
# A deploy job that runs a playbook but does not trigger on
# ansible/requirements.yml keeps deploying the pre-bump roles.

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

    def test_an_unreadable_ci_file_is_an_operator_error(self, tmp_path):
        """A gate that cannot read its subject reports 2, not a traceback."""
        run = _run([sys.executable, str(self.GATE), str(tmp_path / "nope.yml")])
        assert run.returncode == 2
        assert "cannot read" in run.stderr
        assert "Traceback" not in run.stderr


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
        # A script:-block pin, copied per job: no `variables:` key backs it.
        'job-a:\n'
        '  script:\n'
        '    - |\n'
        '      TASK_VERSION="v3.52.0"\n'
        '      TASK_SHA256="02c6"\n'
        'job-b:\n'
        '  script:\n'
        '    - |\n'
        '      TASK_VERSION="v3.52.0"\n'
        '      TASK_SHA256="02c6"\n'
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

    def test_a_drifted_script_block_copy_fails(self, tmp_path):
        """A pin copied into two script: blocks has no `variables:` counterpart,
        so only copy-to-copy equality holds the two in step."""
        ci = tmp_path / "ci.yml"
        ci.write_text(self.CLEAN.replace('TASK_SHA256="02c6"\n', 'TASK_SHA256="02c7"\n', 1))
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 1, run.stdout + run.stderr
        assert "TASK_SHA256" in run.stdout

    def test_an_unseen_script_block_pin_fails_rather_than_passing_vacuously(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(self.CLEAN.replace("TASK_SHA256", "task_sha256"))
        run = _run(["bash", str(self.GATE), str(ci)])
        assert run.returncode == 1, run.stdout + run.stderr
        assert "no longer sees it" in run.stdout

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


# check-integration-matrix-coverage.py
# An integration-test directory with no parallel:matrix entry is a suite that
# silently never runs.

class TestIntegrationMatrixCoverage:
    GATE = SCRIPTS / "check-integration-matrix-coverage.py"

    @staticmethod
    def _fixture(tmp_path: Path, dirs: list[str], matrix: list[str]) -> list[str]:
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
        return [
            "--ci-file", str(ci),
            "--integration-dir", str(it),
            "--integration-job", "integration-tests",
        ]

    @staticmethod
    def _with(args: list[str], flag: str, value: str) -> list[str]:
        out = list(args)
        out[out.index(flag) + 1] = value
        return out

    def _gate(self, args: list[str]):
        return _run([sys.executable, str(self.GATE), *args])

    def test_clean_fixture_passes(self, tmp_path):
        args = self._fixture(tmp_path, ["dns-stack", "mail-stack"], ["dns-stack", "mail-stack"])
        run = self._gate(args)
        assert run.returncode == 0, run.stdout + run.stderr

    def test_an_unmatrixed_suite_fails(self, tmp_path):
        args = self._fixture(tmp_path, ["dns-stack", "mail-stack"], ["dns-stack"])
        run = self._gate(args)
        assert run.returncode == 1, "a suite with no matrix entry must fail"
        assert "mail-stack" in run.stderr

    def test_a_missing_job_is_an_operator_error(self, tmp_path):
        args = self._fixture(tmp_path, ["dns-stack"], ["dns-stack"])
        run = self._gate(self._with(args, "--integration-job", "renamed-job"))
        assert run.returncode == 2, (
            "a renamed job is an uninspectable subject (exit 2), not full "
            "coverage of nothing"
        )

    def test_a_missing_directory_is_an_operator_error(self, tmp_path):
        args = self._fixture(tmp_path, ["dns-stack"], ["dns-stack"])
        run = self._gate(self._with(args, "--integration-dir", str(tmp_path / "moved-away")))
        assert run.returncode == 2, run.stdout + run.stderr

    def test_an_empty_integration_dir_declares_no_suite(self, tmp_path):
        """A repo with no integration suite says so; the gate then skips the
        comparison instead of reporting coverage of nothing."""
        args = self._fixture(tmp_path, ["dns-stack"], ["dns-stack"])
        run = self._gate(self._with(args, "--integration-dir", ""))
        assert run.returncode == 0, run.stdout + run.stderr
        assert "No integration suite declared" in run.stdout

    def test_an_empty_tree_and_an_empty_matrix_is_an_operator_error(self, tmp_path):
        """Both sides empty makes every comparison trivially clean."""
        args = self._fixture(tmp_path, [], [])
        Path(args[args.index("--integration-dir") + 1]).mkdir(parents=True, exist_ok=True)
        run = self._gate(args)
        assert run.returncode == 2, run.stdout + run.stderr
        assert "inspected nothing" in run.stderr

    def test_the_documented_default_invocation_passes(self):
        """The gate's defaults name this repo's own matrix and suite tree."""
        run = self._gate([])
        assert run.returncode == 0, run.stdout + run.stderr

    @classmethod
    def _ci_invocation_args(cls) -> list[str] | None:
        """The flags the CI lint job hands this gate, or None when nothing in
        .gitlab-ci.yml runs it any more."""
        for line in (REPO / ".gitlab-ci.yml").read_text().splitlines():
            stripped = line.strip()
            if cls.GATE.name not in stripped or stripped.startswith("#"):
                continue
            tokens = stripped.split()
            return tokens[tokens.index(f"scripts/{cls.GATE.name}") + 1:]
        return None

    def test_the_invocation_ci_actually_runs_passes(self):
        """The job's own flags rather than the gate's defaults: a typo in one of
        them ships green while the default-invocation test stays clean."""
        args = self._ci_invocation_args()
        if args is None:
            pytest.skip(".gitlab-ci.yml no longer runs this gate")
        run = self._gate(args)
        assert run.returncode == 0, (
            f"the CI invocation's flags {args} do not pass: "
            + run.stdout + run.stderr
        )


# .gitlab/ci/integration-jobs.yml vs the vendored molecule scripts
# The job's flags and artifacts must match what the vendored copies support: a
# re-vendor that adds a capability otherwise leaves the job wired for the old one.

SANITIZER = SCRIPTS / "sanitize-junit-expected-failures.py"
RETRY = SCRIPTS / "molecule-retry.sh"
INTEGRATION_JOBS = REPO / ".gitlab/ci/integration-jobs.yml"


def _integration_jobs(ci_text: str) -> dict:
    doc = parse_ci_doc(ci_text)
    return {name: job for name, job in doc.items() if isinstance(job, dict)}


def _sanitizer_invocation(ci_text: str) -> str:
    """The integration job's shell step that runs the junit sanitizer."""
    steps = [
        str(step)
        for job in _integration_jobs(ci_text).values()
        for step in (job.get("script") or [])
        if SANITIZER.name in str(step)
    ]
    assert steps, f"no job in integration-jobs.yml runs {SANITIZER.name}"
    return " ".join(steps)


def _artifact_paths(ci_text: str) -> set[str]:
    return {
        str(path)
        for job in _integration_jobs(ci_text).values()
        for path in ((job.get("artifacts") or {}).get("paths") or [])
    }


class TestIntegrationJobMatchesVendoredScripts:
    def test_strict_is_passed_exactly_when_the_sanitizer_accepts_it(self):
        """--strict fails an expectation that matched no testcase.

        Without the flag a stale expected-junit-failures.txt entry only warns;
        with it against an older sanitizer argparse exits 2 and reds the job.
        """
        accepts = "--strict" in SANITIZER.read_text()
        passed = "--strict" in _sanitizer_invocation(INTEGRATION_JOBS.read_text())
        assert accepts == passed, (
            f"{SANITIZER.name} {'accepts' if accepts else 'does not accept'} "
            f"--strict but the integration job "
            f"{'passes' if passed else 'omits'} it. Re-vendoring the script and "
            "wiring the flag are one change."
        )

    def test_retried_attempts_are_kept_exactly_when_the_wrapper_stashes_them(self):
        """molecule-retry.sh moves a failed attempt's XMLs into a subdirectory.

        The junit report glob does not match a subdirectory, so the stashed
        attempts only survive as artifact paths.
        """
        stashes = "failed-attempt-" in RETRY.read_text()
        kept = "junit/failed-attempt-*/" in _artifact_paths(INTEGRATION_JOBS.read_text())
        assert stashes == kept, (
            f"{RETRY.name} {'stashes' if stashes else 'does not stash'} failed "
            f"attempts but the integration job "
            f"{'keeps' if kept else 'drops'} junit/failed-attempt-*/."
        )

    def test_the_flag_parity_is_load_bearing(self):
        """The extractor discriminates, so the assertions above can fail."""
        call = f'python3 "$CI_PROJECT_DIR/scripts/{SANITIZER.name}" --junit-dir j'
        plain = yaml.safe_dump({"integration-tests": {"script": [call]}})
        strict = yaml.safe_dump({"integration-tests": {"script": [call + " --strict"]}})
        assert "--strict" not in _sanitizer_invocation(plain)
        assert "--strict" in _sanitizer_invocation(strict)
        assert _artifact_paths(plain) == set()

    def test_a_job_that_stopped_running_the_sanitizer_is_an_error(self):
        ci = yaml.safe_dump({"integration-tests": {"script": ["molecule test"]}})
        with pytest.raises(AssertionError):
            _sanitizer_invocation(ci)


# check-alertmanager-behaviour.py
# The script is vendored; this repo owns the site config it reads. The local
# failure mode is a ROUTE_CASE naming a receiver or alertname nothing defines.

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
# The vendored gate's built-in allowlist is empty (fail-closed), so this file
# carries the two peer-less egress rules and the reasons for them.

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

    def test_the_library_fence_lists_are_not_shadowed(self, doc):
        """Declaring either key replaces the vendored gate's built-in wholesale,
        so a library update to the fences would land here inert."""
        for key in ("canonical_except_lists", "fence_networks"):
            assert key not in doc, (
                f"{key} in netpol-except.yaml overrides the vendored gate's "
                "built-in; drop it so a library update applies"
            )


class TestPendingAdoptionTable:
    """docs/13's pending-adoption table lists what each consumer still owns locally.

    Every row's library path exists and its local anchor is still defined, and
    every library template is included, pending, or declared not-consumed here.
    """

    DOC = REPO / "docs/13-ci-cd.md"
    MARKER = "**Pending adoption.**"

    # Extracted templates this pipeline does not include. An entry here is a
    # decision with a reason; backlog rows live in the docs/13 table. A template
    # in neither place fails the sweep.
    NOT_CONSUMED = {
        "/ci/build/docker-build.yml": (
            "this pipeline's .build-image-base is not on an adoption path "
            "(docs/13 names it under the table)"
        ),
        "/ci/maintenance/version-bump-bot.yml": (
            "the local version-bump-bot job is deliberately kept — "
            "docs/13 § Version bump bot"
        ),
        "/ci/release/github-release-workflow.example.yml": (
            "an Actions reference copy for GitHub-shape consumers, not a "
            "GitLab include"
        ),
        "/ci/release/semantic-release.yml": (
            "this repo cuts no releases — deploys are merge-triggered, not "
            "tagged"
        ),
    }
    # Library ci/ subdirectories that hold consumer-facing GitLab templates.
    # ci/github (Actions examples) and ci/internal (the library's own jobs)
    # are out of scope by construction.
    TEMPLATE_DIRS = (
        "build", "deploy", "lint", "maintenance", "release",
        "review", "security", "templates", "test", "validate",
    )

    def _rows(self) -> list[tuple[list[str], str]]:
        """(library paths, local-counterpart cell) per pending-adoption row."""
        lines = self.DOC.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(self.MARKER))
        rows: list[tuple[list[str], str]] = []
        seen_table = False
        for line in lines[start:]:
            if not line.startswith("|"):
                if seen_table:
                    break
                continue
            seen_table = True
            cells = line.split("|")
            paths = re.findall(r"`(/ci/[\w./-]+\.yml)`", cells[1])
            if paths:
                rows.append((paths, cells[2]))
        assert rows, "parsed no library paths out of the pending-adoption table"
        return rows

    @staticmethod
    def _included_files() -> set[str]:
        ci = load_ci_doc(REPO / ".gitlab-ci.yml")
        files: set[str] = set()
        for entry in ci.get("include") or []:
            if not isinstance(entry, dict):
                continue
            value = entry.get("file")
            if isinstance(value, list):
                files.update(str(v) for v in value)
            elif value:
                files.add(str(value))
        return files

    def test_every_pending_library_path_exists_in_the_library(self):
        """Pinned ref OR working tree: a row may name a template the library has
        extracted but not yet tagged, which the adoption bump will carry."""
        from test_vendored_byte_identity import _lib_root

        lib = _lib_root()
        shipped = _lib_paths()
        missing = sorted(
            path
            for paths, _local in self._rows()
            for path in paths
            if ".." in Path(path).parts
            or (path.lstrip("/") not in shipped and not (lib / path.lstrip("/")).is_file())
        )
        assert not missing, (
            f"{self.DOC.name}'s pending-adoption table names {missing}, which the "
            "library checkout does not ship — the template moved or retired, so "
            "the row is wrong either way."
        )

    def test_every_row_is_blocked_on_a_local_anchor_that_still_exists(self):
        """Each pending row's local anchor is still DEFINED in .gitlab-ci.yml.

        `extends:` and `!reference` usages survive adoption, so only the
        `^.anchor:` definition line proves the local block is still there.
        """
        ci_text = (REPO / ".gitlab-ci.yml").read_text()
        problems = []
        for paths, local in self._rows():
            anchors = re.findall(r"`(\.[\w-]+)`", local)
            if not anchors:
                problems.append(
                    f"{paths[0]}: its row names no backticked `.anchor` — the "
                    "stale-row check has nothing to bind to"
                )
                continue
            for anchor in anchors:
                if not re.search(rf"(?m)^{re.escape(anchor)}:", ci_text):
                    problems.append(
                        f"{paths[0]}: blocked on {anchor}, whose definition no "
                        "longer exists — the template was adopted, drop the row"
                    )
        assert not problems, (
            f"{self.DOC.name}'s pending-adoption table is stale:\n  "
            + "\n  ".join(problems)
        )

    def test_every_library_template_is_included_pending_or_declared(self):
        """Every extracted template is classified exactly once.

        Included, pending in the docs/13 table, or declared not-consumed; a
        template in none or in more than one of them fails.
        """
        ci_paths = _lib_paths("ci")
        assert ci_paths, "the library checkout ships no ci/ tree at the pinned ref"
        # A NEW library ci/ directory must be classified before the sweep can
        # claim coverage — silently ignoring it recreates the unwatched gap.
        known_dirs = set(self.TEMPLATE_DIRS) | {"github", "internal"}
        unclassified = sorted(
            {
                path.split("/")[1]
                for path in ci_paths
                if len(path.split("/")) > 2 and path.split("/")[1] not in known_dirs
            }
        )
        assert not unclassified, (
            f"library ci/ directories not classified as consumer-facing or excluded: {unclassified}"
        )
        shipped = {
            f"/{path}"
            for path in ci_paths
            if path.endswith(".yml") and path.split("/")[1] in set(self.TEMPLATE_DIRS)
        }
        assert shipped, "the library checkout ships no ci/ templates at all"
        included = self._included_files()
        pending = {path for paths, _local in self._rows() for path in paths}

        unaccounted = sorted(shipped - included - pending - set(self.NOT_CONSUMED))
        assert not unaccounted, (
            "library templates neither included, nor pending in docs/13, nor "
            f"declared not-consumed here: {unaccounted} — adopt, table, or "
            "declare each with its reason."
        )
        # Every pair of categories is contradictory bookkeeping, not just
        # overlap with the include list: pending AND not-consumed is a
        # decision recorded twice with opposite meanings.
        not_consumed = set(self.NOT_CONSUMED)
        overlapping = sorted(
            (pending & included) | (not_consumed & included) | (pending & not_consumed)
        )
        assert not overlapping, (
            f"templates recorded in more than one adoption category: {overlapping}"
        )


class TestPythonTestsFireOnTheirOwnSubjects:
    """The python-tests job's `changes:` list covers what the gates read.

    A guard whose subject matches no pattern does not run on the MR that breaks
    it. Subjects come from each gate's repo-path literals that git tracks.
    """

    JOB_TEMPLATE = "/ci/test/python-tests.yml"
    # Top-level trees a literal has to sit under to be a repo path rather than a
    # URL fragment, a label key or a message.
    TREES = ("ansible/", "kubernetes/", "terraform/", "docker/", "docs/",
             "scripts/", ".gitlab/", ".claude/", ".github/")
    ROOT_FILES = {"Taskfile.yml", "README.md", "CLAUDE.md", "AGENTS.md",
                  ".cursorrules", "ruff.toml", ".gitlab-ci.yml", ".gitignore"}
    ROOT_TREE = "<root>"
    # Anti-vacuity floor PER TREE: one tree's subjects can disappear entirely
    # while a single global total stays comfortably above any floor.
    MIN_SUBJECTS = {"kubernetes/": 15, "ansible/": 10, "scripts/": 8,
                    ROOT_TREE: 5, "terraform/": 1, "docs/": 1}

    @classmethod
    def _tree(cls, subject: str) -> str:
        for tree in cls.TREES:
            if subject.startswith(tree):
                return tree
        return cls.ROOT_TREE

    @staticmethod
    def _glob_to_re(glob: str) -> re.Pattern:
        """GitLab matches `changes:` with Ruby File.fnmatch under PATHNAME, so
        `*` stops at a '/' and only `**` crosses one. Python's fnmatch does not
        make that distinction and would call every pattern a match."""
        out = ""
        i = 0
        while i < len(glob):
            if glob.startswith("**/", i):
                out += "(?:.*/)?"
                i += 3
            elif glob.startswith("**", i):
                out += ".*"
                i += 2
            elif glob[i] == "*":
                out += "[^/]*"
                i += 1
            elif glob[i] == "?":
                out += "[^/]"
                i += 1
            else:
                out += re.escape(glob[i])
                i += 1
        return re.compile("^" + out + "$")

    @classmethod
    def _changes(cls) -> list[str]:
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        for include in doc.get("include") or []:
            if isinstance(include, dict) and include.get("file") == cls.JOB_TEMPLATE:
                patterns = (include.get("inputs") or {}).get("changes")
                assert patterns, f"{cls.JOB_TEMPLATE} passes no changes: list"
                return patterns
        raise AssertionError(f"{cls.JOB_TEMPLATE} is not included any more")

    @staticmethod
    def _tracked() -> tuple[set[str], set[str]]:
        """(tracked files, their parent directories) — the ground truth for
        'this literal is a real subject'."""
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                             cwd=str(REPO), check=True).stdout.split()
        files = set(out)
        dirs = {
            "/".join(path.split("/")[:depth])
            for path in files
            for depth in range(1, path.count("/") + 1)
        }
        return files, dirs

    @classmethod
    def _subjects(cls) -> dict[str, set[str]]:
        """{repo path a gate names: the gates that name it}.

        Single string literals only: assembled paths and globs are invisible
        here, which is what the per-tree floors below exist to catch.
        """
        literal = re.compile(r"""["']([^"'\s]+)["']""")
        found: dict[str, set[str]] = {}
        for path in sorted(SCRIPTS.iterdir()):
            if path.suffix not in (".py", ".sh"):
                continue
            if not (path.name.startswith("test_") or path.name.startswith("check-")):
                continue
            for match in literal.finditer(path.read_text()):
                value = match.group(1).rstrip("/")
                if "*" in value:
                    continue
                if value.startswith(cls.TREES) or value in cls.ROOT_FILES:
                    found.setdefault(value, set()).add(path.name)
        return found

    def test_every_gate_subject_is_a_python_tests_trigger(self):
        matchers = [self._glob_to_re(p) for p in self._changes()]
        files, dirs = self._tracked()
        uncovered: list[str] = []
        checked: dict[str, int] = {}
        for subject, gates in sorted(self._subjects().items()):
            if subject in files:
                probe = subject
            elif subject in dirs:
                # A directory subject means the gate reads files under it.
                probe = f"{subject}/probe.yaml"
            else:
                continue  # a fixture path, not a real repo subject
            checked[self._tree(subject)] = checked.get(self._tree(subject), 0) + 1
            if not any(m.match(probe) for m in matchers):
                uncovered.append(f"{subject} (read by {', '.join(sorted(gates))})")
        thin = {tree: (checked.get(tree, 0), floor)
                for tree, floor in self.MIN_SUBJECTS.items()
                if checked.get(tree, 0) < floor}
        assert not thin, (
            "the literal scan stopped resolving gate subjects under "
            + ", ".join(f"{tree} ({got} < {floor})"
                        for tree, (got, floor) in sorted(thin.items()))
            + " — this guard reports full coverage of a tree it cannot see. "
            "Either a gate's paths moved out of single string literals (see "
            "_subjects) or the gates themselves went away."
        )
        assert not uncovered, (
            "gates read repo paths that no python-tests `changes:` pattern "
            "matches, so they cannot fire on their own subject:\n  "
            + "\n  ".join(uncovered)
            + f"\n\nAdd a pattern for each to the {self.JOB_TEMPLATE} include "
            "inputs in .gitlab-ci.yml."
        )

    def test_the_matcher_respects_path_boundaries(self):
        """A matcher that let `*` cross '/' would report full coverage of
        anything, which is the failure mode this whole class exists to stop."""
        single = self._glob_to_re("kubernetes/*/README.md")
        assert single.match("kubernetes/apps/README.md")
        assert not single.match("kubernetes/apps/authentik/README.md")
        deep = self._glob_to_re("kubernetes/**/*")
        assert deep.match("kubernetes/apps/authentik/release.yaml")
        assert not deep.match("scripts/check-doc-links.py")


class TestLintMirrorsTheCiLintStage:
    """`task lint` says it mirrors the CI lint stage; this enforces it.

    Neither side reads the other, so a gate added to CI alone would pass every
    local run right up to the pipeline that rejects the MR.
    """

    # `task lint` commands that are not gates and so need no CI twin.
    NOT_A_GATE = {
        "scripts/flux-child-kustomizations.py": (
            "lists the cluster's stages for the tasks that loop over them; it "
            "asserts nothing, and the gate over the same list is "
            "scripts/test_flux_child_kustomizations.py"
        ),
        "scripts/resolve-tool.sh": (
            "resolves how to invoke a pyenv/PATH dev tool locally; CI installs "
            "its tools at pinned versions and never resolves one"
        ),
    }

    # Gates the pipeline runs from somewhere a scan of this repo's .gitlab-ci.yml
    # cannot see: a library template's body, or another test. The `/ci/...yml`
    # values are checked against the live include: list below.
    CI_RUNS_IT_ELSEWHERE = {
        "scripts/check-comment-length.py": (
            "the library's /ci/lint/comment-length.yml job runs it; the script "
            "path lives in that template, not here"
        ),
        "scripts/check-doc-links.py": (
            "the library's /ci/lint/docs-link-check.yml job runs it; the script "
            "name lives in that template, not here"
        ),
        "scripts/check-kustomization-coverage.py": (
            "the python-tests job runs scripts/test_kustomization_coverage.py, "
            "which drives this gate over the real tree"
        ),
        "scripts/flux-corpus-gates.sh": (
            "CI reaches it through the flux-lint template's extra_validation "
            "input rather than a run_check line, so a scan of the job bodies "
            "here does not see it"
        ),
    }

    # run_check entries whose command is an inline shell function rather than a
    # script — each maps to the `task lint` sub-task that runs the same logic.
    INLINE_CHECKS = {
        "cluster-invariants": "lint:cluster-invariants",
        "flux-version-pin": "lint:flux-version-pin",
        "busybox-version-pin": "lint:busybox-version-pin",
        "tailscale-policy-syntax": "lint:tailscale-policy",
        "partner-pins": "lint:partner-pins",
        "taskfile-smoke": "lint:taskfile-smoke",
        "hosts-env-sync": "lint:sync-checks",
        "flux-versions-sync": "lint:sync-checks",
    }

    @staticmethod
    def _ci_jobs() -> dict:
        return load_ci_doc(REPO / ".gitlab-ci.yml")

    def _run_checks(self) -> dict[str, str]:
        """{check name: the rest of its run_check line} across both gate jobs."""
        found: dict[str, str] = {}
        jobs = self._ci_jobs()
        for job in ("check-repo-policies", "check-generated-files"):
            script = jobs[job]["script"]
            body = "\n".join(script if isinstance(script, list) else [script])
            for match in re.finditer(r"^\s*run_check\s+(\S+)\s+(.*)$", body, re.M):
                found[match.group(1)] = match.group(2)
        assert found, "parsed no run_check entries out of the CI gate jobs"
        return found

    @staticmethod
    def _lint_tree() -> tuple[str, set[str]]:
        """(every command string reachable from `task lint`, the task names walked)."""
        tasks = taskfile_tree.load_tasks(REPO)
        seen: set[str] = set()
        commands: list[str] = []

        def walk(name: str) -> None:
            if name in seen or name not in tasks:
                return
            seen.add(name)
            for cmd in tasks[name].get("cmds") or []:
                if isinstance(cmd, dict) and "task" in cmd:
                    walk(cmd["task"])
                elif isinstance(cmd, str):
                    commands.append(cmd)
                    # `- task lint:x` spelled through the shell (see the
                    # scripts:test note in Taskfile.yml) is still a dependency.
                    for ref in re.findall(r"\btask\s+([\w:-]+)", cmd):
                        walk(ref)
        walk("lint")
        assert "lint" in seen, "Taskfile.yml has no `lint` task"
        return "\n".join(commands), seen

    def test_every_ci_gate_script_is_reachable_from_task_lint(self):
        commands, _tasks = self._lint_tree()
        missing = []
        for name, argv in self._run_checks().items():
            scripts = re.findall(r"scripts/[\w.-]+\.(?:py|sh)", argv)
            if not scripts:
                continue
            for script in scripts:
                if script not in commands:
                    missing.append(f"{name} -> {script}")
        assert not missing, (
            "CI lint-stage checks that `task lint` never runs: "
            + ", ".join(sorted(missing))
            + "\n\nAdd them to a lint: sub-task, or `task lint` is not the mirror it "
            "advertises."
        )

    def test_every_inline_ci_check_maps_to_a_lint_subtask(self):
        """Floor set: a check that names no script still has to be listed."""
        _commands, tasks = self._lint_tree()
        checks = self._run_checks()
        unmapped = sorted(
            name for name, argv in checks.items()
            if not re.search(r"scripts/[\w.-]+\.(?:py|sh)", argv)
            and name not in self.INLINE_CHECKS
        )
        assert not unmapped, (
            f"inline CI checks with no declared `task lint` counterpart: {unmapped} — "
            "add each to INLINE_CHECKS naming the sub-task that mirrors it."
        )
        stale = sorted(set(self.INLINE_CHECKS) - set(checks))
        assert not stale, f"INLINE_CHECKS names checks CI no longer runs: {stale}"
        unreachable = sorted(
            task for name, task in self.INLINE_CHECKS.items() if task not in tasks
        )
        assert not unreachable, (
            f"mapped sub-tasks not reachable from `task lint`: {unreachable}"
        )

    @staticmethod
    def _ci_shell() -> str:
        """Every shell string the pipeline executes, comments removed.

        Job script bodies plus the shell include inputs, not the raw file: a
        `changes:` path or a comment naming a gate is not a run of it.
        """
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        parts: list[str] = []

        def add(value) -> None:
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                for item in value:
                    add(item)

        for include in doc.get("include") or []:
            if isinstance(include, dict):
                for key, value in (include.get("inputs") or {}).items():
                    if (key == "extra_validation" or key.endswith("_command")
                            or key.endswith("_script")):
                        add(value)
        for job in doc.values():
            if isinstance(job, dict):
                for key in ("script", "before_script", "after_script"):
                    add(job.get(key))
        return re.sub(r"(?m)#.*$", "", "\n".join(parts))

    @classmethod
    def _ci_scripts(cls) -> set[str]:
        """Every scripts/ gate the pipeline can reach.

        Either named in a shell string it runs, or reached through `task <name>`,
        which hides the script name from a literal scan.
        """
        shell = cls._ci_shell()
        reachable = set(re.findall(r"scripts/[\w.-]+\.(?:py|sh)", shell))
        tasks = taskfile_tree.load_tasks(REPO)
        for name in re.findall(r"\btask\s+([\w:-]+)", shell):
            for cmd in tasks.get(name, {}).get("cmds") or []:
                if isinstance(cmd, str):
                    reachable.update(re.findall(r"scripts/[\w.-]+\.(?:py|sh)", cmd))
        return reachable

    def test_every_task_lint_gate_has_a_ci_twin(self):
        """The other direction: a gate added to `task lint` alone is invisible
        to the pipeline, so an MR that breaks it merges green. `task lint`
        advertises itself as the CI mirror; a mirror is symmetric."""
        commands, _tasks = self._lint_tree()
        lint_scripts = set(re.findall(r"scripts/[\w.-]+\.(?:py|sh)", commands))
        exempt = set(self.NOT_A_GATE) | set(self.CI_RUNS_IT_ELSEWHERE)
        missing = sorted(lint_scripts - self._ci_scripts() - exempt)
        assert not missing, (
            "gates `task lint` runs that no CI job can reach: "
            + ", ".join(missing)
            + "\n\nAdd each to .gitlab-ci.yml (a run_check line in "
            "check-repo-policies, or the flux-lint extra_validation input for a "
            "corpus check), or record it in NOT_A_GATE / CI_RUNS_IT_ELSEWHERE "
            "with the reason."
        )
        stale = sorted(exempt - lint_scripts)
        assert not stale, f"exemptions name scripts `task lint` no longer runs: {stale}"

    def test_the_elsewhere_exemptions_name_live_ci_includes(self):
        """An exemption that points at a deleted library job would silently
        excuse a gate nothing runs."""
        included = set(re.findall(r"^\s*file:\s*(/ci/\S+\.yml)\s*$",
                                  (REPO / ".gitlab-ci.yml").read_text(), re.M))
        assert included, "parsed no library include paths out of .gitlab-ci.yml"
        for script, reason in self.CI_RUNS_IT_ELSEWHERE.items():
            for named in re.findall(r"/ci/[\w./-]+\.yml", reason):
                assert named in included, (
                    f"{script} is exempted because {named} runs it, but that "
                    "template is no longer included"
                )

    def test_the_parser_would_notice_a_missing_gate(self):
        """A parity test that cannot fail is worse than none."""
        commands, tasks = self._lint_tree()
        assert "scripts/check-deploy-coverage.sh" in commands
        assert "scripts/check-not-a-real-gate.py" not in commands
        assert "lint:sync-checks" in tasks
        # ...and in the lint -> CI direction.
        reachable = self._ci_scripts()
        assert "scripts/check-cluster-literals.py" in reachable
        assert "scripts/check-tailscale-policy.py" in reachable, (
            "the `task <name>` indirection must be followed, or every "
            "inline-function gate reads as a missing CI twin"
        )
        assert "scripts/check-not-a-real-gate.py" not in reachable


class TestLivePolicyKindParity:
    """taskfiles/flux.yml's LIVE_POLICY_KINDS is the single source for the kinds
    check-live-cpu-limits.py inspects. The scheduled cluster-drift-plan job
    spells that list by hand, so a kind added to the var must reach it too."""

    KIND_ARGV = re.compile(
        r"kubectl\s+get\s+([\w,]+)\s+-A\s+-o\s+json\s*\|\s*python3\s+"
        r"scripts/check-live-cpu-limits\.py"
    )

    @staticmethod
    def _taskfile_kinds() -> set[str]:
        doc = yaml.safe_load((REPO / "taskfiles" / "flux.yml").read_text()) or {}
        kinds = (doc.get("vars") or {}).get("LIVE_POLICY_KINDS")
        assert kinds, "taskfiles/flux.yml declares no LIVE_POLICY_KINDS var"
        return {kind for kind in str(kinds).split(",") if kind}

    @staticmethod
    def _drift_job_body() -> str:
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        job = doc.get("cluster-drift-plan")
        assert job, ".gitlab-ci.yml has no cluster-drift-plan job"
        script = job["script"]
        return "\n".join(script if isinstance(script, list) else [script])

    @classmethod
    def _kinds_in(cls, body: str) -> set[str]:
        found = cls.KIND_ARGV.findall(body)
        assert len(found) == 1, (
            "expected exactly one check-live-cpu-limits.py kubectl argv, "
            f"found {found}"
        )
        return {kind for kind in found[0].split(",") if kind}

    def test_the_scheduled_drift_job_inspects_the_taskfile_kind_set(self):
        assert self._kinds_in(self._drift_job_body()) == self._taskfile_kinds(), (
            "cluster-drift-plan's kubectl argv disagrees with "
            "taskfiles/flux.yml's LIVE_POLICY_KINDS — the scheduled detector "
            "would inspect a narrower set than `task flux:verify-cpu-limits`"
        )

    def test_a_narrowed_ci_argv_fails(self):
        """A parity test that cannot fail is worse than none."""
        body = self._drift_job_body()
        argv = self.KIND_ARGV.search(body).group(1)
        narrowed = body.replace(argv, ",".join(argv.split(",")[:-1]), 1)
        assert narrowed != body
        assert self._kinds_in(narrowed) != self._taskfile_kinds()


class TestTerraformDriftPlanJobs:
    """The three terraform drift-plan jobs are detectors, never reconcilers.

    They come from the library template, so what this repo owns is the include
    inputs. The exit-code contract lives in the template, checked at the pin.
    """

    TEMPLATE = "/ci/validate/terraform-drift-plan.yml"
    CONSUMERS = ("tailscale-drift-plan", "authentik-drift-plan", "unifi-drift-plan")
    APPLY = re.compile(r"\bterraform\s+apply\b")

    @classmethod
    def _includes(cls) -> dict[str, dict]:
        """{job_name: that include's inputs} for every drift-plan include."""
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        found: dict[str, dict] = {}
        for include in doc.get("include") or []:
            if not isinstance(include, dict):
                continue
            files = include.get("file")
            files = files if isinstance(files, list) else [files]
            if cls.TEMPLATE not in files:
                continue
            inputs = include.get("inputs") or {}
            name = inputs.get("job_name")
            assert name, f"a {cls.TEMPLATE} include passes no job_name"
            assert name not in found, f"two includes share job_name {name}"
            found[name] = inputs
        return found

    @classmethod
    def _template_doc(cls) -> dict:
        """The library template's job body at the pinned ref."""
        from test_vendored_byte_identity import (
            _CILoader, _lib_root, _pinned_ref, _ref_available,
        )

        lib = _lib_root()
        ref = _pinned_ref()
        relpath = cls.TEMPLATE.lstrip("/")
        if _ref_available(lib, ref):
            blob = subprocess.run(
                ["git", "-C", str(lib), "show", f"{ref}:{relpath}"],
                capture_output=True, text=True,
            )
            assert blob.returncode == 0, blob.stderr
            text = blob.stdout
        else:
            text = (lib / relpath).read_text()
        docs = [d for d in yaml.load_all(text, Loader=_CILoader) if isinstance(d, dict)]
        assert len(docs) == 2, f"{relpath} is not a spec header plus a job body"
        return docs[1]

    def test_every_module_is_included_exactly_once(self):
        found = self._includes()
        assert set(found) == set(self.CONSUMERS), (
            f"drift-plan includes are {sorted(found)} but CONSUMERS names "
            f"{sorted(self.CONSUMERS)} — a module with no include is undetected, "
            "and a job absent from CONSUMERS skips every arm below."
        )

    def test_each_include_names_its_own_module_and_state(self):
        found = self._includes()
        modules = [inputs.get("module_dir") for inputs in found.values()]
        states = [inputs.get("state_name") for inputs in found.values()]
        assert all(modules) and len(set(modules)) == len(modules), modules
        assert all(states) and len(set(states)) == len(states), (
            f"two drift plans share a terraform state: {states}"
        )
        for name, inputs in found.items():
            module = str(inputs["module_dir"])
            assert (REPO / module).is_dir(), f"{name} plans a missing {module}"

    def test_each_include_runs_on_the_infrastructure_runner(self):
        """The template default is `[]` — any runner, including tagless ones."""
        for name, inputs in self._includes().items():
            assert inputs.get("tags") == ["infrastructure"], (
                f"{name} passes tags {inputs.get('tags')!r}"
            )

    def test_the_jobs_never_apply(self):
        applies = {
            name: self.APPLY.findall(str(inputs.get("secrets_exports") or ""))
            for name, inputs in self._includes().items()
        }
        assert not any(applies.values()), {k: v for k, v in applies.items() if v}
        body = yaml.safe_dump(self._template_doc())
        assert not self.APPLY.search(body), "the library template now applies"

    def test_the_never_applies_assertion_is_load_bearing(self):
        assert self.APPLY.search("terraform apply -auto-approve")

    def test_every_secret_read_is_assigned_then_exported(self):
        """`export X=$(op read ...)` returns export's status, so a failed read
        plans with an empty credential instead of failing the job."""
        bad = {}
        for name, inputs in self._includes().items():
            offenders = [
                line.strip()
                for line in str(inputs.get("secrets_exports") or "").splitlines()
                if re.match(r"\s*export\s+\w+=", line)
            ]
            if offenders:
                bad[name] = offenders
        assert not bad, bad

    def test_the_template_still_asks_for_a_detailed_exit_code(self):
        body = yaml.safe_dump(self._template_doc())
        assert "terraform plan" in body, "the template runs no terraform plan"
        assert "-detailed-exitcode" in body, (
            "the library template dropped -detailed-exitcode, so drift exits 0 "
            "and the advisory-yellow allow_failure never fires"
        )

    def test_only_the_drift_exit_code_is_tolerated(self):
        job = next(iter(self._template_doc().values()))
        assert job["allow_failure"] == {"exit_codes": [2]}, job.get("allow_failure")

    # Every drift job materializes provider credentials, so these arms are about
    # WHERE that read is allowed to happen.
    SCHEDULE_ANCHOR = ".drift-schedule-rule"
    LOCAL_VAULT_JOBS = ("b2-drift-plan",)

    @staticmethod
    def _job_text(name: str) -> str:
        """One top-level job's raw block, so `!reference` entries stay visible."""
        lines = (REPO / ".gitlab-ci.yml").read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line == f"{name}:")
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if lines[i] and not lines[i][0].isspace() and not lines[i].startswith("#")
            ),
            len(lines),
        )
        return "\n".join(lines[start:end])

    @staticmethod
    def _mr_rules(rules) -> list[str]:
        return [
            str(rule.get("if"))
            for rule in (rules or [])
            if isinstance(rule, dict) and "merge_request_event" in str(rule.get("if", ""))
        ]

    def test_the_template_has_no_merge_request_rule(self):
        """A vault read in a job running an unmerged branch's code (docs/13)."""
        job = next(iter(self._template_doc().values()))
        assert not self._mr_rules(job.get("rules"))

    def test_no_local_drift_job_reads_the_vault_on_a_merge_request_pipeline(self):
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        offenders = {
            name: found
            for name in self.LOCAL_VAULT_JOBS
            if (found := self._mr_rules((doc.get(name) or {}).get("rules")))
        }
        assert not offenders, (
            f"these drift jobs read provider credentials on an MR pipeline: {offenders} "
            "— that runs an unmerged branch's code with the vault token (docs/13 "
            "§ Vault reads on merge-request pipelines)"
        )

    def test_the_merge_request_arm_is_load_bearing(self):
        """A parity test that cannot fail is worse than none."""
        assert self._mr_rules(
            [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
        )
        assert not self._mr_rules([{"if": '$CI_COMMIT_BRANCH == "main"'}])

    def test_every_local_drift_job_keeps_the_scheduled_detector(self):
        for name in self.LOCAL_VAULT_JOBS:
            assert f"!reference [{self.SCHEDULE_ANCHOR}, rules]" in self._job_text(name), (
                f"{name} dropped the scheduled detector, so out-of-band console "
                "drift is only seen when something in-repo changes"
            )

    def test_no_included_drift_plan_conjoins_its_schedule_with_a_credential(self):
        """A credential guard REMOVES the job when the token is revoked, so drift
        goes undetected behind a green pipeline instead of reding the job."""
        for name, inputs in self._includes().items():
            guard = str(inputs.get("secrets_guard", "true"))
            assert "$" not in guard, (
                f"{name} passes secrets_guard {guard!r}; a variable there deletes "
                "the scheduled detector when the credential goes away"
            )

    def test_the_schedule_rule_is_not_conjoined_with_a_credential(self):
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        rules = (doc.get(self.SCHEDULE_ANCHOR) or {}).get("rules")
        assert rules == [{"if": '$CI_PIPELINE_SOURCE == "schedule"'}], rules


class TestYamlLintTargetParity:
    """The CI yaml-lint targets and `task lint:yamllint` must cover the same tree.

    A path linted in one place only merges green locally or reds only in CI.
    """

    # CI-only targets: terraform/ holds no YAML `task lint` reaches, and the
    # terraform helper YAML is linted by its own lint:terraform-yaml task.
    CI_ONLY = {"terraform/"}

    @staticmethod
    def _ci_targets() -> set[str]:
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        for include in doc.get("include") or []:
            if not isinstance(include, dict):
                continue
            files = include.get("file")
            files = files if isinstance(files, list) else [files]
            if "/ci/lint/yaml-lint.yml" in files:
                targets = (include.get("inputs") or {}).get("targets")
                assert targets, "the yaml-lint include passes no targets input"
                return set(str(targets).split())
        raise AssertionError(".gitlab-ci.yml does not include /ci/lint/yaml-lint.yml")

    @staticmethod
    def _task_targets(command: str | None = None) -> set[str]:
        if command is None:
            tasks = taskfile_tree.load_tasks(REPO)
            cmds = [c for c in tasks["lint:yamllint"]["cmds"] if isinstance(c, str)]
            command = next(c for c in cmds if "yamllint -c" in c)
        _config, _rest = command.split("yamllint -c ", 1)[1].split(" ", 1)
        return set(_rest.split())

    def test_the_two_target_lists_agree(self):
        assert self._ci_targets() - self.CI_ONLY == self._task_targets(), (
            "the CI yaml-lint targets and `task lint:yamllint` disagree — "
            "add the path to both, or record a CI-only path in CI_ONLY"
        )

    def test_the_profile_directory_is_itself_linted(self):
        """lint/ triggers the job; without it in targets it is never read."""
        assert "lint/" in self._ci_targets()
        assert "lint/" in self._task_targets()

    def test_a_dropped_target_fails(self):
        """A parity test that cannot fail is worse than none."""
        dropped = self._task_targets("yamllint -c lint/yamllint-relaxed.yml ansible/")
        assert dropped != self._ci_targets() - self.CI_ONLY


class TestVerifiedDownloadBlocksUseErrexit:
    """Every sha256-verified download block in CI must run under errexit.

    GitLab Runner checks only the LAST command of a multi-line `- |` entry, so
    without `set -e` a failed `sha256sum -c -` leaves the binary on PATH.
    """

    CI_FILES = (".gitlab-ci.yml", ".gitlab/ci/integration-jobs.yml")
    SCRIPT_KEYS = ("before_script", "script", "after_script")
    VERIFY = "sha256sum -c"

    @classmethod
    def _verified_blocks(cls) -> list[tuple[str, str, str]]:
        """(file, job, block) for every multi-line block that verifies a sha256."""
        found = []
        for name in cls.CI_FILES:
            doc = load_ci_doc(REPO / name)
            for job, body in doc.items():
                if not isinstance(body, dict):
                    continue
                for key in cls.SCRIPT_KEYS:
                    for step in body.get(key) or []:
                        if isinstance(step, str) and cls.VERIFY in step:
                            found.append((name, f"{job}.{key}", step))
        return found

    @staticmethod
    def _errexit_precedes_verify(block: str) -> bool:
        lines = block.splitlines()
        for line in lines:
            stripped = line.strip()
            if re.fullmatch(r"set -[a-z]*e[a-z]*( .*)?", stripped):
                return True
            if TestVerifiedDownloadBlocksUseErrexit.VERIFY in stripped:
                return False
        return False

    def test_every_block_sets_errexit_before_it_verifies(self):
        blocks = self._verified_blocks()
        assert blocks, "parsed no sha256-verified CI blocks — this gate is examining nothing"
        bad = [
            f"{name}: {where}"
            for name, where, block in blocks
            if not self._errexit_precedes_verify(block)
        ]
        assert not bad, (
            "these CI script blocks verify a download without errexit, so a "
            "sha256 mismatch cannot fail the job: " + ", ".join(bad)
        )

    def test_a_block_without_errexit_is_rejected(self):
        """A gate that cannot fail is worse than none."""
        mutant = (
            'curl -fsSL https://example.invalid/x -o /tmp/x\n'
            'echo "deadbeef  /tmp/x" | sha256sum -c -\n'
            'install -m 0755 /tmp/x /usr/local/bin/x\n'
        )
        assert not self._errexit_precedes_verify(mutant)
        assert self._errexit_precedes_verify("set -eo pipefail\n" + mutant)


class TestInputsHeaderParsing:
    """GitLab's `spec:` inputs syntax makes a pipeline file two documents.

    Single-document `yaml.load` raises ComposerError on such a file, so every
    pipeline parser here takes the last mapping document instead.
    """

    PIPELINE = (
        "spec:\n"
        "  inputs:\n"
        "    stage:\n"
        "      default: deploy\n"
        "---\n"
        "variables:\n"
        "  WEISSSRV_LIB_REF: v1.2.3\n"
        ".acme-script:\n"
        "  script:\n"
        "    - ansible-playbook playbooks/acme.yml\n"
        "deploy-acme:\n"
        "  stage: deploy\n"
        "  script:\n"
        "    - !reference [.acme-script, script]\n"
    )

    def test_the_null_tag_parser_takes_the_jobs_document(self):
        doc = parse_ci_doc(self.PIPELINE)
        assert set(doc) == {"variables", ".acme-script", "deploy-acme"}

    def test_the_reference_preserving_parser_takes_the_jobs_document(self):
        from ci_yaml import parse_ci, script_lines

        doc = parse_ci(self.PIPELINE)
        assert list(script_lines(doc["deploy-acme"], doc)) == [
            "ansible-playbook playbooks/acme.yml"
        ]

    def test_the_collection_pin_gate_reads_the_jobs_document(self, tmp_path):
        pin = load_script("check-collection-pin-trigger.py")
        (tmp_path / ".gitlab-ci.yml").write_text(self.PIPELINE)
        assert set(pin._load_ci(tmp_path / ".gitlab-ci.yml")) == {
            "variables", ".acme-script", "deploy-acme"
        }

    def test_the_molecule_pin_gate_reads_the_ref_past_the_header(self, tmp_path):
        pin = load_script("check-molecule-image-pin.py")
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(self.PIPELINE)
        assert pin.declared_ref(ci) == "v1.2.3"

    def test_the_matrix_coverage_gate_reads_the_jobs_document(self, tmp_path):
        """The gate reads the jobs document of a two-document pipeline file."""
        ci = tmp_path / "integration-jobs.yml"
        ci.write_text(self.PIPELINE)
        (tmp_path / "acme").mkdir()
        res = _run([
            sys.executable, str(SCRIPTS / "check-integration-matrix-coverage.py"),
            "--ci-file", str(ci), "--integration-dir", str(tmp_path),
            "--integration-job", "deploy-acme",
        ])
        assert "Traceback" not in res.stderr, res.stderr
        assert "has no job" not in res.stderr, res.stderr


class TestCollectionInstallHonoursThePin:
    """`ansible-galaxy` skips an already-installed collection.

    Without --force a warm dependency cache keeps the previous weisssrv.infra
    release after a pin bump, so the job runs against the pre-bump roles.
    """

    # Only the installs that read the pin file itself; the coordinated-bump path
    # installs a stripped temp copy and force-installs the checkout next. Matched
    # to end of line, because --force is as often written after `-r` as before.
    INSTALL = re.compile(
        r"ansible-galaxy\s+collection\s+install\b[^\n]*\brequirements\.yml\b[^\n]*"
    )
    SOURCES = (".gitlab-ci.yml", "taskfiles/ansible.yml")

    def _invocations(self) -> list[tuple[str, str]]:
        found = []
        for rel in self.SOURCES:
            text = (REPO / rel).read_text(encoding="utf-8")
            found += [(rel, m.group(0)) for m in self.INSTALL.finditer(text)]
        assert found, (
            "no `ansible-galaxy collection install` reading requirements.yml was "
            f"found in {', '.join(self.SOURCES)} — this check inspected nothing"
        )
        return found

    def test_every_pinned_install_forces_a_refresh(self):
        unforced = [
            f"{rel}: {call}" for rel, call in self._invocations()
            if "--force" not in call
        ]
        assert not unforced, (
            "these installs read ansible/requirements.yml without --force, so a "
            "warm cache keeps the pre-bump collection:\n  "
            + "\n  ".join(unforced)
        )

    def test_the_rule_rejects_an_unforced_install(self):
        """Mutation proof: the pattern must match the form it exists to ban."""
        match = self.INSTALL.search(
            "    - ansible-galaxy collection install -r ansible/requirements.yml\n"
        )
        assert match and "--force" not in match.group(0)


# validation-gate membership
# Every deploy-* job declares `needs: [validation-gate]`, so membership of that
# gate's needs is the only thing standing between a red check and a deploy.

# Stages that run before `gate`, where a non-advisory job must block the deploy.
GATED_STAGES = {"lint", "validate", "test", "build", "security"}
# GitLab's default when a job names no stage.
DEFAULT_STAGE = "test"
# Advisory jobs, exempt because allow_failure means they can never block.
# The three terraform drift plans come from a library include, so they are not
# in this document at all and the sweep below never sees them.
ADVISORY_JOBS = (
    "b2-drift-plan", "cluster-drift-plan", "unifi-settings-drift",
)


def _inherited(doc: dict, name: str, key: str, seen: frozenset = frozenset()):
    """A job's effective value for `key`, following `extends` as GitLab does."""
    job = doc.get(name)
    if not isinstance(job, dict) or name in seen:
        return None
    if key in job:
        return job[key]
    parents = job.get("extends") or []
    if isinstance(parents, str):
        parents = [parents]
    for parent in parents:
        value = _inherited(doc, parent, key, seen | {name})
        if value is not None:
            return value
    return None


def _gate_needs(doc: dict) -> set[str]:
    needs = (doc.get("validation-gate") or {}).get("needs") or []
    return {need["job"] if isinstance(need, dict) else str(need) for need in needs}


def ungated_blocking_jobs(doc: dict) -> list[str]:
    """Blocking pre-deploy jobs absent from validation-gate's `needs`."""
    needs = _gate_needs(doc)
    out = []
    for name, job in doc.items():
        if not isinstance(job, dict) or name.startswith(".") or name == "validation-gate":
            continue
        if _inherited(doc, name, "script") is None and _inherited(doc, name, "trigger") is None:
            continue
        if (_inherited(doc, name, "stage") or DEFAULT_STAGE) not in GATED_STAGES:
            continue
        if _inherited(doc, name, "allow_failure"):
            continue
        if name not in needs:
            out.append(name)
    return sorted(out)


class TestValidationGateMembership:
    @staticmethod
    def _doc() -> dict:
        return load_ci_doc(REPO / ".gitlab-ci.yml")

    def test_every_blocking_pre_deploy_job_is_in_the_gate(self):
        ungated = ungated_blocking_jobs(self._doc())
        assert not ungated, (
            "these jobs run before the gate and can fail the pipeline, but "
            "validation-gate does not need them, so the deploy stage runs "
            f"regardless: {ungated}"
        )

    def test_the_advisory_exemption_is_earned_by_allow_failure(self):
        """The exemption is derived, so a job losing allow_failure is gated."""
        doc = self._doc()
        for name in ADVISORY_JOBS:
            assert name in doc, f"{name} is gone; drop it from ADVISORY_JOBS"
            assert _inherited(doc, name, "allow_failure"), (
                f"{name} no longer sets allow_failure, so it must join "
                "validation-gate's needs"
            )

    def test_the_gate_lists_jobs_this_pipeline_defines(self):
        """A gate whose needs name nothing local would pass the check vacuously."""
        doc = self._doc()
        assert _gate_needs(doc) & set(doc), "validation-gate needs no local job"

    def test_an_ungated_lint_job_is_detected(self):
        doc = {
            "validation-gate": {"stage": "gate", "needs": [{"job": "yaml-lint"}]},
            "yaml-lint": {"stage": "lint", "script": ["yamllint ."]},
            "new-lint": {"stage": "lint", "script": ["true"]},
            "advisory-lint": {"stage": "lint", "script": ["true"], "allow_failure": True},
        }
        assert ungated_blocking_jobs(doc) == ["new-lint"]


# validation-gate's `optional: true` needs
# Most of those names come from a library include, so a moved `job_name`
# default leaves the need matching no job and the gate waits on nothing.

# Top-level keys GitLab reserves; every other key in a jobs document is a job.
CI_RESERVED = frozenset({
    "default", "include", "image", "services", "stages", "variables", "workflow",
    "before_script", "after_script", "cache", "spec",
})
INPUT_REF = re.compile(r"\$\[\[\s*inputs\.([A-Za-z0-9_]+)\s*\]\]")


def template_keys(text: str, passed: dict | None = None) -> set[str]:
    """Top-level names a CI template creates, `$[[ inputs.x ]]` resolved from
    the passed inputs over the template's own spec defaults. Jobs and hidden
    fragments both, because a consumer `extends:` the fragments."""
    from test_vendored_byte_identity import _CILoader

    docs = [d for d in yaml.load_all(text, Loader=_CILoader) if isinstance(d, dict)]
    if not docs:
        return set()
    inputs = {}
    spec = docs[0].get("spec") if "spec" in docs[0] else None
    if isinstance(spec, dict):
        for name, decl in (spec.get("inputs") or {}).items():
            inputs[name] = decl.get("default") if isinstance(decl, dict) else None
    inputs.update(passed or {})
    names = set()
    for key in docs[-1]:
        if not isinstance(key, str) or key in CI_RESERVED:
            continue
        resolved = INPUT_REF.sub(lambda m: str(inputs.get(m.group(1)) or ""), key)
        if resolved:
            names.add(resolved)
    return names


def template_job_names(text: str, passed: dict | None = None) -> set[str]:
    """Job names a CI template creates. A `.`-prefixed name is a fragment."""
    return {n for n in template_keys(text, passed) if not n.startswith(".")}


def template_fragment_names(text: str, passed: dict | None = None) -> set[str]:
    """Hidden-job fragment names a CI template creates."""
    return {n for n in template_keys(text, passed) if n.startswith(".")}


def _lib_ci_text(relpath: str) -> str:
    """One library CI file at the pinned ref, else from the checkout's tree."""
    from test_vendored_byte_identity import _lib_root, _pinned_ref, _ref_available

    lib, ref = _lib_root(), _pinned_ref()
    if _ref_available(lib, ref):
        return subprocess.run(
            ["git", "-C", str(lib), "show", f"{ref}:{relpath}"],
            capture_output=True, text=True, check=True,
        ).stdout
    return (lib / relpath).read_text(encoding="utf-8")


def _resolvable(doc: dict, select) -> dict[str, str]:
    """Top-level names this pipeline can create, mapped to where they come from:
    the local file, an included local file, or a library template."""
    found = {
        name: ".gitlab-ci.yml"
        for name in doc
        if isinstance(name, str) and select(name) and name not in CI_RESERVED
    }
    for entry in doc.get("include") or []:
        if not isinstance(entry, dict):
            continue
        if "local" in entry:
            relpath = str(entry["local"]).lstrip("/")
            keys = template_keys((REPO / relpath).read_text(encoding="utf-8"))
            found.update(dict.fromkeys({k for k in keys if select(k)}, relpath))
            continue
        files = entry.get("file") or []
        if isinstance(files, str):
            files = [files]
        for spec_file in files:
            relpath = str(spec_file).lstrip("/")
            keys = template_keys(_lib_ci_text(relpath), entry.get("inputs"))
            found.update(dict.fromkeys({k for k in keys if select(k)}, relpath))
    return found


def resolvable_job_names(doc: dict) -> dict[str, str]:
    """Every job name this pipeline can create, mapped to its source."""
    return _resolvable(doc, lambda name: not name.startswith("."))


def resolvable_fragment_names(doc: dict) -> dict[str, str]:
    """Every hidden-job fragment this pipeline can `extends:`, mapped to its source."""
    return _resolvable(doc, lambda name: name.startswith("."))


def optional_needs(doc: dict, job: str = "validation-gate") -> list[str]:
    """`optional: true` entries in one job's `needs`."""
    return [
        need["job"] for need in (doc.get(job) or {}).get("needs") or []
        if isinstance(need, dict) and need.get("optional") and need.get("job")
    ]


def unresolved_optional_needs(doc: dict, known: dict[str, str]) -> list[str]:
    """Optional needs naming a job nothing in this pipeline creates."""
    return sorted(name for name in optional_needs(doc) if name not in known)


class TestOptionalNeedsResolve:
    @staticmethod
    def _doc() -> dict:
        return load_ci_doc(REPO / ".gitlab-ci.yml")

    def test_every_optional_need_names_a_job_something_creates(self):
        from test_vendored_byte_identity import _pinned_ref

        doc = self._doc()
        needs = optional_needs(doc)
        assert needs, "validation-gate declares no optional need — this gate read nothing"
        known = resolvable_job_names(doc)
        unresolved = unresolved_optional_needs(doc, known)
        assert not unresolved, (
            "these validation-gate needs name no job this pipeline creates, so "
            "the gate waits on nothing and a red check cannot block the deploy: "
            f"{unresolved}. An include's `job_name` default may have moved at "
            f"{_pinned_ref()}."
        )

    def test_the_library_includes_are_what_create_most_of_them(self):
        """A resolution that found only local jobs would pass vacuously."""
        known = resolvable_job_names(self._doc())
        from_lib = {name for name, src in known.items() if src.startswith("ci/")}
        assert from_lib & set(optional_needs(self._doc())), (
            "no optional need resolves to a library template — the include "
            "resolution stopped working"
        )

    def test_a_need_naming_no_job_is_detected(self):
        doc = {"validation-gate": {"needs": [
            {"job": "yaml-lint", "optional": True},
            {"job": "renamed-lint", "optional": True},
        ]}}
        assert unresolved_optional_needs(doc, {"yaml-lint": "ci/lint/yaml-lint.yml"}) == [
            "renamed-lint"
        ]

    def test_a_moved_job_name_default_stops_resolving(self):
        """The mutation the gate exists for: the template renames its job."""
        template = (
            "spec:\n  inputs:\n    job_name:\n      default: yaml-lint\n"
            "---\n"
            '"$[[ inputs.job_name ]]":\n  script:\n    - yamllint .\n'
        )
        assert template_job_names(template) == {"yaml-lint"}
        renamed = template.replace("default: yaml-lint", "default: yamllint")
        assert template_job_names(renamed) == {"yamllint"}
        assert template_job_names(template, {"job_name": "yaml-lint-custom"}) == {
            "yaml-lint-custom"
        }

    def test_a_fragment_is_not_a_job(self):
        template = (
            "spec:\n  inputs:\n    base_name:\n      default: .deploy-base\n"
            "---\n"
            '"$[[ inputs.base_name ]]":\n  script:\n    - true\n'
        )
        assert template_job_names(template) == set()


# `extends:` target resolution
# A target nothing defines drops the whole inherited body — tags, rules, image —
# and the job then runs on the default runner under the pipeline's default rules.


def extends_targets(job: dict) -> list[str]:
    """One job's `extends:` entries, in either the scalar or list spelling."""
    targets = job.get("extends") or []
    return [str(t) for t in ([targets] if isinstance(targets, str) else targets)]


def unknown_extends_targets(doc: dict, known: dict[str, str]) -> list[str]:
    """`job -> target` pairs naming a fragment nothing in this pipeline defines."""
    missing = []
    for name, job in doc.items():
        if not isinstance(name, str) or not isinstance(job, dict):
            continue
        for target in extends_targets(job):
            if target not in doc and target not in known:
                missing.append(f"{name} -> {target}")
    return sorted(missing)


def extends_cycles(doc: dict) -> list[str]:
    """Jobs whose local `extends:` chain loops, which GitLab refuses outright."""
    cycles = []

    def walk(name: str, seen: tuple[str, ...]) -> bool:
        if name in seen:
            return True
        job = doc.get(name)
        if not isinstance(job, dict):
            return False
        return any(walk(target, seen + (name,)) for target in extends_targets(job))

    for name, job in doc.items():
        if isinstance(name, str) and isinstance(job, dict) and walk(name, ()):
            cycles.append(name)
    return sorted(cycles)


class TestExtendsTargetsResolve:
    @staticmethod
    def _doc() -> dict:
        return load_ci_doc(REPO / ".gitlab-ci.yml")

    def test_every_extends_target_is_defined(self):
        doc = self._doc()
        unknown = unknown_extends_targets(doc, resolvable_fragment_names(doc))
        assert not unknown, (
            "these jobs extend a fragment nothing in this pipeline defines, so "
            "the inherited tags, rules and image are silently dropped: "
            f"{unknown}"
        )

    def test_the_library_includes_are_what_define_some_of_them(self):
        """A resolution that found only local fragments would pass vacuously."""
        doc = self._doc()
        known = resolvable_fragment_names(doc)
        extended = {t for job in doc.values() if isinstance(job, dict)
                    for t in extends_targets(job)}
        from_lib = {name for name, src in known.items() if src.startswith("ci/")}
        assert from_lib & extended, (
            "no `extends:` target resolves to a library template — the include "
            "resolution stopped working"
        )

    def test_no_extends_chain_loops(self):
        assert extends_cycles(self._doc()) == []

    def test_a_typod_target_is_detected(self):
        doc = {
            "deploy-ansible-base": {"extends": ".deploy-bse"},
            "build-image": {"extends": [".build-image-base", ".dep-cache"]},
            ".build-image-base": {"image": "docker"},
        }
        assert unknown_extends_targets(doc, {".dep-cache": "ci/templates/dep-cache.yml"}) == [
            "deploy-ansible-base -> .deploy-bse"
        ]

    def test_a_cycle_is_detected(self):
        doc = {".a": {"extends": ".b"}, ".b": {"extends": ".a"}, "job": {"extends": ".a"}}
        assert extends_cycles(doc) == [".a", ".b", "job"]

    def test_a_fragment_renamed_in_a_template_stops_resolving(self):
        """The mutation the gate exists for: the template renames its fragment."""
        template = (
            "spec:\n  inputs:\n    fragment_name:\n      default: .deploy-base\n"
            "---\n"
            '"$[[ inputs.fragment_name ]]":\n  script:\n    - true\n'
        )
        assert template_fragment_names(template) == {".deploy-base"}
        renamed = template.replace("default: .deploy-base", "default: .ansible-deploy-base")
        assert template_fragment_names(renamed) == {".ansible-deploy-base"}


# python-tests coverage of the pytest suites
# The suites run only because the python-tests include inherits the library's
# `test_dir` default: a suite outside it stops running in CI with no signal.

PYTHON_TESTS_TEMPLATE = "ci/test/python-tests.yml"
TASKFILE_PYTEST_CMD = "python3 -m pytest scripts/ -v"


def template_input(text: str, name: str, passed: dict | None = None):
    """A template input's effective value: what the consumer passes, else the
    template's own spec default."""
    from test_vendored_byte_identity import _CILoader

    if passed and name in passed:
        return passed[name]
    docs = [d for d in yaml.load_all(text, Loader=_CILoader) if isinstance(d, dict)]
    spec = docs[0].get("spec") if docs and "spec" in docs[0] else None
    decl = ((spec or {}).get("inputs") or {}).get(name)
    return decl.get("default") if isinstance(decl, dict) else None


def _python_tests_include(doc: dict) -> dict:
    for entry in doc.get("include") or []:
        if not isinstance(entry, dict):
            continue
        files = entry.get("file") or []
        if isinstance(files, str):
            files = [files]
        if any(str(f).lstrip("/") == PYTHON_TESTS_TEMPLATE for f in files):
            return entry
    raise AssertionError(f"no include of {PYTHON_TESTS_TEMPLATE} — this gate read nothing")


def suites_outside(test_dir: str, tracked: list[str]) -> list[str]:
    """Tracked pytest suites the `test_dir` prefix does not reach."""
    prefix = test_dir.strip().rstrip("/") + "/"
    return sorted(p for p in tracked if not p.startswith(prefix))


class TestPytestSuiteCoverage:
    @staticmethod
    def _tracked_suites() -> list[str]:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--", "*/test_*.py", "test_*.py"],
            capture_output=True, text=True, check=True,
        ).stdout
        return [line for line in out.splitlines() if line]

    def test_every_suite_lives_where_ci_runs_pytest(self):
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        entry = _python_tests_include(doc)
        test_dir = template_input(
            _lib_ci_text(PYTHON_TESTS_TEMPLATE), "test_dir", entry.get("inputs")
        )
        assert test_dir, (
            f"{PYTHON_TESTS_TEMPLATE} declares no `test_dir` default and the "
            "include passes none, so the pytest target is unknown"
        )
        tracked = self._tracked_suites()
        assert tracked, "no tracked test_*.py found — this gate read nothing"
        outside = suites_outside(str(test_dir), tracked)
        assert not outside, (
            f"python-tests runs `pytest {test_dir}`, so these suites never run "
            f"in CI: {outside}. Move them under {test_dir} or pass a wider "
            "`test_dir` to the include."
        )

    def test_the_task_runner_targets_the_same_directory(self):
        """`task scripts:test` and the CI job must not drift apart."""
        doc = load_ci_doc(REPO / ".gitlab-ci.yml")
        test_dir = template_input(
            _lib_ci_text(PYTHON_TESTS_TEMPLATE), "test_dir",
            _python_tests_include(doc).get("inputs"),
        )
        taskfile = (REPO / "Taskfile.yml").read_text(encoding="utf-8")
        assert TASKFILE_PYTEST_CMD in taskfile, (
            f"`task scripts:test` no longer runs {TASKFILE_PYTEST_CMD!r}; keep it "
            "and the CI `test_dir` in step"
        )
        assert str(test_dir).strip().rstrip("/") == "scripts", (
            f"CI runs `pytest {test_dir}` while `task scripts:test` runs "
            f"{TASKFILE_PYTEST_CMD!r} — one of them is not running the suite"
        )

    def test_a_suite_outside_the_test_dir_is_detected(self):
        assert suites_outside("scripts/", [
            "scripts/test_a.py", "tests/test_b.py", "kubernetes/test_c.py",
        ]) == ["kubernetes/test_c.py", "tests/test_b.py"]

    def test_a_narrowed_test_dir_is_detected(self):
        """The mutation the gate exists for: the include passes a subdirectory."""
        template = "spec:\n  inputs:\n    test_dir:\n      default: \"scripts/\"\n---\n{}\n"
        assert template_input(template, "test_dir") == "scripts/"
        assert template_input(template, "test_dir", {"test_dir": "scripts/gates/"}) == (
            "scripts/gates/"
        )
        assert suites_outside("scripts/gates/", ["scripts/test_a.py"]) == [
            "scripts/test_a.py"
        ]


# Build-stage ordering
# `build` sits after `security` so no image is pushed before Secret Detection
# runs; a `needs:` on either build job makes it ignore stage order.
BUILD_JOBS = ("build-hermes-agent", "build-camofox-browser")


def build_runs_after(stages: list[str], gate: str = "security") -> bool:
    """True when the `build` stage is ordered after `gate`."""
    return stages.index("build") > stages.index(gate)


class TestBuildStageRunsAfterSecurity:
    @staticmethod
    def _doc() -> dict:
        return load_ci_doc(REPO / ".gitlab-ci.yml")

    def test_build_is_ordered_after_the_scanning_stages(self):
        stages = self._doc()["stages"]
        for gate in ("lint", "validate", "test", "security"):
            assert gate in stages, f"the {gate} stage is gone; this gate read nothing"
        assert build_runs_after(stages), (
            "the `build` stage no longer runs after `security`, so the "
            "privileged DinD jobs push images to the registry before Secret "
            f"Detection has run: {stages}"
        )

    def test_neither_build_job_declares_needs(self):
        doc = self._doc()
        for name in BUILD_JOBS:
            assert name in doc, f"{name} is gone; drop it from BUILD_JOBS"
            assert "needs" not in doc[name], (
                f"{name} declares `needs:`, which makes it ignore stage order "
                "and publish its image before the scanning stages run"
            )

    def test_a_reordered_stage_list_is_detected(self):
        assert not build_runs_after(["lint", "build", "security", "deploy"])
        assert build_runs_after(["lint", "security", "build", "deploy"])


# Tool provisioning for the jobs that touch live infrastructure
# A script that calls a binary no job image ships, and that the job's
# before_script never installs, fails at runtime rather than at lint.

# Tools a job has to install, because no image in this pipeline ships them. A
# name outside this set (shell keywords, coreutils) is never asked about, so a
# newly-used tool enters the gate by being added here.
PROVISIONED_TOOLS = frozenset({
    "amtool", "ansible", "ansible-galaxy", "ansible-lint", "ansible-playbook",
    "curl", "dig", "envsubst", "flux", "git", "gpg", "helm", "jq", "kubeconform",
    "kubectl", "kustomize", "nc", "nslookup", "op", "promtool", "shellcheck",
    "ssh", "task", "terraform", "yamllint", "yq",
})

# Package -> what it puts on PATH, for `apt-get install` and `apk add`.
PACKAGE_TOOLS = {
    "1password-cli": {"op"}, "bind-tools": {"dig", "nslookup"}, "curl": {"curl"},
    "dnsutils": {"dig", "nslookup"}, "gettext": {"envsubst"},
    "gettext-base": {"envsubst"}, "git": {"git"}, "gnupg": {"gpg"}, "jq": {"jq"},
    "netcat-openbsd": {"nc"}, "openssh": {"ssh"}, "openssh-client": {"ssh"},
}

# pip requirement -> the CLIs it installs.
PIP_TOOLS = {
    "ansible": {"ansible", "ansible-galaxy", "ansible-playbook"},
    "ansible-core": {"ansible", "ansible-galaxy", "ansible-playbook"},
    "ansible-lint": {"ansible-lint"}, "yamllint": {"yamllint"},
}

# Third-party module a gate imports -> the pip requirements that satisfy it.
# ansible pulls PyYAML in, which is why the maintenance jobs need no pyyaml pin.
PIP_MODULES = {"yaml": {"pyyaml", "ansible", "ansible-core"}, "requests": {"requests"}}

# What each job image ships. An image absent here provides nothing from this
# vocabulary, so a job moved onto a new image installs what its scripts call.
IMAGE_TOOLS = {
    "python:3.11-slim": frozenset(),
    "python:3.13-slim": frozenset(),
    "python:3.13": frozenset({"curl", "git", "ssh"}),
    "hashicorp/terraform:1.16.5": frozenset({"terraform", "git", "ssh"}),
}

# A tool a sourced helper offers on a branch the CI caller never takes, keyed
# "<script>:<tool>" with why the job need not install it.
OFF_CI_PATH = {
    "smoke-lib.sh:ssh": "verify-gitlab.sh calls no ssh probe",
}

# Fragments whose descendants deploy, verify or maintain the live estate.
LIVE_BASES = (".deploy-base", ".k3s-deploy-base", ".maintenance-base")
LIVE_STAGES = ("deploy", "verify", "maintenance")
# Anchors, so a rename cannot leave the gate inspecting an empty job set.
LIVE_ANCHORS = ("cluster-drift-plan", "deploy-verify", "maintenance-verify")

_TOOL_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
# Command-position separators. Quoting is not tracked, so a `bash -c '...; ...'`
# payload splits like any other list and its commands are seen.
_SEGMENT = re.compile(r"\|\||&&|\$\(|<<<|[|;()`&{}\n]")
_SHELL_WORDS = frozenset({
    "case", "do", "done", "elif", "else", "esac", "env", "eval", "exec", "fi",
    "for", "if", "in", "nohup", "sudo", "then", "time", "until", "while", "!",
})
_SCRIPT_REF = re.compile(
    r"(?:scripts/|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/)([A-Za-z0-9_.-]+\.(?:sh|py))"
)
_VAR_REF = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)
_INPUT_REF = re.compile(r"\$\[\[\s*inputs\.([A-Za-z0-9_]+)\s*\]\]")


def strip_shell_comments(text: str) -> str:
    """Shell text with `#` comments dropped, so prose naming a tool is not read
    as a call. A `#` inside a quoted string goes with them."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


def shell_commands(text: str) -> set:
    """Tools from PROVISIONED_TOOLS the text calls: the first word of each
    command segment, plus the word after `--` and after `command -v`. A command
    handed to another runtime (`kubectl run -- sh -c ...`) is not counted."""
    found = set()
    for segment in _SEGMENT.split(strip_shell_comments(text)):
        words = [word.strip("\"'") for word in segment.split()]
        head = 0
        while head < len(words) and (
            words[head] in _SHELL_WORDS
            or ("=" in words[head] and not words[head].startswith("-"))
        ):
            head += 1
        if head < len(words) and _TOOL_TOKEN.fullmatch(words[head]):
            found.add(words[head])
        for index, word in enumerate(words[:-1]):
            nxt = words[index + 1]
            if word == "--" and _TOOL_TOKEN.fullmatch(nxt):
                found.add(nxt)
            if word == "command" and nxt == "-v" and index + 2 < len(words):
                if _TOOL_TOKEN.fullmatch(words[index + 2]):
                    found.add(words[index + 2])
    return found & PROVISIONED_TOOLS


def shell_installs(text: str) -> set:
    """What the text puts on PATH: package installs, pip requirements, a
    verified binary dropped in /usr/local/bin, a ci-fetch-tools.py fetch.
    Modules appear as `py:<module>`; install ordering is not modelled."""
    got = set()
    body = strip_shell_comments(text)
    pattern = r"(?:apt-get|apk)\s+(?:install|add)((?:\s+-{1,2}[A-Za-z-]+)*(?:\s+[^\n;&|]*))"
    for match in re.finditer(pattern, body):
        for word in match.group(1).split():
            if not word.startswith("-"):
                got |= PACKAGE_TOOLS.get(word.strip("\"'"), set())
    for match in re.finditer(r"pip\s+install([^\n;&|]*)", body):
        for word in match.group(1).split():
            if word.startswith("-") or word.startswith("$"):
                continue
            name = re.split(r"[=<>!~\[]", word.strip("\"'"))[0].strip().lower()
            got |= PIP_TOOLS.get(name, set())
            got |= {f"py:{mod}" for mod, reqs in PIP_MODULES.items() if name in reqs}
    for match in re.finditer(r"install\s+-m\s+\S+\s+\S+\s+/usr/local/bin/(\S+)", body):
        got.add(match.group(1))
    for match in re.finditer(r"tar\s+[a-z]+\s+\S+\s+-C\s+/usr/local/bin\s+(\S+)", body):
        got.add(match.group(1))
    for match in re.finditer(r"ci-fetch-tools\.py([^\n;&|'\"]*)", body):
        got |= {word for word in match.group(1).split() if not word.startswith("-")}
    if "1password-cli" in body:
        got.add("op")
    return got


def unprovisioned(image: str, text: str, also_required=(), excused=()) -> list:
    """Tools and modules the job needs and nothing in it provides."""
    provided = set(IMAGE_TOOLS.get(image, frozenset())) | shell_installs(text)
    required = (shell_commands(text) | set(also_required)) - set(excused)
    return sorted(required - provided)


def python_modules(path, seen=None) -> set:
    """Third-party modules a gate needs, as `py:<module>`, following the sibling
    modules it imports — gate_common.py is where a `kubectl -o json` gate picks
    PyYAML up."""
    import ast

    path = Path(path)
    seen = set() if seen is None else seen
    if not path.is_file() or path in seen:
        return set()
    seen.add(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".")[0])
    need = set()
    for name in names:
        sibling = path.parent / f"{name}.py"
        if sibling.is_file():
            need |= python_modules(sibling, seen)
        elif name in PIP_MODULES:
            need.add(f"py:{name}")
    return need


def script_closure(names, seen=None, found=None) -> dict:
    """Every repo script reached from `names`: the shell text to scan, the
    modules its python gates import, and the off-path tools to excuse."""
    seen = set() if seen is None else seen
    found = {"text": [], "modules": set(), "excused": set()} if found is None else found
    for name in sorted(names):
        path = SCRIPTS / name
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        if name.endswith(".py"):
            found["modules"] |= python_modules(path)
            continue
        text = path.read_text(encoding="utf-8")
        found["text"].append(text)
        used = shell_commands(text)
        found["excused"] |= {
            tool for tool in used if f"{name}:{tool}" in OFF_CI_PATH
        }
        script_closure(set(_SCRIPT_REF.findall(strip_shell_comments(text))), seen, found)
    return found


def _substitute_inputs(node, inputs: dict):
    """A library template with its `$[[ inputs.x ]]` resolved."""
    from ci_yaml import Reference

    if isinstance(node, str):
        return _INPUT_REF.sub(
            lambda m: "" if inputs.get(m.group(1)) is None else str(inputs[m.group(1)]),
            node,
        )
    if isinstance(node, Reference):
        return Reference([_substitute_inputs(item, inputs) for item in node])
    if isinstance(node, list):
        return [_substitute_inputs(item, inputs) for item in node]
    if isinstance(node, dict):
        return {
            _substitute_inputs(key, inputs): _substitute_inputs(value, inputs)
            for key, value in node.items()
        }
    return node


def library_fragments(doc: dict) -> dict:
    """The hidden jobs the library include: block contributes, so a `!reference`
    into one resolves to the fragment this pipeline actually gets."""
    from ci_yaml import CILoader

    frags = {}
    for entry in doc.get("include") or []:
        if not isinstance(entry, dict) or "file" not in entry:
            continue
        files = entry["file"] if isinstance(entry["file"], list) else [entry["file"]]
        for spec_file in files:
            text = _lib_ci_text(str(spec_file).lstrip("/"))
            docs = [d for d in yaml.load_all(text, Loader=CILoader) if isinstance(d, dict)]
            if not docs:
                continue
            inputs = {}
            if "spec" in docs[0]:
                for name, decl in ((docs[0].get("spec") or {}).get("inputs") or {}).items():
                    inputs[name] = decl.get("default") if isinstance(decl, dict) else None
            inputs.update(entry.get("inputs") or {})
            for key, body in docs[-1].items():
                if not isinstance(key, str) or not isinstance(body, dict):
                    continue
                name = _substitute_inputs(key, inputs)
                if name:
                    frags.setdefault(name, _substitute_inputs(body, inputs))
    return frags


def pipeline_doc() -> dict:
    """.gitlab-ci.yml with `!reference` kept and the library fragments merged in."""
    from ci_yaml import load_ci

    doc = load_ci(REPO / ".gitlab-ci.yml")
    for name, body in library_fragments(doc).items():
        doc.setdefault(name, body)
    return doc


def extends_chain(doc: dict, name: str, seen=()) -> list:
    """A job's `extends` ancestry, least derived first."""
    body = doc.get(name)
    if not isinstance(body, dict) or name in seen:
        return []
    parents = body.get("extends")
    parents = [parents] if isinstance(parents, str) else (parents or [])
    chain = []
    for parent in parents:
        chain += extends_chain(doc, parent, seen + (name,))
    return chain + [name]


def job_shell(doc: dict, name: str) -> tuple:
    """A job's image and the shell it runs. A job-level `before_script` replaces
    the inherited one, so the most derived definition of each block wins."""
    from ci_yaml import script_lines

    chain = extends_chain(doc, name)
    variables = {k: str(v) for k, v in (doc.get("variables") or {}).items()}
    image = None
    for link in chain:
        variables.update({k: str(v) for k, v in (doc[link].get("variables") or {}).items()})
        if doc[link].get("image"):
            image = doc[link]["image"]
    if isinstance(image, dict):
        image = image.get("name")
    lines = []
    for block in ("before_script", "script", "after_script"):
        for link in reversed(chain):
            if block in doc[link]:
                lines += script_lines(doc[link], doc, block)
                break
    def expand(raw: str) -> str:
        return _VAR_REF.sub(
            lambda m: variables.get(m.group(1) or m.group(2), m.group(0)), raw
        )

    text = "\n".join(lines)
    for _ in range(3):
        text = expand(text)
    return expand(str(image or "")).split("@")[0], text


def live_jobs(doc: dict) -> list:
    """Jobs that deploy, verify or maintain the live estate."""
    return sorted(
        name for name, body in doc.items()
        if isinstance(body, dict) and not name.startswith(".")
        and name not in CI_RESERVED
        and (body.get("stage") in LIVE_STAGES
             or any(base in LIVE_BASES for base in extends_chain(doc, name)))
    )


def job_missing_tools(doc: dict, name: str) -> list:
    image, text = job_shell(doc, name)
    reached = script_closure(set(_SCRIPT_REF.findall(strip_shell_comments(text))))
    return unprovisioned(
        image, "\n".join([text] + reached["text"]),
        also_required=reached["modules"], excused=reached["excused"],
    )


class TestLiveJobToolProvisioning:
    def test_every_live_job_provides_what_its_scripts_call(self):
        doc = pipeline_doc()
        jobs = live_jobs(doc)
        missing = {name: job_missing_tools(doc, name) for name in jobs}
        missing = {name: tools for name, tools in missing.items() if tools}
        assert not missing, (
            "these jobs run a script that calls a tool or imports a module "
            "nothing in the job installs, so they fail at runtime rather than "
            f"at lint: {missing}. Install it in the job's before_script "
            "(scripts/ci-fetch-tools.py for a pinned static binary), or record "
            "it in IMAGE_TOOLS if the job image ships it."
        )

    def test_the_subject_set_covers_the_live_jobs(self):
        doc = pipeline_doc()
        jobs = live_jobs(doc)
        assert len(jobs) > 10, f"only {len(jobs)} live jobs found — the gate read almost nothing"
        for anchor in LIVE_ANCHORS:
            assert anchor in jobs, f"{anchor} is no longer a live job; fix LIVE_BASES/LIVE_STAGES"

    def test_the_library_fragments_resolve(self):
        """`!reference [.kubectl-setup, before_script]` must resolve, or every
        tool the fragment installs reads as absent and the gate is noise."""
        doc = pipeline_doc()
        for fragment in (".deploy-base", ".kubectl-setup", ".install-1password"):
            assert isinstance(doc.get(fragment), dict), (
                f"{fragment} did not resolve from the library include block"
            )
        _, text = job_shell(doc, "deploy-verify")
        assert "kubectl" in shell_installs(text), (
            ".kubectl-setup no longer installs a kubectl the gate can see"
        )

    def test_an_argument_is_not_read_as_a_command(self):
        """The precision the gate depends on: a tool name in an argument
        position is not a call."""
        assert shell_commands('git diff --quiet -- "ansible/inventories/prod/all.yml"') == {"git"}
        assert shell_commands("flux reconcile source git flux-system") == {"flux"}
        assert shell_commands("# run task hosts:sync first") == set()
        assert shell_commands("op run -- ansible-playbook -i inventories/prod site.yml") == {
            "op", "ansible-playbook",
        }

    def test_a_dropped_install_is_detected(self):
        """The mutation: the fragment that installed jq stops installing it."""
        with_jq = 'apt-get install -y -qq jq\nkubectl get pods -o json | jq .items'
        assert unprovisioned("python:3.13-slim", with_jq) == ["kubectl"]
        without_jq = "kubectl get pods -o json | jq .items"
        assert unprovisioned("python:3.13-slim", without_jq) == ["jq", "kubectl"]

    def test_a_dropped_fetch_is_detected(self):
        """The repo's own provisioning path: scripts/ci-fetch-tools.py."""
        fetched = "python3 scripts/ci-fetch-tools.py jq amtool\namtool check-config x\njq ."
        assert unprovisioned("python:3.13-slim", fetched) == []
        assert unprovisioned("python:3.13-slim", "amtool check-config x\njq .") == [
            "amtool", "jq",
        ]

    def test_a_missing_python_module_is_detected(self):
        """The class the drift job hit: a gate imports PyYAML through
        gate_common.py and the slim image ships none."""
        assert unprovisioned("python:3.13-slim", "true", also_required={"py:yaml"}) == [
            "py:yaml"
        ]
        pinned = 'pip install --quiet "pyyaml==6.0.2"'
        assert unprovisioned("python:3.13-slim", pinned, also_required={"py:yaml"}) == []
        assert unprovisioned(
            "python:3.13-slim", 'pip install --quiet "ansible==14.4.0"',
            also_required={"py:yaml"},
        ) == []

    def test_an_off_path_tool_is_excused_only_when_declared(self):
        probe = "nc -z -w 5 host 22"
        assert unprovisioned("python:3.13-slim", probe) == ["nc"]
        assert unprovisioned("python:3.13-slim", probe, excused={"nc"}) == []
        assert set(OFF_CI_PATH) == {"smoke-lib.sh:ssh"}
