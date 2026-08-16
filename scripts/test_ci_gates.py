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
import re
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


class TestPendingAdoptionTable:
    """docs/13's pending-adoption table must agree with weisssrv-lib's registry.

    The doc's previous claim — "Nothing is pending adoption" — outlived three
    library releases that shipped exactly the templates it said did not exist.
    Prose about another repo's state has no other reader, so it is derived from
    that repo here instead.
    """

    DOC = REPO / "docs/13-ci-cd.md"
    MARKER = "**Pending adoption.**"

    @staticmethod
    def _registry() -> dict:
        from test_vendored_byte_identity import _lib_root

        doc = yaml.safe_load((_lib_root() / "docs/CONSUMERS.yml").read_text())
        for consumer in doc["consumers"]:
            if consumer["name"] == "weisssrv":
                return consumer["pins"]["ci_includes"]
        raise AssertionError("weisssrv is not registered in the library's CONSUMERS.yml")

    def _documented(self) -> set[str]:
        """Library paths named in the first column of the pending-adoption table."""
        lines = self.DOC.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(self.MARKER))
        rows: set[str] = set()
        seen_table = False
        for line in lines[start:]:
            if not line.startswith("|"):
                if seen_table:
                    break
                continue
            seen_table = True
            first = line.split("|")[1]
            match = re.search(r"`(/ci/[\w./-]+\.yml)`", first)
            if match:
                rows.add(match.group(1))
        assert rows, "parsed no library paths out of the pending-adoption table"
        return rows

    def test_every_not_yet_adopted_template_is_documented(self):
        pending = set(self._registry().get("not_yet_adopted") or [])
        assert pending, "the library registry lists nothing as not_yet_adopted"
        missing = sorted(pending - self._documented())
        assert not missing, (
            f"weisssrv-lib lists {missing} as not_yet_adopted for this repo, but "
            f"{self.DOC.name}'s pending-adoption table does not mention them."
        )

    def test_the_table_names_no_template_the_library_already_gives_us(self):
        registry = self._registry()
        allowed = set(registry.get("not_yet_adopted") or []) | set(
            registry.get("not_consumed") or []
        )
        stale = sorted(self._documented() - allowed)
        assert not stale, (
            f"{self.DOC.name} lists {stale} as pending, but the library registry has "
            "them neither in not_yet_adopted nor not_consumed — adopt the row or drop it."
        )


