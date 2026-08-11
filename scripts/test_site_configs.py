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
