"""The site data the vendored scripts read.

Externalising the registries and policies out of the library scripts moved the
site-specific half into config files — which is the half the library's own
suites cannot test. These assert the shapes those scripts assume, plus the
handful of whole-repo invariants that only hold here (the CI file passes the
pin gate, hosts.env is in sync with the inventory).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

KNOWN_CATEGORIES = {
    "github", "dockerhub", "ghcr", "lsio", "helm",
    "gitlab", "plex", "apt_repo", "manual",
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").removesuffix(".py"), SCRIPTS / name
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def registry() -> dict:
    return _load("version-registry.py").CONFIG


class TestVersionRegistry:
    def test_vars_file_and_aliases_exist(self, registry):
        assert (REPO / registry["vars_file"]).is_file()
        for alias, rel in registry["version_file_aliases"].items():
            assert (REPO / rel).is_file(), f"version_file_aliases[{alias}] -> missing {rel}"

    def test_entries_are_well_formed(self, registry):
        problems = []
        for svc in registry["services"]:
            if svc["category"] not in KNOWN_CATEGORIES:
                problems.append(f"{svc['name']}: unknown category {svc['category']!r}")
            if svc.get("held") and not svc.get("notes"):
                problems.append(f"{svc['name']}: held without a note saying why")
        assert not problems, problems

    def test_identifiers_are_unique(self, registry):
        """--service and --update resolve by name and by var_name, so a
        duplicate silently makes one entry unreachable."""
        for field in ("name", "var_name"):
            seen = [svc[field] for svc in registry["services"]]
            dupes = sorted({v for v in seen if seen.count(v) > 1})
            assert not dupes, f"duplicate {field}: {dupes}"

    def test_version_file_pins_point_at_real_files(self, registry):
        aliases = registry["version_file_aliases"]
        for svc in registry["services"]:
            version_file = svc.get("version_file")
            if not version_file:
                continue
            paths = [version_file] if isinstance(version_file, str) else version_file
            for path in paths:
                resolved = REPO / aliases.get(path, path)
                assert resolved.is_file(), f"{svc['name']}: version_file {path} does not exist"

    def test_every_pin_is_tracked_or_allowlisted(self):
        """--check-coverage, offline: a `*_version` with no entry is never
        reported as outdated."""
        run = subprocess.run(
            [sys.executable, str(SCRIPTS / "check-versions.py"), "--check-coverage"],
            capture_output=True, text=True, cwd=REPO,
        )
        assert run.returncode == 0, run.stdout + run.stderr


def _parse_deploy_coverage_conf(path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """-> (settings, {section: [entry, ...]}), rationale comments stripped."""
    settings: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    section = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        value = line.split("#")[0].strip()
        if section == "settings":
            key, _, val = value.partition("=")
            settings[key.strip()] = val.strip()
        elif section:
            sections.setdefault(section, []).append(value)
    return settings, sections


class TestDeployCoverageConfig:
    GATE = SCRIPTS / "check-deploy-coverage.sh"

    @staticmethod
    def _git(repo: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def _fixture_repo(self, tmp_path: Path) -> Path:
        """A throwaway repo with a real diff, so the gate's mapping arm runs.

        Passing BASE_REF=HEAD against the live repo yields an empty three-dot
        diff and the script returns 0 at its "nothing changed" short-circuit,
        before any CI parsing or membership check happens.
        """
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "ansible/playbooks").mkdir(parents=True)
        (repo / "ansible/inventories/prod").mkdir(parents=True)
        (repo / "scripts/check-deploy-coverage.sh").write_bytes(self.GATE.read_bytes())
        (repo / ".gitlab-ci.yml").write_text(
            "deploy-dns:\n"
            "  stage: deploy\n"
            "  rules:\n"
            "    - changes:\n"
            "        - ansible/playbooks/dns.yml\n"
        )
        (repo / "scripts/deploy-coverage.conf").write_text(
            "[settings]\n"
            "playbooks_dir = ansible/playbooks\n"
            "inventory_dir = ansible/inventories/prod\n"
            "ci_file = .gitlab-ci.yml\n"
            "\n"
            "[playbooks]\n"
            "exempt.yml  # deployed by hand\n"
        )
        for name in ("dns.yml", "orphan.yml", "exempt.yml"):
            (repo / "ansible/playbooks" / name).write_text("---\n- hosts: all\n")
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.email", "t@example.invalid")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        return repo

    # The gate prefers CI_MERGE_REQUEST_DIFF_BASE_SHA over its $1, then falls
    # back to CI_COMMIT_BEFORE_SHA. Both are set in every MR pipeline and both
    # name a commit that exists only in THIS repo — inheriting them pointed the
    # throwaway fixture repos at a SHA with no common ancestor (rc 2, "shares no
    # common ancestor with HEAD") and silently re-targeted the whole-repo case
    # at the real MR base. Blanked, never unset: the gate's `${VAR:-}` reads
    # empty and set-but-empty identically, and an explicit empty value survives
    # any wrapper that re-exports the environment.
    _NO_CI_BASE = {"CI_MERGE_REQUEST_DIFF_BASE_SHA": "", "CI_COMMIT_BEFORE_SHA": ""}

    def _run(self, repo: Path, base: str = "HEAD~1"):
        return subprocess.run(
            ["bash", "scripts/check-deploy-coverage.sh", base],
            capture_output=True, text=True, cwd=repo,
            env={
                **os.environ,
                **self._NO_CI_BASE,
                "DEPLOY_COVERAGE_CONFIG": "scripts/deploy-coverage.conf",
            },
        )

    def test_config_parses_and_the_gate_runs(self):
        """Every entry needs a trailing rationale; the script exits 2 without
        one, so a clean run is the parse assertion."""
        run = subprocess.run(
            ["bash", str(self.GATE), "HEAD"],
            capture_output=True, text=True, cwd=REPO,
            env={**os.environ, **self._NO_CI_BASE},
        )
        assert run.returncode == 0, run.stdout + run.stderr

    def test_the_fixture_gate_ignores_an_inherited_ci_base(self, tmp_path: Path):
        """Regression: in an MR pipeline both CI base variables are set to
        commits of THIS repo, and the gate prefers them over its argument. A
        fixture run that inherits them resolves a SHA the throwaway repo has
        never seen, so every deploy-coverage test failed in CI and only in CI."""
        repo = self._fixture_repo(tmp_path)
        (repo / "ansible/playbooks/dns.yml").write_text("---\n- hosts: dns\n")
        self._git(repo, "commit", "-aqm", "touch dns")
        foreign = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        leaked = subprocess.run(
            ["bash", "scripts/check-deploy-coverage.sh", "HEAD~1"],
            capture_output=True, text=True, cwd=repo,
            env={
                **os.environ,
                "CI_MERGE_REQUEST_DIFF_BASE_SHA": foreign,
                "DEPLOY_COVERAGE_CONFIG": "scripts/deploy-coverage.conf",
            },
        )
        assert leaked.returncode == 2, "the leak this test pins no longer happens"

        run = self._run(repo)
        assert run.returncode == 0, run.stdout + run.stderr

    def test_a_mapped_playbook_passes_the_mapping_arm(self, tmp_path: Path):
        repo = self._fixture_repo(tmp_path)
        (repo / "ansible/playbooks/dns.yml").write_text("---\n- hosts: dns\n")
        self._git(repo, "commit", "-aqm", "touch dns")
        run = self._run(repo)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "skipped" not in run.stdout, "the gate short-circuited instead of mapping"

    def test_an_unmapped_playbook_fails(self, tmp_path: Path):
        repo = self._fixture_repo(tmp_path)
        (repo / "ansible/playbooks/orphan.yml").write_text("---\n- hosts: all\n  become: true\n")
        self._git(repo, "commit", "-aqm", "touch orphan")
        run = self._run(repo)
        assert run.returncode == 1, run.stdout + run.stderr
        assert "orphan.yml" in run.stderr

    def test_a_config_exemption_grants_coverage(self, tmp_path: Path):
        repo = self._fixture_repo(tmp_path)
        (repo / "ansible/playbooks/exempt.yml").write_text("---\n- hosts: all\n  become: true\n")
        self._git(repo, "commit", "-aqm", "touch exempt")
        run = self._run(repo)
        assert run.returncode == 0, run.stdout + run.stderr

    def test_settings_match_the_repo_layout(self):
        settings, _ = _parse_deploy_coverage_conf(SCRIPTS / "deploy-coverage.conf")
        assert (REPO / settings["ci_file"]).is_file()
        assert (REPO / settings["inventory_dir"]).is_dir()
        assert (REPO / settings["playbooks_dir"]).is_dir()

    def test_playbook_and_inventory_entries_name_real_paths(self):
        """A stale exemption is silent in both directions: it either grants an
        exemption that can never match, or keeps exempting a path that has been
        renamed and should now be CI-deployed. ([roles] is deliberately not
        checked — the roles moved to weisssrv.infra, and those entries are kept
        for any role that lands back in-tree.)"""
        settings, sections = _parse_deploy_coverage_conf(SCRIPTS / "deploy-coverage.conf")
        missing = []
        for section, base in (
            ("playbooks", settings["playbooks_dir"]),
            ("inventory", settings["inventory_dir"]),
        ):
            for entry in sections.get(section, []):
                if not (REPO / base / entry).is_file():
                    missing.append(f"[{section}] {base}/{entry}")
        assert not missing, missing


class TestAutoscalingPolicy:
    def test_loads_through_the_gate(self):
        policy = _load("check-hpa-vpa-invariant.py").load_policy(
            str(SCRIPTS / "autoscaling-policy.yaml")
        )
        assert policy.chart_native_hpa_targets, (
            "the chart-native HPA list is empty — --require-chart-native-vpas "
            "would then assert nothing"
        )

    def test_cpu_limit_allowlist_entries_are_workload_keys(self):
        doc = yaml.safe_load((SCRIPTS / "autoscaling-policy.yaml").read_text())
        for entry in doc.get("cpu_limit_allowlist") or []:
            assert entry.count("/") == 2, f"{entry!r} is not namespace/Kind/name"


class TestHelmValuesReleases:
    """validate-helm-values.py takes the chart IDENTITY from these entries and
    only the version from the manifest, so an entry that has drifted from the
    HelmRelease it names renders the wrong chart against the right values and
    still reports green — a false pass on the one gate that can see .spec.values.
    """

    SOURCES = REPO / "kubernetes/infrastructure/sources"

    @staticmethod
    def _entries() -> list[dict]:
        return yaml.safe_load((SCRIPTS / "helm-values-releases.yaml").read_text())["releases"]

    def _helmrepositories(self) -> dict[str, str]:
        """name -> url for every HelmRepository under infrastructure/sources/."""
        repos: dict[str, str] = {}
        for path in sorted(self.SOURCES.glob("*.yaml")):
            for doc in yaml.safe_load_all(path.read_text()):
                if isinstance(doc, dict) and doc.get("kind") == "HelmRepository":
                    repos[doc["metadata"]["name"]] = (doc.get("spec") or {}).get("url")
        return repos

    def test_every_release_manifest_exists(self):
        for rel in self._entries():
            for key in ("name", "manifest", "chart", "repo_name", "repo_url"):
                assert rel.get(key), f"{rel} is missing {key}"
            assert (REPO / rel["manifest"]).is_file(), f"{rel['name']}: {rel['manifest']} missing"

    def test_entries_agree_with_the_helmrelease_they_name(self):
        problems = []
        for rel in self._entries():
            doc = yaml.safe_load((REPO / rel["manifest"]).read_text())
            assert doc.get("kind") == "HelmRelease", f"{rel['manifest']} is not a HelmRelease"
            spec = doc.get("spec") or {}
            chart_spec = ((spec.get("chart") or {}).get("spec")) or {}
            # `name` is a label for logging (and a last-resort fallback for
            # `helm template <release>`), so it is deliberately NOT compared:
            # gitlab-agent's HelmRelease is releaseName `weisssrv-k3s`. What the
            # gate actually renders from is chart + repo.
            if rel["chart"] != chart_spec.get("chart"):
                problems.append(
                    f"{rel['name']}: entry chart {rel['chart']!r} != manifest chart "
                    f"{chart_spec.get('chart')!r}"
                )
            source_ref = (chart_spec.get("sourceRef") or {}).get("name")
            if rel["repo_name"] != source_ref:
                problems.append(
                    f"{rel['name']}: entry repo_name {rel['repo_name']!r} != manifest "
                    f"sourceRef {source_ref!r}"
                )
        assert not problems, problems

    # HelmReleases deliberately NOT rendered, each with the reason. An entry is
    # a claim a reviewer can check; the alternative — a chart quietly absent
    # from the list — is invisible.
    EXEMPT = {
        "kubernetes/infrastructure/crds/release.yaml": (
            "prometheus-operator-crds ships CRD manifests and no configurable "
            "values surface; rendering it also trips the validator's single-doc "
            "yaml load on the CRD stream"
        ),
        "kubernetes/apps/gitlab-runner/release.yaml": (
            "chart identity (chart, version, sourceRef) is merged in from the "
            "kubernetes/components/gitlab-runner-common component, so the "
            "manifest this gate would read names no chart"
        ),
        "kubernetes/apps/gitlab-runner-privileged/release.yaml": (
            "same shared-component chart spec as gitlab-runner"
        ),
    }

    def test_every_helmrelease_is_rendered_or_exempt(self):
        """A new or edited HelmRelease must land in the list or in EXEMPT.

        `helm template` is the only gate that sees inside `.spec.values`; a
        chart that is on neither list has its values completely unvalidated,
        and nothing says so.
        """
        listed = {rel["manifest"] for rel in self._entries()}
        found: set[str] = set()
        for path in sorted((REPO / "kubernetes").rglob("*.yaml")):
            text = path.read_text()
            if "kind: HelmRelease" not in text:
                continue
            try:
                docs = list(yaml.safe_load_all(text))
            except yaml.YAMLError:
                continue
            if any(isinstance(d, dict) and d.get("kind") == "HelmRelease" for d in docs):
                found.add(str(path.relative_to(REPO)))
        uncovered = sorted(found - listed - set(self.EXEMPT))
        assert not uncovered, (
            "HelmRelease manifests with no `helm template` coverage: "
            f"{uncovered}. Add each to scripts/helm-values-releases.yaml, or to "
            "TestHelmValuesReleases.EXEMPT with the reason it cannot be rendered."
        )
        stale = sorted(set(self.EXEMPT) - found)
        assert not stale, f"EXEMPT names manifests that no longer hold a HelmRelease: {stale}"

    def test_every_exemption_carries_a_reason(self):
        for manifest, reason in self.EXEMPT.items():
            assert reason.strip(), f"{manifest} is exempt with no reason"

    def test_repo_urls_match_the_helmrepository_flux_uses(self):
        repos = self._helmrepositories()
        problems = []
        for rel in self._entries():
            url = repos.get(rel["repo_name"])
            if url is None:
                problems.append(
                    f"{rel['name']}: no HelmRepository named {rel['repo_name']!r} under "
                    f"infrastructure/sources/"
                )
            elif url.rstrip("/") != rel["repo_url"].rstrip("/"):
                problems.append(
                    f"{rel['name']}: entry repo_url {rel['repo_url']!r} != HelmRepository "
                    f"url {url!r}"
                )
        assert not problems, problems


class TestHostsEnv:
    def test_generated_file_is_in_sync(self, tmp_path):
        """The committed roster must match the inventory; nothing else notices
        when a host is added and hosts.env is not regenerated."""
        out = tmp_path / "hosts.env"
        run = subprocess.run(
            [
                sys.executable, str(SCRIPTS / "generate-hosts-env.py"),
                "--inventory", "ansible/inventories/prod/hosts.yml",
                "--map", "scripts/hosts-env-map.yml",
                "--output", str(out),
                "--regen-command", "task hosts:sync",
            ],
            capture_output=True, text=True, cwd=REPO,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert out.read_text() == (SCRIPTS / "hosts.env").read_text(), (
            "scripts/hosts.env is stale — run `task hosts:sync`"
        )


class TestB2BucketConfig:
    def test_loads_through_the_gate(self):
        cfg = _load("b2-bucket-drift.py").load_config(SCRIPTS / "b2-bucket.json")
        assert cfg["desired"]["bucketType"] == "allPrivate", (
            "the offsite bucket must never be public"
        )

    def test_lifecycle_rule_cannot_expire_a_live_version(self):
        """`daysFromUploadingToHiding` set would auto-hide live backups, and the
        hide->delete window would then expire the only offsite copy."""
        cfg = json.loads((SCRIPTS / "b2-bucket.json").read_text())
        for rule in cfg["desired"]["lifecycleRules"]:
            assert rule["daysFromUploadingToHiding"] is None


def test_ci_include_pins_agree_with_the_single_source():
    """Every `include:` pinning weisssrv-lib must repeat variables.WEISSSRV_LIB_REF
    (GitLab resolves includes before the variables block exists, so the literals
    are unavoidable) and it must be a release tag."""
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "check-lib-pins.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert run.returncode == 0, run.stdout + run.stderr


class _CILoader(yaml.SafeLoader):
    """SafeLoader tolerating GitLab's `!reference` tags, subclassed so the
    constructor is not registered on the global SafeLoader."""


_CILoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _python_invocations(text: str) -> dict[str, dict[str, str | None]]:
    """{script path: {flag: value}} for every `python3 scripts/*.py` call.

    Shell-shape agnostic on purpose: the Taskfile writes one call per line with
    backslash continuations, the CI input writes them all on one line separated
    by `;`. Redirections and pipes are cut off so `< "$RENDER_ALL"` (present in
    both, spelled the same) never becomes a difference.
    """
    joined = re.sub(r"\\\n\s*", " ", text)
    found: dict[str, dict[str, str | None]] = {}
    for statement in re.split(r"[;\n]", joined):
        match = re.search(r"python3\s+(scripts/[\w.\-]+\.py)(.*)$", statement.strip())
        if not match:
            continue
        script, rest = match.group(1), re.split(r"[<>|]", match.group(2))[0]
        tokens = shlex.split(rest)
        found[script] = {
            tok: (
                tokens[i + 1]
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("--")
                else None
            )
            for i, tok in enumerate(tokens)
            if tok.startswith("--")
        }
    return found


def _taskfile_flux_lint_block() -> str:
    text = (REPO / "Taskfile.yml").read_text()
    start = text.index("\n  flux:lint:")
    end = text.index("\n  # === Maintenance Tasks ===", start)
    return text[start:end]


def _ci_extra_validation() -> str:
    ci = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
    for include in ci.get("include") or []:
        if isinstance(include, dict) and str(include.get("file", "")).endswith(
            "flux-lint.yml"
        ):
            return (include.get("inputs") or {}).get("extra_validation") or ""
    raise AssertionError("no /ci/validate/flux-lint.yml include found in .gitlab-ci.yml")


def test_flux_lint_extra_validation_matches_the_taskfile():
    """`task flux:lint` and CI's flux-lint must run the same argv.

    They are two hand-maintained copies of one command list with no shared
    source, and the drift is invisible: an omitted `--releases` is a
    FileNotFoundError only on the path that has it, and an omitted
    `--policy-config` makes check-hpa-vpa-invariant silently assert zero
    chart-native targets — a gate that passes by checking nothing. Round one of
    this review found exactly that pair missing from the CI side.
    """
    task_side = _python_invocations(_taskfile_flux_lint_block())
    ci_side = _python_invocations(_ci_extra_validation())

    assert task_side, "parsed no python invocations out of the Taskfile flux:lint block"
    assert set(task_side) == set(ci_side), (
        f"different scripts run locally vs in CI — "
        f"only in Taskfile: {sorted(set(task_side) - set(ci_side))}, "
        f"only in .gitlab-ci.yml: {sorted(set(ci_side) - set(task_side))}"
    )
    for script in sorted(task_side):
        assert task_side[script] == ci_side[script], (
            f"{script} is invoked with different flags:\n"
            f"  Taskfile flux:lint      {task_side[script]}\n"
            f"  .gitlab-ci extra_validation {ci_side[script]}"
        )


def test_the_parity_parser_can_see_a_dropped_flag():
    """A parity test that cannot fail is worse than none: prove the parser
    resolves continuations and would notice the round-one drift."""
    both = _python_invocations(
        'python3 scripts/a.py --policy-config x.yaml < "$R"; python3 scripts/b.py'
    )
    assert both == {"scripts/a.py": {"--policy-config": "x.yaml"}, "scripts/b.py": {}}
    continued = _python_invocations(
        "python3 scripts/a.py --policy-config \\\n     x.yaml < \"$R\"\n"
    )
    assert continued == {"scripts/a.py": {"--policy-config": "x.yaml"}}
    assert _python_invocations("python3 scripts/a.py") != both


def test_the_collection_pin_matches_the_ci_lib_ref():
    """ansible/requirements.yml and variables.WEISSSRV_LIB_REF are ONE pin.

    check-lib-pins.py walks `include:` entries only, so the collection pin — the
    half that decides which roles reach the live cluster — had no gate at all.
    A bump that moves the CI templates but leaves the collection behind deploys
    the OLD roles from a pipeline whose lint ran against the new library, and
    nothing in the repo notices. cluster-template already gates this; this is
    the same assertion in the repo that actually deploys.
    """
    ci = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
    lib_ref = (ci.get("variables") or {}).get("WEISSSRV_LIB_REF")
    assert lib_ref, "variables.WEISSSRV_LIB_REF is the single source of the pin"

    requirements = yaml.safe_load((REPO / "ansible/requirements.yml").read_text()) or {}
    git_entries = [
        c
        for c in requirements.get("collections") or []
        if isinstance(c, dict) and "weisssrv-lib" in str(c.get("name", ""))
    ]
    assert len(git_entries) == 1, (
        f"expected exactly one weisssrv-lib collection entry, found {len(git_entries)}"
    )
    assert git_entries[0].get("version") == lib_ref, (
        f"ansible/requirements.yml pins the collection at "
        f"{git_entries[0].get('version')!r} but .gitlab-ci.yml pins the library "
        f"at {lib_ref!r}. They are one pin — bump both, or the deploy jobs run "
        f"roles from a different library revision than CI validated."
    )


def test_the_ansible_pin_matches_the_ci_variable():
    """requirements.txt and `variables.ANSIBLE_VERSION` install the same
    interpreter-side Ansible.

    Several CI jobs `pip install "ansible==${ANSIBLE_VERSION}"` directly rather
    than through requirements.txt, so the two are copies of one pin. A drift
    means a local run and a pipeline run resolve different collection
    behaviour — which is exactly the class of difference that makes a local
    reproduction disagree with CI.
    """
    ci = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
    ci_pin = (ci.get("variables") or {}).get("ANSIBLE_VERSION")
    assert ci_pin, "variables.ANSIBLE_VERSION is where the CI jobs read the pin"

    match = re.search(
        r"^ansible==([^\s#]+)", (REPO / "requirements.txt").read_text(), re.MULTILINE
    )
    assert match, "requirements.txt no longer pins `ansible==`"
    assert match.group(1) == str(ci_pin), (
        f"requirements.txt pins ansible=={match.group(1)} but .gitlab-ci.yml's "
        f"ANSIBLE_VERSION is {ci_pin!r}. They are one pin — bump both."
    )


def test_terraform_module_refs_match_the_ci_variable():
    """Every terraform root pins `weisssrv-lib//terraform/modules/...` at a
    `?ref=` that is coupled to the library release but written by hand —
    check-lib-pins.py reads only the include block and requirements.yml, so
    this is the gate for the one pin class it cannot see. A stale ref means a
    root plans against module behaviour (and guardrails) a different library
    revision shipped.
    """
    ci = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
    lib_ref = (ci.get("variables") or {}).get("WEISSSRV_LIB_REF")
    assert lib_ref, "variables.WEISSSRV_LIB_REF is the library pin"

    # Discovered, not enumerated: a fifth root added without touching this file
    # would otherwise escape the only gate its `?ref=` pin has. The floor keeps
    # the discovery from silently degrading to a no-op loop.
    roots = sorted(p.parent.name for p in (REPO / "terraform").glob("*/main.tf"))
    assert len(roots) >= 4, (
        f"terraform root discovery found {roots} — expected at least the four "
        "roots under terraform/; this gate must never run on an empty list."
    )

    for root in roots:
        body = (REPO / "terraform" / root / "main.tf").read_text()
        refs = re.findall(r"weisssrv-lib\.git//terraform/modules/[^?\"]+\?ref=([^\"\s]+)", body)
        assert refs, f"terraform/{root}/main.tf no longer pins a lib module ref"
        for ref in refs:
            assert ref == str(lib_ref), (
                f"terraform/{root}/main.tf pins ?ref={ref} but "
                f"WEISSSRV_LIB_REF is {lib_ref!r}. The terraform refs move "
                f"with the library pin — bump them together."
            )


def test_molecule_image_literals_match_the_ci_variable():
    """The `${MOLECULE_TEST_IMAGE:-...:vX.Y.Z}` fallbacks in the integration
    scenarios and ansible/TESTING.md are copies of the library pin.

    CI always overrides MOLECULE_TEST_IMAGE from $WEISSSRV_LIB_REF, so a stale
    literal is invisible in the pipeline and only bites a LOCAL
    `task ansible:test-integration-*`, which then validates roles against an old
    image. check-lib-pins.py cannot see them (include block + requirements.yml
    only) and is vendored byte-identical, so the gate lives in the site-local
    check-molecule-image-pin.py — run here so it rides `task lint`.
    """
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "check-molecule-image-pin.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_no_tenant_onboards_while_traefik_allows_cross_namespace():
    """The docs/30 pre-onboarding checklist, as a build failure.

    Traefik's `allowCrossNamespace: true` is an accepted single-operator risk
    with nothing enforcing the precondition, while tenant-crd-editor already
    grants a future tenant full traefik.io write. The gate fires on the exact
    commit that adds a tenant wiring file, not a checklist re-read.
    """
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "check-tenant-traefik-isolation.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_tenant_isolation_gate_fires_on_a_tenant(tmp_path):
    """A gate that cannot fail is worse than none: prove it trips once a second
    resource joins the tenants kustomization."""
    check = _load("check-tenant-traefik-isolation.py")
    for rel in (check.TENANTS_KUSTOMIZATION, check.TRAEFIK_RELEASE):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((REPO / rel).read_text())

    assert check.check(tmp_path) == []

    kustomization = tmp_path / check.TENANTS_KUSTOMIZATION
    kustomization.write_text(
        kustomization.read_text().replace(
            "  - tenant-crd-editor.yaml",
            "  - tenant-crd-editor.yaml\n  - some-tenant.yaml",
        )
    )
    problems = check.check(tmp_path)
    assert problems and "some-tenant.yaml" in problems[0]

    release = tmp_path / check.TRAEFIK_RELEASE
    release.write_text(
        release.read_text().replace(
            "allowCrossNamespace: true", "allowCrossNamespace: false"
        )
    )
    assert check.check(tmp_path) == []


def test_the_tailscale_policy_passes_its_gate():
    """policy.hujson parses, declares every tag it uses, and auto-approves
    exactly the routes the inventory advertises — the three things nothing else
    checks before the supervised apply against the live tailnet."""
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "check-tailscale-policy.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_tailscale_gate_catches_an_undeclared_tag_and_a_route_drift(tmp_path):
    """Both semantic arms must be able to fire — a syntax-only gate is what this
    replaced."""
    gate = _load("check-tailscale-policy.py")
    policy = tmp_path / gate.POLICY
    policy.parent.mkdir(parents=True, exist_ok=True)
    for rel in gate.INVENTORY_GLOBS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    proxmox = tmp_path / "ansible/inventories/prod/group_vars/proxmox.yml"
    proxmox.write_text('tailscale_advertise_routes:\n  - "192.168.0.0/24"\n')

    good = (
        '{\n'
        '  // comment with a https:// url\n'
        '  "groups": {"group:admins": ["a@example.com"]},\n'
        '  "tagOwners": {"tag:router": ["a@example.com"]},\n'
        '  "acls": [{"action": "accept", "src": ["group:admins"],\n'
        '            "dst": ["tag:router:22,443"]}],\n'
        '  "ssh": [],\n'
        '  "autoApprovers": {"routes": {"192.168.0.0/24": ["tag:router"]}},\n'
        '}\n'
    )
    policy.write_text(good)
    assert gate.check(tmp_path) == []

    for bad_value in ('[]}', '"tag:router"}', '[42]}'):
        policy.write_text(good.replace('["tag:router"]}', bad_value))
        assert any("approver" in p for p in gate.check(tmp_path)), bad_value
    policy.write_text(good)

    policy.write_text(good.replace("tag:router:22,443", "tag:typo:22,443"))
    assert any("tag:typo" in v for v in gate.check(tmp_path))

    policy.write_text(good.replace('"192.168.0.0/24": ', '"10.0.0.0/8": '))
    assert any("autoApprovers.routes" in v for v in gate.check(tmp_path))


def test_cluster_config_value_reads_the_configmap():
    """The VIPs host-side tooling consumes come from cluster-config, and an
    absent key fails rather than yielding an empty sed/probe list."""
    ok = subprocess.run(
        [str(SCRIPTS / "cluster-config-value.sh"),
         "cluster_metallb_public_vip", "cluster_api_vip"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert ok.returncode == 0, ok.stderr
    config = yaml.safe_load(
        (REPO / "kubernetes/infrastructure/sources/cluster-config.yaml").read_text()
    )["data"]
    assert ok.stdout.split() == [
        config["cluster_metallb_public_vip"], config["cluster_api_vip"]
    ]

    missing = subprocess.run(
        [str(SCRIPTS / "cluster-config-value.sh"), "cluster_not_a_key"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert missing.returncode != 0 and "cluster_not_a_key" in missing.stderr


def test_the_gitlab_backup_companions_are_what_the_wrapper_copies():
    """`gitlab-backup-run.sh` drops `gitlab-secrets.json` and `gitlab.rb` beside
    the tarball, and the collector's `companions:` globs are what emit
    `backup_artifact_companion_present`. A glob naming anything else reports
    present=0 forever, so BackupArtifactCompanionMissing would fire on a healthy
    backup.

    check-backup-artifact-apps.py (vendored, suite upstream) proves the
    declared/alerted pairing; only this repo knows which filenames the wrapper
    actually writes.
    """
    data = yaml.safe_load(
        (REPO / "ansible/inventories/prod/host_vars/pve-nas-01.yml").read_text()
    )
    entry = next(
        a for a in data["nas_storage_backup_artifact_apps"] if a["name"] == "gitlab"
    )
    assert entry["companions"] == ["gitlab-secrets.json", "gitlab.rb"]


class TestBackupAlertInstanceRegexes:
    """The per-host existence witnesses spell their host set as a PromQL
    character class, and adding a host to hosts.yml does not update it.

    Both arms of those alerts are then silent for the new host: the timestamp
    arm needs a series the host never emits, and the `up{instance=~...}` witness
    — the arm that exists precisely to catch "this host emits nothing" — does
    not select it. So the alert reports healthy for a host it no longer covers.
    Derive the expectation from the inventory instead of eyeballing the class.
    """

    RULES = REPO / "kubernetes/infrastructure/observability/rules/scripts.yaml"
    PORT = "9101"  # node_exporter_host; the k3s DaemonSet owns 9100 on this LAN
    # alert -> the inventory group whose ansible_host set its witness must cover
    WITNESSES = {
        "VzdumpBackupStale": "proxmox",
        "EtcdSnapshotStale": "k3s_servers",
    }

    @staticmethod
    def _inventory_addresses() -> dict[str, str]:
        """{inventory_hostname: ansible_host} across the whole prod inventory."""
        found: dict[str, str] = {}

        def walk(node) -> None:
            if not isinstance(node, dict):
                return
            for name, host in (node.get("hosts") or {}).items():
                if isinstance(host, dict) and host.get("ansible_host"):
                    found[name] = str(host["ansible_host"])
            for child in (node.get("children") or {}).values():
                walk(child)

        inventory = yaml.safe_load(
            (REPO / "ansible/inventories/prod/hosts.yml").read_text()
        )
        walk(inventory.get("all") or {})
        assert found, "parsed no ansible_host entries out of hosts.yml"
        return found

    @classmethod
    def _group_members(cls, group: str) -> set[str]:
        from test_host_log_staleness import _build_group_index, _resolve_hosts

        inventory = yaml.safe_load(
            (REPO / "ansible/inventories/prod/hosts.yml").read_text()
        )
        hosts = _resolve_hosts(group, _build_group_index(inventory))
        assert hosts, f"group {group!r} resolved to no hosts (inventory drift?)"
        return hosts

    @classmethod
    def _witness_pattern(cls, alert: str) -> str:
        """The instance regex out of the alert's expr, as PromQL sees it.

        The manifest is a plain YAML scalar, so `\\\\.` reaches PromQL as two
        characters and PromQL's own double-quoted string unescapes them to one.
        """
        doc = yaml.safe_load(cls.RULES.read_text())
        rules = [
            rule
            for group in doc["spec"]["groups"]
            for rule in group["rules"]
            if rule.get("alert") == alert
        ]
        assert len(rules) == 1, f"expected exactly one {alert} rule, found {len(rules)}"
        match = re.search(r'instance=~"([^"]+)"', rules[0]["expr"])
        assert match, f"{alert} has no instance=~ witness arm any more"
        return match.group(1).replace("\\\\", "\\")

    @pytest.mark.parametrize("alert,group", sorted(WITNESSES.items()))
    def test_the_witness_covers_exactly_its_inventory_group(self, alert, group):
        pattern = self._witness_pattern(alert)
        addresses = self._inventory_addresses()
        members = self._group_members(group)

        uncovered = sorted(
            f"{host} ({addresses[host]})"
            for host in members
            if host in addresses
            and not re.fullmatch(pattern, f"{addresses[host]}:{self.PORT}")
        )
        assert not uncovered, (
            f"{alert}'s instance regex {pattern!r} does not select {uncovered} — "
            f"those {group} hosts are outside the existence witness, so the alert "
            "reads healthy for a host emitting nothing. Widen the character class."
        )

        overreach = sorted(
            f"{host} ({address})"
            for host, address in addresses.items()
            if host not in members
            and re.fullmatch(pattern, f"{address}:{self.PORT}")
        )
        assert not overreach, (
            f"{alert}'s instance regex {pattern!r} also selects {overreach}, which "
            f"are not in {group} — the witness would fire for hosts that are not "
            "supposed to emit the metric at all."
        )