class TestPythonTestsFireOnTheirOwnSubjects:
    """The python-tests job's `changes:` list must cover what the gates read.

    Most of scripts/ is drift guards over files OUTSIDE scripts/. A guard whose
    subject matches no `changes:` pattern does not run on the MR that breaks it
    — it runs later, on some unrelated scripts/ edit, and the regression is
    already merged. .gitlab-ci.yml documents that list as hand-maintained from a
    one-off audit-hook trace, which is exactly the kind of list that rots.

    Subjects are derived from the repo-path literals in each gate's source, and
    only paths git actually tracks count — a fixture path a hygiene test invents
    (`terraform/*/plan.out`) is not a subject.
    """

    JOB_TEMPLATE = "/ci/test/python-tests.yml"
    # Top-level trees a literal has to sit under to be a repo path rather than a
    # URL fragment, a label key or a message.
    TREES = ("ansible/", "kubernetes/", "terraform/", "docker/", "docs/",
             "scripts/", ".gitlab/", ".claude/", ".github/")
    ROOT_FILES = {"Taskfile.yml", "README.md", "CLAUDE.md", "AGENTS.md",
                  ".cursorrules", "ruff.toml", ".gitlab-ci.yml"}
    ROOT_TREE = "<root>"
    # Anti-vacuity floor, PER TREE, set just under today's resolved counts
    # (kubernetes 19, ansible 14, scripts 10, root 7, terraform 1, docs 1). A
    # single total was vacuous in the other direction: one tree's subjects can
    # disappear entirely — a gate deleted, its literals rewritten as segmented
    # joins — while the total stays comfortably over any global floor and this
    # guard keeps reporting full coverage of a tree it no longer sees.
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
        from test_site_configs import _CILoader

        doc = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
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

        Single string literals only, by contract: a path assembled from
        segments (`REPO / "kubernetes" / "apps"`, `os.path.join(...)`) and a
        literal containing a glob are both INVISIBLE here — the join never
        produces the whole path as one token, and a glob cannot be resolved
        against `git ls-files`. That is a known blind spot, and it is why the
        per-tree floors below exist: rewriting a gate's literals into joins
        silently empties its tree instead of failing anything.
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
    """`task lint` says it mirrors the CI lint stage; this is what enforces it.

    repo-policy-checks and repo-sync-checks are shell, `task lint` is a YAML
    command tree, and neither reads the other. A gate added to CI alone passes
    every local run right up to the pipeline that rejects the MR.
    """

    # `task lint` commands that are not gates and so need no CI twin.
    NOT_A_GATE = {
        "scripts/resolve-tool.sh": (
            "resolves how to invoke a pyenv/PATH dev tool locally; CI installs "
            "its tools at pinned versions and never resolves one"
        ),
    }

    # Gates the pipeline DOES run, but from somewhere a scan of this repo's
    # .gitlab-ci.yml shell cannot see — a library template's own body, or
    # another test. Each value says where, and the `/ci/...yml` ones are checked
    # against the live include: list below so a dropped include cannot leave a
    # gate exempted for a job that no longer exists.
    CI_RUNS_IT_ELSEWHERE = {
        "scripts/check-doc-links.py": (
            "the library's /ci/lint/docs-link-check.yml job runs it; the script "
            "name lives in that template, not here"
        ),
        "scripts/flux-render.sh": (
            "reached through the flux_render_script input (scripts/flux-env.sh) "
            "of the library's /ci/validate/flux-lint.yml job"
        ),
        "scripts/check-lib-pins.py": (
            "subprocessed by scripts/test_site_configs.py, which the "
            "python-tests job runs"
        ),
    }

    # run_check entries whose command is an inline shell function rather than a
    # script — each maps to the `task lint` sub-task that runs the same logic.
    INLINE_CHECKS = {
        "flux-version-pin": "lint:flux-version-pin",
        "busybox-version-pin": "lint:busybox-version-pin",
        "tailscale-policy-syntax": "lint:tailscale-policy",
        "taskfile-smoke": "lint:taskfile-smoke",
        "hosts-env-sync": "lint:sync-checks",
        "flux-versions-sync": "lint:sync-checks",
    }

    @staticmethod
    def _ci_jobs() -> dict:
        from test_site_configs import _CILoader

        return yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}

    def _run_checks(self) -> dict[str, str]:
        """{check name: the rest of its run_check line} across both gate jobs."""
        found: dict[str, str] = {}
        jobs = self._ci_jobs()
        for job in ("repo-policy-checks", "repo-sync-checks"):
            script = jobs[job]["script"]
            body = "\n".join(script if isinstance(script, list) else [script])
            for match in re.finditer(r"^\s*run_check\s+(\S+)\s+(.*)$", body, re.M):
                found[match.group(1)] = match.group(2)
        assert found, "parsed no run_check entries out of the CI gate jobs"
        return found

    @staticmethod
    def _lint_tree() -> tuple[str, set[str]]:
        """(every command string reachable from `task lint`, the task names walked)."""
        tasks = yaml.safe_load((REPO / "Taskfile.yml").read_text())["tasks"]
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
        """Every shell string the pipeline EXECUTES, comments removed.

        Job script bodies plus the include inputs that are shell
        (extra_validation, *_command). Deliberately not the raw file: a
        `changes:` path or a comment naming a gate is not a run of it, and
        counting either would let a real one-sided gate read as covered.
        Parsing the YAML drops the file's own comments; the `#` strip removes
        the shell comments that survive inside a script body.
        """
        from test_site_configs import _CILoader

        doc = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
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

        Two ways it reaches one: named in a shell string it runs, or run through
        `task <name>` from an inline function — this repo's idiom for a gate
        with one implementation (check_tailscale_policy), which hides the script
        name from a literal scan.
        """
        shell = cls._ci_shell()
        reachable = set(re.findall(r"scripts/[\w.-]+\.(?:py|sh)", shell))
        tasks = yaml.safe_load((REPO / "Taskfile.yml").read_text())["tasks"]
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
            "repo-policy-checks, or the flux-lint extra_validation input for a "
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
