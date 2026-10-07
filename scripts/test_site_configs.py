"""The site data the vendored scripts read.

Asserts the config shapes those scripts assume, plus the whole-repo invariants
that hold only here, which the library's own suites cannot cover.
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from inventory_tree import (
    addresses_by_host,
    group_index,
    load_inventory,
    resolve_hosts,
)
from test_vendored_byte_identity import _CILoader, _pinned_ref, load_ci_doc
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

KNOWN_CATEGORIES = {
    "github", "dockerhub", "ghcr", "lsio", "helm",
    "gitlab", "plex", "apt_repo", "manual",
}


def _load(name: str):
    return load_script(name)


@functools.cache
def _inventory_addresses() -> dict[str, str]:
    """{inventory_hostname: ansible_host} across the whole prod inventory."""
    found = addresses_by_host(load_inventory(REPO / "ansible/inventories/prod/hosts.yml"))
    assert found, "parsed no ansible_host entries out of hosts.yml"
    return found


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
            # A half-declared pin has no renderable checksum URL, and a
            # version_file pin has no vars-file version to render one at.
            if bool(svc.get("checksum_var")) != bool(svc.get("checksum_url")):
                problems.append(
                    f"{svc['name']}: declares only one of checksum_var/checksum_url"
                )
            if svc.get("checksum_var") and svc.get("version_file"):
                problems.append(
                    f"{svc['name']}: pairs a checksum pin with a version_file pin"
                )
            # CRITICAL: the gates download these, so a plaintext one lets the
            # network decide what gets hashed or which version is "latest".
            # `source_url` is a reference link nothing fetches and is exempt.
            for field in ("checksum_url", "apt_url", "apt_index_url", "helm_repo"):
                url = svc.get(field)
                if url and not str(url).startswith("https://"):
                    problems.append(f"{svc['name']}: {field} is not https: {url}")
        assert not problems, problems

    @pytest.mark.parametrize(
        "entry",
        [
            {"checksum_var": "made_up_checksum"},
            {"checksum_url": "https://example.invalid/{version}.tar.gz"},
            {
                "checksum_var": "made_up_checksum",
                "checksum_url": "https://example.invalid/{version}.tar.gz",
                "version_file": "ansible/requirements.yml",
            },
            {
                "checksum_var": "made_up_checksum",
                "checksum_url": "http://example.invalid/{version}.tar.gz",
            },
            {"apt_url": "http://example.invalid/Packages.gz"},
            {"helm_repo": "http://charts.example.invalid"},
        ],
    )
    def test_a_malformed_checksum_pin_is_reported(self, registry, entry):
        broken = dict(registry)
        broken["services"] = [{"name": "half-pinned", "category": "github", **entry}]
        with pytest.raises(AssertionError):
            self.test_entries_are_well_formed(broken)

    def test_the_checksum_arm_has_something_to_check(self, registry):
        """A registry with no checksum pin makes every assertion above vacuous:
        the well-formedness arm only inspects entries that declare one."""
        pinned = [
            svc["name"]
            for svc in registry["services"]
            if svc.get("checksum_var") and svc.get("checksum_url")
        ]
        assert pinned, (
            "no registry entry declares a checksum_var + checksum_url pair, so the "
            "checksum arm of test_entries_are_well_formed verified nothing — a "
            "rename or a refactor dropped the supply-chain pins"
        )

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

    # Image-build inputs: the Dockerfiles bake them into an image the CI job
    # builds, so they never appear as a ${...} placeholder in a manifest.
    BUILD_INPUTS = {
        "hermes_version", "hermes_codex_version", "hermes_claude_version",
        "hermes_op_version",
    }

    def test_rollout_mapping_matches_how_the_pin_reaches_the_fleet(self):
        """_deploy_command answers Flux for every var a manifest spells as `${var}`."""
        mod = _load("version-registry.py")
        refs = set()
        for path in (REPO / "kubernetes").rglob("*.yaml"):
            refs |= set(re.findall(r"\$\{([a-z0-9_]+_version)\}", path.read_text()))
        assert refs, "found no ${..._version} placeholders — the substitution syntax moved"

        problems = []
        for svc in mod.CONFIG["services"]:
            var = svc["var_name"]
            if var in refs and mod._deploy_command(svc) != mod._FLUX_DEPLOY:
                problems.append(f"{var}: substituted by Flux but not routed through it")
            if var in mod._FLUX_MANAGED and var not in refs and var not in self.BUILD_INPUTS:
                problems.append(f"{var}: listed as Flux-managed but no manifest reads it")
        assert not problems, problems

    def test_version_file_lists_name_every_manifest_carrying_the_pin(self, registry):
        """A digest pin shared by several manifests must list all of them: bumped
        in two of three, the third silently keeps the old image."""
        problems = []
        for svc in registry["services"]:
            version_file, ref = svc.get("version_file"), svc.get("image_ref")
            if not version_file or not ref:
                continue
            listed = {version_file} if isinstance(version_file, str) else set(version_file)
            if not all(path.startswith("kubernetes/") for path in listed):
                continue
            pattern = re.compile(rf"image:\s*{re.escape(ref)}:[^\s@]+@sha256:")
            carrying = {
                str(path.relative_to(REPO))
                for path in (REPO / "kubernetes").rglob("*.yaml")
                if pattern.search(path.read_text())
            }
            if carrying != listed:
                problems.append(
                    f"{svc['name']}: version_file lists {sorted(listed)} but "
                    f"{sorted(carrying)} pin {ref}"
                )
        assert not problems, problems

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
        """The gate's mapping arm runs on a throwaway repo with a real diff.

        BASE_REF=HEAD against the live repo short-circuits on an empty diff.
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

    # Blank both CI base variables: the gate prefers them over its $1, and in an
    # MR pipeline they name commits of THIS repo, not the fixture. Blank, never
    # unset - the gate's `${VAR:-}` reads unset and set-but-empty identically.
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
        """The fixture run must not inherit the CI base variables: the gate
        prefers them over its argument and they name a commit the throwaway repo
        has never seen."""
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
        """Every deploy-coverage exemption names a path that exists.

        [roles] is unchecked: its entries wait for a role landing back in-tree.
        """
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

    # allowlist key -> the shape its entries must spell. The gates turn each key
    # into a set member verbatim, so a mis-shaped one exempts nothing silently.
    ALLOWLIST_SHAPES = {
        "cpu_limit_allowlist": "namespace/Kind/name",
        "vpa_cap_allowlist": "namespace/VerticalPodAutoscaler/name",
        "memory_ratio_allowlist": "namespace/Kind/name",
    }

    @pytest.mark.parametrize("key", sorted(ALLOWLIST_SHAPES))
    def test_allowlist_entries_are_workload_keys(self, key: str):
        doc = yaml.safe_load((SCRIPTS / "autoscaling-policy.yaml").read_text())
        assert key in doc, f"{key} is absent — the gate reading it exempts nothing"
        entries = doc[key]
        assert isinstance(entries, dict), (
            f"{key} must be a mapping of key -> reason, not {type(entries).__name__}"
        )
        shape = self.ALLOWLIST_SHAPES[key]
        for entry, reason in entries.items():
            assert entry.count("/") == 2, f"{entry!r} is not {shape}"
            assert isinstance(reason, str) and len(reason.split()) >= 3, (
                f"{entry!r} needs a reason naming why it is exempt, got {reason!r}"
            )


class TestHelmValuesReleases:
    """helm-values-releases.yaml entries match the HelmReleases they name.

    validate-helm-values.py takes chart identity from the entry, so a drifted
    one renders the wrong chart against the right values and still passes.
    """

    SOURCES = REPO / "kubernetes/infrastructure/sources"

    @staticmethod
    def _entries() -> list[dict]:
        return yaml.safe_load((SCRIPTS / "helm-values-releases.yaml").read_text())["releases"]

    def _helmrepositories(self) -> dict[str, str]:
        """name -> url for every HelmRepository under infrastructure/sources/.

        Both suffixes and every depth: a repo Flux reconciles but this scan
        misses passes the url parity assertion on a stale entry.
        """
        repos: dict[str, str] = {}
        for path in sorted({*self.SOURCES.rglob("*.yaml"), *self.SOURCES.rglob("*.yml")}):
            for doc in yaml.safe_load_all(path.read_text()):
                if isinstance(doc, dict) and doc.get("kind") == "HelmRepository":
                    repos[doc["metadata"]["name"]] = (doc.get("spec") or {}).get("url")
        return repos

    def test_every_release_manifest_exists(self):
        for rel in self._entries():
            for key in ("name", "manifest", "chart", "repo_name", "repo_url"):
                assert rel.get(key), f"{rel} is missing {key}"
            assert (REPO / rel["manifest"]).is_file(), f"{rel['name']}: {rel['manifest']} missing"

    @staticmethod
    def _helm_release(relpath: str) -> dict:
        """The first HelmRelease document in a manifest that may hold several."""
        for doc in yaml.safe_load_all((REPO / relpath).read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "HelmRelease":
                return doc
        raise AssertionError(f"{relpath} holds no HelmRelease")

    def test_entries_agree_with_the_helmrelease_they_name(self):
        problems = []
        for rel in self._entries():
            doc = self._helm_release(rel["manifest"])
            spec = doc.get("spec") or {}
            chart_spec = ((spec.get("chart") or {}).get("spec")) or {}
            # `name` is a logging label, not what the gate renders from, so it
            # is not compared: gitlab-agent's HelmRelease is releaseName
            # `weisssrv-k3s`.
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

    # HelmReleases not rendered, each with the reason. An entry is a claim a
    # reviewer can check; a chart quietly absent from the list is invisible.
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
        """A new or edited HelmRelease lands in the list or in EXEMPT.

        `helm template` is the only gate that sees inside `.spec.values`.
        """
        listed = {rel["manifest"] for rel in self._entries()}
        found: set[str] = set()
        for path in sorted((REPO / "kubernetes").rglob("*.yaml")):
            text = path.read_text()
            if "kind: HelmRelease" not in text:
                continue
            try:
                docs = list(yaml.safe_load_all(text))
            except yaml.YAMLError as exc:
                raise AssertionError(
                    f"{path}: unparseable YAML ({exc.__class__.__name__}) — it names "
                    "a HelmRelease the render gate would then never cover"
                ) from exc
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

    def test_both_sides_of_the_parity_check_are_populated(self):
        """Every assertion in this class runs inside a loop over one of these two
        inputs, so an emptied list would pass the whole class comparing nothing."""
        assert self._entries(), (
            "scripts/helm-values-releases.yaml declares no releases — the render "
            "and parity arms below would all pass vacuously"
        )
        assert self._helmrepositories(), (
            "infrastructure/sources/ declares no HelmRepository — the repo-URL "
            "parity arm below would have nothing to compare against"
        )

    def test_repo_urls_match_the_helmrepository_flux_uses(self):
        repos = self._helmrepositories()
        problems = []
        compared = 0
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
            else:
                compared += 1
        assert not problems, problems
        # Comparisons, not files: both inputs can be non-empty and still share no
        # repo_name, which is agreement by coincidence rather than by check.
        assert compared, (
            f"none of the {len(self._entries())} release entries matched any of the "
            f"{len(repos)} HelmRepository CR(s), so no repo URL was actually compared"
        )

    def test_a_yml_or_nested_helmrepository_is_still_scanned(self, tmp_path, monkeypatch):
        """Both suffixes and every depth, or the url parity check sees no repo."""
        (tmp_path / "nested").mkdir()
        (tmp_path / "flat.yml").write_text(
            "apiVersion: source.toolkit.fluxcd.io/v1\nkind: HelmRepository\n"
            "metadata:\n  name: flat\nspec:\n  url: https://flat.invalid/charts\n"
        )
        (tmp_path / "nested/deep.yaml").write_text(
            "apiVersion: source.toolkit.fluxcd.io/v1\nkind: HelmRepository\n"
            "metadata:\n  name: deep\nspec:\n  url: https://deep.invalid/charts\n"
        )
        monkeypatch.setattr(type(self), "SOURCES", tmp_path)
        assert self._helmrepositories() == {
            "flat": "https://flat.invalid/charts",
            "deep": "https://deep.invalid/charts",
        }


KPS_RELEASE = "kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml"

# Helm's own defaults scope discovery to objects carrying the release label, so
# restoring any of these drops every app-side ServiceMonitor and PrometheusRule.
WIDE_DISCOVERY = {
    "serviceMonitorSelectorNilUsesHelmValues": False,
    "serviceMonitorNamespaceSelector": {},
    "podMonitorSelectorNilUsesHelmValues": False,
    "podMonitorNamespaceSelector": {},
    "ruleSelectorNilUsesHelmValues": False,
    "ruleNamespaceSelector": {},
}


def _prometheus_spec(doc: dict) -> dict:
    values = (doc.get("spec") or {}).get("values") or {}
    return (values.get("prometheus") or {}).get("prometheusSpec") or {}


def _discovery_problems(spec: dict) -> list[str]:
    """An absent key counts, so a renamed or moved values path fails loudly
    rather than reading as the chart default."""
    problems = []
    for key, want in WIDE_DISCOVERY.items():
        if key not in spec:
            problems.append(f"{key} is absent, so the chart default applies")
        elif spec[key] != want:
            problems.append(f"{key} is {spec[key]!r}, want {want!r}")
    return problems


def test_prometheus_discovers_monitoring_objects_in_every_namespace():
    doc = TestHelmValuesReleases._helm_release(KPS_RELEASE)
    problems = _discovery_problems(_prometheus_spec(doc))
    assert not problems, (
        f"{KPS_RELEASE} prometheusSpec: {'; '.join(problems)} — app-side "
        "ServiceMonitors and PrometheusRules outside the release namespace stop "
        "being discovered, so their panels go blank and their alerts never fire"
    )


def test_a_narrowed_discovery_selector_is_reported():
    """Mutation case: the collector, not just the shipped values."""
    assert _discovery_problems(dict(WIDE_DISCOVERY)) == []
    assert _discovery_problems({**WIDE_DISCOVERY, "ruleSelectorNilUsesHelmValues": True})
    pruned = {k: v for k, v in WIDE_DISCOVERY.items() if k != "ruleNamespaceSelector"}
    assert _discovery_problems(pruned)
    assert _discovery_problems({})


# Releases whose chart owns CRDs, each naming the shape that keeps them. The
# shape is checked, so a values key the chart never reads fails instead of
# passing on the string alone.
CRD_KEEPERS = {
    "kubernetes/infrastructure/crds/release.yaml": "values-annotation",
    "kubernetes/infrastructure/controllers/cert-manager/release.yaml": "values-keep",
    "kubernetes/infrastructure/controllers/external-secrets/release.yaml": "values-annotation",
    "kubernetes/infrastructure/controllers/metallb/release.yaml": "post-renderer",
    "kubernetes/infrastructure/controllers/traefik/release.yaml": "flux-create-replace",
    "kubernetes/infrastructure/controllers/vpa/release.yaml": "flux-create-replace",
    "kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml": "owned-elsewhere",
}

# Every other HelmRelease, with the reason an uninstall takes no CR with it. A
# release in neither dict fails the walk below, so a chart that starts shipping
# CRDs in templates/ cannot reach the cluster unnoticed.
NO_CRDS = {
    "kubernetes/apps/authentik/release.yaml": (
        "the chart ships no CustomResourceDefinition; Authentik state lives in "
        "its Postgres database and in terraform/authentik"
    ),
    "kubernetes/apps/gitlab-agent/release.yaml": (
        "the chart ships no CustomResourceDefinition; the agent reads its own "
        "config from .gitlab/agents/ in this repository"
    ),
    "kubernetes/apps/gitlab-runner/release.yaml": (
        "the chart ships no CustomResourceDefinition; runner registration state "
        "lives in a Secret and in GitLab itself"
    ),
    "kubernetes/apps/gitlab-runner-privileged/release.yaml": (
        "same chart as gitlab-runner, which ships no CustomResourceDefinition"
    ),
    "kubernetes/infrastructure/controllers/external-dns/release.yaml": (
        "the chart ships no CustomResourceDefinition; records are driven by "
        "annotations on Services and IngressRoutes"
    ),
    "kubernetes/infrastructure/controllers/kured/release.yaml": (
        "the chart ships no CustomResourceDefinition; reboot state lives in node "
        "annotations and a lock ConfigMap"
    ),
    "kubernetes/infrastructure/controllers/metrics-server/release.yaml": (
        "the chart ships an APIService, not a CustomResourceDefinition, and the "
        "metrics it serves are recomputed from the kubelets"
    ),
    "kubernetes/infrastructure/controllers/nvidia-device-plugin/release.yaml": (
        "the chart ships no CustomResourceDefinition; the plugin advertises its "
        "devices through the kubelet device plugin API"
    ),
    "kubernetes/infrastructure/controllers/onepassword-connect/release.yaml": (
        "the chart ships no CustomResourceDefinition here; the operator that owns "
        "OnePasswordItem is not installed, ESO reads Connect instead"
    ),
    "kubernetes/infrastructure/controllers/reloader/release.yaml": (
        "the chart ships no CustomResourceDefinition; it watches ConfigMaps and "
        "Secrets and annotates workloads"
    ),
    "kubernetes/infrastructure/controllers/tailscale-operator/release.yaml": (
        "the chart's CRDs ship in its crds/ directory, which a helm uninstall "
        "never deletes"
    ),
    "kubernetes/infrastructure/observability/alloy/release.yaml": (
        "the chart ships no CustomResourceDefinition; the collector config lives "
        "in a ConfigMap this repository owns"
    ),
    "kubernetes/infrastructure/observability/alloy-syslog/release.yaml": (
        "same chart as alloy, which ships no CustomResourceDefinition"
    ),
    "kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml": (
        "the chart ships no CustomResourceDefinition; its probe modules live in "
        "the release values"
    ),
    "kubernetes/infrastructure/observability/loki/release.yaml": (
        "the chart ships no CustomResourceDefinition; the ruler reads its LogQL "
        "rules from a ConfigMap this repository owns"
    ),
}

KEEP_ANNOTATION = ("helm.sh/resource-policy", "keep")


def _crd_values(doc: dict) -> dict:
    values = (doc.get("spec") or {}).get("values") or {}
    crds = values.get("crds")
    return crds if isinstance(crds, dict) else {}


def _keeps_values_annotation(doc: dict) -> bool:
    key, want = KEEP_ANNOTATION
    annotations = _crd_values(doc).get("annotations")
    return isinstance(annotations, dict) and annotations.get(key) == want


def _keeps_values_keep(doc: dict) -> bool:
    return _crd_values(doc).get("keep") is True


def _keeps_post_renderer(doc: dict) -> bool:
    """A kustomize patch stamping the keep annotation onto the chart's CRDs."""
    key, want = KEEP_ANNOTATION
    for renderer in (doc.get("spec") or {}).get("postRenderers") or []:
        for patch in ((renderer.get("kustomize") or {}).get("patches") or []):
            target = patch.get("target") or {}
            if target.get("kind") != "CustomResourceDefinition":
                continue
            body = yaml.safe_load(patch.get("patch") or "") or {}
            annotations = ((body.get("metadata") or {}).get("annotations")) or {}
            if annotations.get(key) == want:
                return True
    return False


def _flux_crds_policy(doc: dict) -> tuple[object, object]:
    spec = doc.get("spec") or {}
    return (
        (spec.get("install") or {}).get("crds"),
        (spec.get("upgrade") or {}).get("crds"),
    )


def _keeps_flux_create_replace(doc: dict) -> bool:
    """Flux applies the chart's crds/ directory and never deletes from it."""
    return _flux_crds_policy(doc) == ("CreateReplace", "CreateReplace")


def _keeps_owned_elsewhere(doc: dict) -> bool:
    """The CRDs come from another release, so this chart must ship none."""
    return (
        _flux_crds_policy(doc) == ("Skip", "Skip")
        and _crd_values(doc).get("enabled") is False
    )


KEEP_SHAPES = {
    "values-annotation": _keeps_values_annotation,
    "values-keep": _keeps_values_keep,
    "post-renderer": _keeps_post_renderer,
    "flux-create-replace": _keeps_flux_create_replace,
    "owned-elsewhere": _keeps_owned_elsewhere,
}


def _helm_release_paths() -> list[str]:
    """Every HelmRelease path under kubernetes/, relative to the repo root."""
    found: set[str] = set()
    for path in sorted((REPO / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if "kind: HelmRelease" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "HelmRelease":
                found.add(str(path.relative_to(REPO)))
    return sorted(found)


def _unclassified(paths: list[str]) -> list[str]:
    return sorted(set(paths) - set(CRD_KEEPERS) - set(NO_CRDS))


@pytest.mark.parametrize("relpath,shape", sorted(CRD_KEEPERS.items()))
def test_crd_owning_releases_keep_their_crds(relpath, shape):
    doc = TestHelmValuesReleases._helm_release(relpath)
    assert KEEP_SHAPES[shape](doc), (
        f"{relpath} no longer carries the {shape} policy that keeps the CRDs its "
        "chart owns — the next uninstall or failed install takes every CR with them"
    )


def _upgrade_remediation_strategy(doc: dict) -> object:
    """`spec.upgrade.remediation.strategy` — absent means Flux rolls back."""
    spec = doc.get("spec") or {}
    return ((spec.get("upgrade") or {}).get("remediation") or {}).get("strategy")


@pytest.mark.parametrize("relpath", sorted(CRD_KEEPERS))
def test_crd_owning_releases_roll_back_instead_of_uninstalling(relpath):
    """An uninstall remediation on a failed upgrade is the cascade itself, so a
    CRD-owning release rolls back in place instead."""
    strategy = _upgrade_remediation_strategy(TestHelmValuesReleases._helm_release(relpath))
    assert strategy != "uninstall", (
        f"{relpath} remediates a failed upgrade by uninstalling, which deletes "
        "the chart's CRDs and every CR"
    )


def test_an_uninstalling_upgrade_remediation_is_reported():
    """Mutation case: the reader, not just the shipped releases."""
    assert _upgrade_remediation_strategy({"spec": {}}) is None
    uninstall = {"upgrade": {"remediation": {"strategy": "uninstall"}}}
    assert _upgrade_remediation_strategy({"spec": uninstall}) == "uninstall"


def test_every_helmrelease_is_classified_for_crd_ownership():
    """A release in neither dict is one nothing holds to the keep policy: a chart
    shipping CRDs in templates/ loses every CR on an uninstall."""
    paths = _helm_release_paths()
    assert paths, "no HelmRelease found under kubernetes/ — this guard checked nothing"
    missing = _unclassified(paths)
    assert not missing, (
        "HelmReleases in neither CRD_KEEPERS nor NO_CRDS:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd each to CRD_KEEPERS with the shape that keeps its CRDs, or to "
        "NO_CRDS with the reason an uninstall takes no CR with it."
    )
    stale = sorted((set(CRD_KEEPERS) | set(NO_CRDS)) - set(paths))
    assert not stale, f"these entries name no HelmRelease the tree ships: {stale}"


def test_every_no_crds_entry_states_its_reason():
    for relpath, reason in NO_CRDS.items():
        assert len(reason.split()) >= 8, f"{relpath} needs a stated reason"


def test_an_unclassified_helmrelease_is_reported():
    """Mutation case: the inversion, not just the shipped corpus."""
    listed = sorted(CRD_KEEPERS)[:1]
    assert _unclassified(listed) == []
    assert _unclassified([*listed, "kubernetes/apps/demo/release.yaml"]) == [
        "kubernetes/apps/demo/release.yaml"
    ]


def test_a_release_without_its_keep_shape_is_reported():
    """Mutation case: each shape is anchored to the path its chart reads, so a
    misplaced key or annotation fails."""
    key, want = KEEP_ANNOTATION
    assert _keeps_values_annotation({"spec": {"values": {"crds": {"annotations": {key: want}}}}})
    assert not _keeps_values_annotation({"spec": {"values": {"crds": {"annotations": {}}}}})
    assert not _keeps_values_annotation({"spec": {"values": {"crd": {"annotations": {key: want}}}}})
    assert _keeps_values_keep({"spec": {"values": {"crds": {"keep": True}}}})
    assert not _keeps_values_keep({"spec": {"values": {"crds": {"keep": "true"}}}})
    patch = f"metadata:\n  annotations:\n    {key}: {want}\n"
    renderers = [{"kustomize": {"patches": [
        {"target": {"kind": "CustomResourceDefinition"}, "patch": patch},
    ]}}]
    assert _keeps_post_renderer({"spec": {"postRenderers": renderers}})
    deployment = [{"kustomize": {"patches": [
        {"target": {"kind": "Deployment"}, "patch": patch},
    ]}}]
    assert not _keeps_post_renderer({"spec": {"postRenderers": deployment}})
    assert not _keeps_post_renderer({"spec": {}})
    both = {"install": {"crds": "CreateReplace"}, "upgrade": {"crds": "CreateReplace"}}
    assert _keeps_flux_create_replace({"spec": both})
    assert not _keeps_flux_create_replace({"spec": {"install": {"crds": "CreateReplace"}}})
    skip = {"install": {"crds": "Skip"}, "upgrade": {"crds": "Skip"},
            "values": {"crds": {"enabled": False}}}
    assert _keeps_owned_elsewhere({"spec": skip})
    assert not _keeps_owned_elsewhere({"spec": {**skip, "values": {"crds": {"enabled": True}}}})


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


def test_the_collection_pin_matches_the_ci_lib_ref():
    """ansible/requirements.yml and variables.WEISSSRV_LIB_REF are one pin.

    check-lib-pins.py walks `include:` entries only, so the collection pin,
    which decides which roles reach the cluster, is gated here.
    """
    lib_ref = _pinned_ref()

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
    """requirements.txt and `variables.ANSIBLE_VERSION` install one Ansible.

    Several CI jobs pip-install the variable directly, so the two are copies of
    one pin and a drift makes a local run disagree with the pipeline.
    """
    ci = load_ci_doc(REPO / ".gitlab-ci.yml")
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
    """Every terraform root pins its module `?ref=` at the library release.

    These refs are written by hand and check-lib-pins.py cannot see them, so a
    stale one plans against a different library revision's modules.
    """
    lib_ref = _pinned_ref()

    # Discovered, not enumerated: a fifth root, or a module block moved out of
    # main.tf, would otherwise escape the only gate its `?ref=` pin has. The
    # floor keeps the discovery from silently degrading to a no-op loop.
    tf_root = REPO / "terraform"
    tf_files = sorted(
        p for p in tf_root.rglob("*.tf") if ".terraform" not in p.parts
    )
    assert tf_files, "no .tf files under terraform/ — this gate is examining nothing."
    roots = sorted({p.relative_to(tf_root).parts[0] for p in tf_files})
    assert len(roots) >= 4, (
        f"terraform root discovery found {roots} — expected at least the four "
        "roots under terraform/; this gate must never run on an empty list."
    )

    pinned_roots = set()
    for path in tf_files:
        rel = path.relative_to(REPO).as_posix()
        refs = re.findall(
            r"weisssrv-lib\.git//terraform/modules/[^?\"]+\?ref=([^\"\s]+)",
            path.read_text(),
        )
        if refs:
            pinned_roots.add(path.relative_to(tf_root).parts[0])
        for ref in refs:
            assert ref == str(lib_ref), (
                f"{rel} pins ?ref={ref} but "
                f"WEISSSRV_LIB_REF is {lib_ref!r}. The terraform refs move "
                f"with the library pin — bump them together."
            )
    unpinned = sorted(set(roots) - pinned_roots)
    assert not unpinned, (
        f"terraform roots with no lib module `?ref=` pin: {unpinned}"
    )


def test_molecule_image_literals_match_the_ci_variable():
    """The `${MOLECULE_TEST_IMAGE:-...}` fallbacks match the library pin.

    CI overrides the variable, so a stale literal only bites a local
    `task ansible:test-integration-*`. Enforced by check-molecule-image-pin.py.
    """
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "check-molecule-image-pin.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_no_tenant_onboards_while_traefik_allows_cross_namespace():
    """The docs/30 tenant pre-onboarding checklist, as a build failure.

    The gate fires on the commit that adds a tenant wiring file.
    """
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "check-tenant-traefik-isolation.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert run.returncode == 0, run.stdout + run.stderr


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
    for rel in gate.INVENTORY_VAR_DIRS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    # A directory-per-group file must be read like a flat one.
    nested = tmp_path / "ansible/inventories/prod/group_vars/proxmox/routes.yml"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text('tailscale_advertise_routes:\n  - "10.0.10.0/24"\n')

    good = (
        '{\n'
        '  // comment with a https:// url\n'
        '  "groups": {"group:admins": ["a@example.com"]},\n'
        '  "tagOwners": {"tag:router": ["a@example.com"]},\n'
        '  "acls": [{"action": "accept", "src": ["group:admins"],\n'
        '            "dst": ["tag:router:22,443"]}],\n'
        '  "ssh": [],\n'
        '  "autoApprovers": {"routes": {"10.0.10.0/24": ["tag:router"]}},\n'
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

    policy.write_text(good.replace('"10.0.10.0/24": ', '"10.0.0.0/8": '))
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

    # Only the `data:` mapping is a cluster value; `metadata.name` is not.
    outside = subprocess.run(
        [str(SCRIPTS / "cluster-config-value.sh"), "name"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert outside.returncode != 0, f"printed a non-data key: {outside.stdout!r}"


def test_the_gitlab_backup_companions_are_what_the_wrapper_copies():
    """The GitLab `companions:` globs name the files the wrapper writes.

    A glob naming anything else reports present=0 forever, firing
    BackupArtifactCompanionMissing on a healthy backup.
    """
    data = yaml.safe_load(
        (REPO / "ansible/inventories/prod/host_vars/pve-nas-01.yml").read_text()
    )
    entry = next(
        a for a in data["nas_storage_backup_artifact_apps"] if a["name"] == "gitlab"
    )
    assert entry["companions"] == ["gitlab-secrets.json", "gitlab.rb"]


class TestAlertHostSetsMatchTheInventory:
    """Each existence-witness alert's host character class covers hosts.yml.

    A host missing from the class silences both arms of the witness, so the
    alert reads healthy for a host it no longer covers.
    """

    RULES_DIR = REPO / "kubernetes/infrastructure/observability/rules"
    # alert -> (inventory group or single host, port its exporter listens on).
    # node_exporter_host is on 9101; the k3s DaemonSet owns 9100 on this LAN.
    HOST_WITNESSES = {
        "VzdumpBackupStale": ("proxmox", "9101"),
        "EtcdSnapshotStale": ("k3s_servers", "9101"),
        "ProxmoxHostIOPressure": ("proxmox", "9101"),
        "ProxmoxHostMemoryPressure": ("proxmox", "9101"),
        "NVMeDriveTempWarning": ("proxmox", "9101"),
        "NVMeDriveTempCritical": ("proxmox", "9101"),
        "CPUTempWarning": ("proxmox", "9101"),
        "CPUTempCritical": ("proxmox", "9101"),
        "HostGpuTempWarning": ("proxmox", "9101"),
        "HostGpuTempCritical": ("proxmox", "9101"),
        "NICTempWarning": ("proxmox", "9101"),
        # Only the NAS carries spinning SATA disks.
        "SATADriveTempWarning": ("pve-nas-01", "9101"),
        "SATADriveTempCritical": ("pve-nas-01", "9101"),
        "DNSResolutionDown": ("dns", "53"),
    }
    # LAN matchers with no inventory source: the gateway and switch management
    # addresses, which are UniFi state rather than Ansible hosts.
    UNGATED = {"NetworkGearProbeFailed"}

    @classmethod
    def _hosts_for(cls, source: str) -> set[str]:
        """The inventory hosts a HOST_WITNESSES source names."""
        addresses = _inventory_addresses()
        if source in addresses:
            return {source}
        hosts = set(resolve_hosts(source, group_index(load_inventory(REPO / "ansible/inventories/prod/hosts.yml"))))
        assert hosts, f"{source!r} is neither a host nor a group in hosts.yml"
        return hosts

    @classmethod
    def _matchers(cls) -> dict[str, list[str]]:
        """{alert: [instance regex, ...]} across every rule file.

        The manifest is a plain YAML scalar, so `\\\\.` reaches PromQL as two
        characters and PromQL's own double-quoted string unescapes them to one.
        """
        found: dict[str, list[str]] = {}
        for path in sorted(cls.RULES_DIR.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text())
            if not isinstance(doc, dict) or "spec" not in doc:
                continue
            for group in doc["spec"].get("groups") or []:
                for rule in group.get("rules") or []:
                    alert = rule.get("alert")
                    if not alert:
                        continue
                    patterns = [
                        m.replace("\\\\", "\\")
                        for m in re.findall(r'instance=~"([^"]+)"', rule["expr"])
                    ]
                    if patterns:
                        found.setdefault(alert, []).extend(patterns)
        assert found, "parsed no instance=~ matchers out of the rules directory"
        return found

    # Exact instance= targets with no inventory source: two network-gear probes
    # (UniFi state, not Ansible hosts) and the 1.1.1.1 WAN witness.
    NON_INVENTORY_TARGETS = {"10.0.1.2", "10.0.1.3", "1.1.1.1"}

    @staticmethod
    def _alternatives(pattern: str) -> set[str]:
        """Every string a `(a|b)`-style regex alternation can match."""
        group = re.search(r"\(([^()]*)\)", pattern)
        if not group:
            return set(pattern.split("|"))
        head, tail = pattern[: group.start()], pattern[group.end() :]
        found: set[str] = set()
        for choice in group.group(1).split("|"):
            found |= TestAlertHostSetsMatchTheInventory._alternatives(head + choice + tail)
        return found

    @classmethod
    def _literals(cls) -> dict[str, list[str]]:
        """{alert: [exact instance= target, ...]} across every rule file."""
        found: dict[str, list[str]] = {}
        for path in sorted(cls.RULES_DIR.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text())
            if not isinstance(doc, dict) or "spec" not in doc:
                continue
            for group in doc["spec"].get("groups") or []:
                for rule in group.get("rules") or []:
                    alert = rule.get("alert")
                    if not alert:
                        continue
                    literals = re.findall(r'instance="([^"]+)"', rule["expr"])
                    # An `instance!~` exclusion list pins the same addresses, so
                    # a renumber leaves it excluding something that is gone.
                    for excluded in re.findall(r'instance!~"([^"]+)"', rule["expr"]):
                        literals += [
                            member.replace("\\\\", "\\").replace("\\.", ".")
                            for member in cls._alternatives(excluded)
                        ]
                    if literals:
                        found.setdefault(alert, []).extend(literals)
        assert found, "parsed no exact instance= targets out of the rules directory"
        return found

    @classmethod
    def _literal_hosts(cls) -> dict[str, str]:
        """{address: alert} for every exact target spelled as a dotted quad."""
        return {
            literal.rsplit(":", 1)[0]: alert
            for alert, literals in cls._literals().items()
            for literal in literals
            if literal.rsplit(":", 1)[0].count(".") == 3
        }

    def test_every_exact_instance_literal_is_an_inventory_host(self):
        """An exact target escapes the regex parity above: after a renumber the
        arm matches nothing and the alert reads healthy forever."""
        gateway = yaml.safe_load(
            (REPO / "kubernetes/infrastructure/sources/cluster-config.yaml").read_text()
        )["data"]["cluster_lan_gateway"]
        known = (
            set(_inventory_addresses().values()) | self.NON_INVENTORY_TARGETS | {gateway}
        )
        unknown = sorted(
            f"{alert} ({address})"
            for address, alert in self._literal_hosts().items()
            if address not in known
        )
        assert not unknown, (
            f"alerts pinning an address that is no ansible_host: {unknown} — "
            "renumber the rule with the inventory, or add the address to "
            "NON_INVENTORY_TARGETS with the reason it has no inventory source."
        )

    def test_a_renumbered_exclusion_member_is_caught(self, tmp_path, monkeypatch):
        """Mutation case: a quad left behind in an `instance!~` list excludes an
        address nothing serves, so the suppressor arm never fires."""
        (tmp_path / "bogus.yaml").write_text(
            "spec:\n"
            "  groups:\n"
            "    - name: t\n"
            "      rules:\n"
            "        - alert: Bogus\n"
            '          expr: probe_success{instance!~"10\\\\.9\\\\.9\\\\.9|1\\\\.1\\\\.1\\\\.1"}\n'
        )
        monkeypatch.setattr(TestAlertHostSetsMatchTheInventory, "RULES_DIR", tmp_path)
        with pytest.raises(AssertionError, match="10.9.9.9"):
            self.test_every_exact_instance_literal_is_an_inventory_host()

    def test_the_non_inventory_targets_are_all_still_pinned(self):
        """The allowlist cannot outlive the rules that need it."""
        stale = sorted(self.NON_INVENTORY_TARGETS - set(self._literal_hosts()))
        assert not stale, f"no alert pins these addresses any more: {stale}"

    @classmethod
    def _lan_prefixes(cls) -> set[str]:
        return {
            address.rsplit(".", 1)[0]
            for address in _inventory_addresses().values()
            if address.count(".") == 3
        }

    def test_every_alert_pinning_lan_hosts_is_gated(self):
        """A new hand-written host class must not arrive ungated: the point of
        this suite is that nobody eyeballs these character classes."""
        prefixes = self._lan_prefixes()
        pinned = {
            alert
            for alert, patterns in self._matchers().items()
            if any(prefix.replace(".", "\\.") in pattern
                   for pattern in patterns for prefix in prefixes)
        }
        ungated = sorted(pinned - set(self.HOST_WITNESSES) - self.UNGATED)
        assert not ungated, (
            f"alerts spelling a LAN host set with no inventory cross-check: {ungated} "
            "— add it to HOST_WITNESSES with the group it claims, or to UNGATED "
            "with the reason no inventory source exists."
        )
        stale = sorted((set(self.HOST_WITNESSES) | self.UNGATED) - set(self._matchers()))
        assert not stale, f"these alerts no longer carry an instance=~ matcher: {stale}"

    @pytest.mark.parametrize("alert,source,port",
                             sorted((a, s, p) for a, (s, p) in HOST_WITNESSES.items()))
    def test_the_witness_covers_exactly_its_inventory_hosts(self, alert, source, port):
        patterns = self._matchers()[alert]
        assert patterns, f"{alert} has no instance=~ witness arm any more"
        addresses = _inventory_addresses()
        members = self._hosts_for(source)

        def selects(address: str) -> bool:
            return any(re.fullmatch(p, f"{address}:{port}") for p in patterns)

        uncovered = sorted(
            f"{host} ({addresses[host]})"
            for host in members
            if host in addresses and not selects(addresses[host])
        )
        assert not uncovered, (
            f"{alert}'s instance regex {patterns!r} does not select {uncovered} — "
            f"those {source} hosts are outside the witness, so the alert reads "
            "healthy for a host emitting nothing. Widen the character class."
        )

        overreach = sorted(
            f"{host} ({address})"
            for host, address in addresses.items()
            if host not in members and selects(address)
        )
        assert not overreach, (
            f"{alert}'s instance regex {patterns!r} also selects {overreach}, which "
            f"are not in {source} — the witness would fire for hosts that are not "
            "supposed to emit the metric at all."
        )

    def test_the_ha_guest_alert_matches_the_ha_resource_list(self):
        """HAInfraGuestDown enumerates VMIDs; proxmox_ha_resources is the list
        Proxmox is actually configured from."""
        alert = "HAInfraGuestDown"
        rules = [
            rule
            for path in sorted(self.RULES_DIR.glob("*.yaml"))
            for doc in [yaml.safe_load(path.read_text())]
            if isinstance(doc, dict) and "spec" in doc
            for group in doc["spec"].get("groups") or []
            for rule in group.get("rules") or []
            if rule.get("alert") == alert
        ]
        assert len(rules) == 1, f"expected exactly one {alert} rule, found {len(rules)}"
        match = re.search(r'id=~"([^"]+)"', rules[0]["expr"])
        assert match, f"{alert} no longer enumerates guest ids"
        pattern = match.group(1).replace("\\\\", "\\")

        all_vars = yaml.safe_load(
            (REPO / "ansible/inventories/prod/group_vars/all.yml").read_text()
        )
        managed = all_vars.get("proxmox_ha_resources") or []
        assert managed, "proxmox_ha_resources resolved empty"
        kinds = {"ct": "lxc", "vm": "qemu"}
        missing = sorted(
            f"{kinds[res['type']]}/{res['vmid']}"
            for res in managed
            if not re.fullmatch(pattern, f"{kinds[res['type']]}/{res['vmid']}")
        )
        assert not missing, (
            f"{alert}'s id regex {pattern!r} does not select {missing} — those "
            "guests are HA-managed but outside the alert."
        )


def test_the_pytest_pin_matches_the_ci_variable():
    """requirements.txt and `variables.PYTEST_VERSION` install the same pytest.

    `task lint` runs the script tests from the local install and CI installs
    the variable's version, so the two are copies of one pin.
    """
    ci = load_ci_doc(REPO / ".gitlab-ci.yml")
    ci_pin = (ci.get("variables") or {}).get("PYTEST_VERSION")
    assert ci_pin, "variables.PYTEST_VERSION is where the CI jobs read the pin"
    match = re.search(
        r"^pytest==([^\s#]+)", (REPO / "requirements.txt").read_text(), re.MULTILINE
    )
    assert match, "requirements.txt no longer pins `pytest==`"
    assert match.group(1) == str(ci_pin), (
        f"requirements.txt pins pytest=={match.group(1)} but .gitlab-ci.yml's "
        f"PYTEST_VERSION is {ci_pin!r}. They are one pin — bump both."
    )


def test_the_ruff_pin_matches_the_ci_variable_and_the_include_input():
    """requirements.txt, `variables.RUFF_VERSION` and the python-lint include agree.

    A drift between the three copies means a rule the local run does not see.
    """
    ci = load_ci_doc(REPO / ".gitlab-ci.yml")
    ci_pin = (ci.get("variables") or {}).get("RUFF_VERSION")
    assert ci_pin, "variables.RUFF_VERSION is where the CI jobs read the pin"
    match = re.search(
        r"^ruff==([^\s#]+)", (REPO / "requirements.txt").read_text(), re.MULTILINE
    )
    assert match, "requirements.txt no longer pins `ruff==`"
    assert match.group(1) == str(ci_pin), (
        f"requirements.txt pins ruff=={match.group(1)} but .gitlab-ci.yml's "
        f"RUFF_VERSION is {ci_pin!r}. They are one pin — bump both."
    )
    inputs = re.findall(r'^\s*ruff_version:\s*"([^"]+)"', (REPO / ".gitlab-ci.yml").read_text(), re.M)
    assert inputs, "the python-lint include no longer passes ruff_version"
    off = [v for v in inputs if v != str(ci_pin)]
    assert not off, (
        f"python-lint's ruff_version input is {off} but RUFF_VERSION is {ci_pin!r}."
    )


def test_the_dind_service_matches_the_library_input_default():
    """.gitlab/ci/integration-jobs.yml matches the library's dind service.

    Nothing else holds this local job's digest-pinned daemon equal to the
    library's `dind_service` default, so a bump would leave it stale.
    """
    from test_vendored_byte_identity import _lib_root, _ref_available

    lib = _lib_root()
    ref = _pinned_ref()
    relpath = "ci/build/docker-build.yml"
    if _ref_available(lib, ref):
        blob = subprocess.run(
            ["git", "-C", str(lib), "show", f"{ref}:{relpath}"],
            capture_output=True, text=True,
        )
        assert blob.returncode == 0, blob.stderr
        text = blob.stdout
    else:
        text = (lib / relpath).read_text()

    # A template file is two documents: the `spec.inputs` header, then the jobs.
    header = next(
        (d for d in yaml.load_all(text, Loader=_CILoader) if isinstance(d, dict) and "spec" in d),
        {},
    )
    inputs = (header.get("spec") or {}).get("inputs") or {}
    default = (inputs.get("dind_service") or {}).get("default")
    assert default, f"{relpath} no longer declares a dind_service default"

    jobs = load_ci_doc(REPO / ".gitlab/ci/integration-jobs.yml")
    services = [
        service
        for job in jobs.values()
        if isinstance(job, dict)
        for service in (job.get("services") or [])
    ]
    names = {s["name"] if isinstance(s, dict) else s for s in services}
    dind = {n for n in names if "dind" in str(n)}
    assert dind, "no dind service found in .gitlab/ci/integration-jobs.yml"
    assert dind == {default}, (
        f"integration-jobs.yml runs {sorted(dind)} but the library's "
        f"dind_service default at {ref} is {default!r} — they are one pin."
    )


class TestSourcesStage:
    """kubernetes/infrastructure/sources is the one stage with no
    substituteFrom, and its kustomization is the only thing that puts a file
    into the build."""

    SOURCES = REPO / "kubernetes/infrastructure/sources"

    def test_sources_stage_carries_no_placeholders(self):
        for path in sorted(self.SOURCES.glob("*.yaml")):
            body = "\n".join(
                line for line in path.read_text().splitlines()
                if not line.lstrip().startswith("#")
            )
            assert "${" not in body.replace("$${", "__FLUX_ESCAPED__"), (
                f"{path.name}: this stage has no substituteFrom, so a ${{...}} "
                "placeholder ships to the cluster as a literal string."
            )

    def test_sources_resources_list_is_complete(self):
        listed = set(
            yaml.safe_load((self.SOURCES / "kustomization.yaml").read_text())["resources"]
        )
        on_disk = {p.name for p in self.SOURCES.glob("*.yaml")} - {"kustomization.yaml"}
        assert listed == on_disk, (
            f"unlisted (kustomize ignores them, and a HelmRelease then stalls on a "
            f"missing source): {sorted(on_disk - listed)}; listed but absent (the "
            f"build fails): {sorted(listed - on_disk)}"
        )


class TestApiserverEgressPeers:
    """Every egress rule on TCP 6443 names the k3s servers, the API VIP or both.

    Derived from hosts.yml, so a renumber or a fourth server reds it.
    """

    VIP_KEY = "k3s_api_vip"

    @staticmethod
    def _rules(root: Path):
        """(file, policy name, sorted ipBlock CIDRs) per egress rule on 6443."""
        for path in sorted(root.glob("kubernetes/**/*.yaml")):
            try:
                docs = list(yaml.safe_load_all(path.read_text()))
            except yaml.YAMLError as exc:
                raise AssertionError(
                    f"{path}: unparseable YAML ({exc.__class__.__name__}) — a "
                    "NetworkPolicy here would drop out of the 6443 peer check"
                ) from exc
            for doc in docs:
                if not isinstance(doc, dict) or doc.get("kind") != "NetworkPolicy":
                    continue
                for rule in (doc.get("spec") or {}).get("egress") or []:
                    if not any(
                        str(p.get("port")) == "6443" for p in (rule.get("ports") or [])
                    ):
                        continue
                    yield (
                        str(path.relative_to(root)),
                        doc["metadata"]["name"],
                        {
                            peer["ipBlock"]["cidr"]
                            for peer in (rule.get("to") or [])
                            if "ipBlock" in peer
                        },
                    )

    @classmethod
    def _expected(cls) -> tuple[set[str], str]:
        servers = set(resolve_hosts("k3s_servers", group_index(load_inventory(REPO / "ansible/inventories/prod/hosts.yml"))))
        assert servers, "k3s_servers resolved empty out of hosts.yml"
        addresses = _inventory_addresses()
        k3s_vars = yaml.safe_load(
            (REPO / "ansible/inventories/prod/group_vars/k3s.yml").read_text()
        )
        vip = str(k3s_vars[cls.VIP_KEY])
        return {f"{addresses[h]}/32" for h in servers}, f"{vip}/32"

    @staticmethod
    def _offenders(rules, servers: set[str], vip: str) -> list[str]:
        allowed = (servers, servers | {vip}, {vip})
        return [
            f"{path}:{name} allows 6443 to {sorted(cidrs)}"
            for path, name, cidrs in rules
            if cidrs and cidrs not in allowed
        ]

    def test_every_apiserver_egress_names_the_inventory_servers(self):
        servers, vip = self._expected()
        rules = list(self._rules(REPO))
        assert rules, "parsed no 6443 egress rules out of kubernetes/"
        offenders = self._offenders(rules, servers, vip)
        assert not offenders, (
            f"{offenders} — the k3s servers are {sorted(servers)} and the API VIP "
            f"is {vip}. A rule that misses a server loses the apiserver whenever "
            "that server is the leader."
        )

    def test_a_dropped_server_address_is_reported(self):
        """The collector, not just the corpus: a policy short one server fails."""
        servers, vip = self._expected()
        short = set(sorted(servers)[1:])
        synthetic = [("kubernetes/apps/x/networkpolicy.yaml", "allow-egress-x", short)]
        assert self._offenders(synthetic, servers, vip)


def test_the_node_exporter_endpointslice_matches_the_inventory():
    """The host node_exporter targets are a hand-written EndpointSlice, so a
    host added to the play's group list is scraped by nothing until it is also
    added here."""
    role = "weisssrv.infra.node_exporter_host"
    plays = [
        play
        for play in yaml.safe_load((REPO / "ansible/playbooks/site.yml").read_text())
        if any(
            (r.get("role") if isinstance(r, dict) else r) == role
            for r in (play.get("roles") or [])
        )
    ]
    assert len(plays) == 1, f"expected one {role} play in site.yml, found {len(plays)}"
    index = group_index(load_inventory(REPO / "ansible/inventories/prod/hosts.yml"))
    hosts: set[str] = set()
    for token in str(plays[0]["hosts"]).split(":"):
        resolved = set(resolve_hosts(token.lstrip("!"), index))
        if token.startswith("!"):
            hosts -= resolved
        else:
            hosts |= resolved
    assert hosts, f"{plays[0]['hosts']} resolved to no hosts"

    addresses = _inventory_addresses()
    expected = {addresses[h] for h in hosts if h in addresses}
    slices = [
        doc
        for doc in yaml.safe_load_all(
            (REPO / "kubernetes/infrastructure/observability/exporters/"
             "node-exporter-host.yaml").read_text()
        )
        if isinstance(doc, dict) and doc.get("kind") == "EndpointSlice"
    ]
    assert len(slices) == 1, f"expected one EndpointSlice, found {len(slices)}"
    listed = {a for e in slices[0]["endpoints"] for a in e["addresses"]}
    assert listed == expected, (
        f"scraped but not in the play: {sorted(listed - expected)}; in the play but "
        f"unscraped: {sorted(expected - listed)}"
    )


def test_an_unreadable_tailscale_policy_is_a_clean_failure(tmp_path):
    """A renamed or missing policy.hujson reports, rather than tracebacks."""
    gate = _load("check-tailscale-policy.py")
    problems = gate.check(tmp_path)
    assert problems and "cannot be read" in problems[0], problems


def test_a_missing_tailscale_policy_exits_two_not_one(tmp_path):
    """rc 1 would read as a policy the gate judged unsafe; nothing was read."""
    gate = _load("check-tailscale-policy.py")
    assert gate.main(["--repo", str(tmp_path)]) == 2


def test_an_unparsable_inventory_var_file_is_an_operator_error(tmp_path):
    gate = _load("check-tailscale-policy.py")
    var_dir = tmp_path / gate.INVENTORY_VAR_DIRS[0]
    var_dir.mkdir(parents=True)
    (var_dir / "routes.yml").write_text("tailscale_advertise_routes: [\n")
    with pytest.raises(gate.OperatorError):
        gate.advertised_routes(tmp_path)


# Every OIDC consumer URL carries the issuer host in the path
# https://<host>/application/o/<slug>/, which Authentik derives from its BASE_URL.
_OIDC_URL = re.compile(r"https://(?P<host>[^/\s\"'`]+)/application/o/")
_BASE_URL_ENV = "AUTHENTIK_WEB__BASE_URL"


def _env_values(node, name: str):
    """Every `value:` of an env entry called `name`, at any depth."""
    if isinstance(node, dict):
        if node.get("name") == name and isinstance(node.get("value"), str):
            yield node["value"]
        for child in node.values():
            yield from _env_values(child, name)
    elif isinstance(node, list):
        for child in node:
            yield from _env_values(child, name)


def oidc_issuer_hosts(release: Path) -> set[str]:
    """Hosts spelled in the IdP's AUTHENTIK_WEB__BASE_URL env entries."""
    hosts = set()
    for doc in yaml.safe_load_all(release.read_text()):
        for value in _env_values(doc, _BASE_URL_ENV):
            hosts.add(value.removeprefix("https://").removeprefix("http://").split("/")[0])
    return hosts


def oidc_issuer_problems(release: Path, root: Path) -> list[str]:
    """Consumer OIDC URLs under `root` that name a host the IdP does not publish."""
    hosts = oidc_issuer_hosts(release)
    if len(hosts) != 1:
        return [
            f"{release}: expected exactly one {_BASE_URL_ENV} host, found "
            f"{sorted(hosts) or 'none'}"
        ]
    issuer = hosts.pop()
    problems = []
    for path in sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")}):
        for match in _OIDC_URL.finditer(path.read_text()):
            if match["host"] != issuer:
                problems.append(
                    f"{path}: OIDC URL points at {match['host']}, but the IdP "
                    f"publishes {issuer}"
                )
    return problems


_EXPECTED_BASE_URL = "https://auth.${cluster_external_domain}"

# The chart gives server and worker separate env lists, so both are checked.
_BASE_URL_COMPONENTS = ("server", "worker")


def base_url_gaps(release_doc: dict, expected: str) -> list[str]:
    """Authentik components whose own env does not publish `expected`."""
    values = ((release_doc.get("spec") or {}).get("values")) or {}
    gaps = []
    for component in _BASE_URL_COMPONENTS:
        env = (values.get(component) or {}).get("env") or []
        if expected not in [
            entry.get("value") for entry in env if entry.get("name") == _BASE_URL_ENV
        ]:
            gaps.append(
                f"spec.values.{component}.env sets no {_BASE_URL_ENV} of {expected}"
            )
    return gaps


class TestOidcIssuerHost:
    """Authentik signs tokens for the host it publishes as BASE_URL, so a
    consumer pointed at any other spelling fails validation at login."""

    RELEASE = REPO / "kubernetes/apps/authentik/release.yaml"

    def test_the_idp_publishes_one_issuer_host(self):
        assert oidc_issuer_hosts(self.RELEASE) == {"auth.${cluster_external_domain}"}

    def test_every_consumer_uses_the_issuer_host(self):
        problems = oidc_issuer_problems(self.RELEASE, REPO / "kubernetes")
        assert not problems, "\n".join(problems)

    def test_a_consumer_on_the_internal_host_is_caught(self, tmp_path):
        release = tmp_path / "release.yaml"
        release.write_text(
            "env:\n"
            f"  - name: {_BASE_URL_ENV}\n"
            '    value: "https://auth.external.test"\n'
        )
        root = tmp_path / "apps"
        root.mkdir()
        (root / "app.yaml").write_text(
            'value: https://auth.internal.test/application/o/app/\n'
        )
        problems = oidc_issuer_problems(release, root)
        assert problems and "auth.internal.test" in problems[0], problems

    def test_an_idp_with_two_base_urls_is_caught(self, tmp_path):
        release = tmp_path / "release.yaml"
        release.write_text(
            "env:\n"
            f"  - name: {_BASE_URL_ENV}\n"
            '    value: "https://auth.external.test"\n'
            f"  - name: {_BASE_URL_ENV}\n"
            '    value: "https://auth.internal.test"\n'
        )
        root = tmp_path / "apps"
        root.mkdir()
        problems = oidc_issuer_problems(release, root)
        assert problems and "exactly one" in problems[0], problems

    def test_both_authentik_components_publish_the_base_url(self):
        """The host assertion above is satisfied by one entry. Server and worker
        each read their own env, so a component that loses it falls back to the
        request host and signs tokens for the internal name."""
        doc = yaml.safe_load(self.RELEASE.read_text())
        assert not base_url_gaps(doc, _EXPECTED_BASE_URL)

    def test_a_component_missing_the_base_url_is_caught(self):
        doc = {"spec": {"values": {
            "server": {"env": [{"name": _BASE_URL_ENV, "value": _EXPECTED_BASE_URL}]},
            "worker": {"env": [{"name": "OTHER", "value": "x"}]},
        }}}
        gaps = base_url_gaps(doc, _EXPECTED_BASE_URL)
        assert gaps and "worker" in gaps[0], gaps

    def test_a_component_on_the_internal_host_is_caught(self):
        doc = {"spec": {"values": {
            "server": {"env": [{"name": _BASE_URL_ENV, "value": _EXPECTED_BASE_URL}]},
            "worker": {"env": [{"name": _BASE_URL_ENV,
                                "value": "https://auth.${cluster_internal_domain}"}]},
        }}}
        gaps = base_url_gaps(doc, _EXPECTED_BASE_URL)
        assert gaps and "worker" in gaps[0], gaps


# external-dns >= 0.22 defaults to external-dns.kubernetes.io/; the manifests
# here annotate with the alpha prefix, and the mismatch prunes every record.
_EXTERNAL_DNS_RELEASE = REPO / "kubernetes/infrastructure/controllers/external-dns/release.yaml"
_ANNOTATION_PREFIX = "external-dns.alpha.kubernetes.io/"
_ANNOTATION_PREFIX_ARG = f"--annotation-prefix={_ANNOTATION_PREFIX}"
_EXTERNAL_DNS_ANNOTATION_RE = re.compile(r"^external-dns(\.alpha)?\.kubernetes\.io/")


def _annotation_keys(node):
    """Every annotation key in a document, including nested pod templates."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "annotations" and isinstance(value, dict):
                yield from value
            else:
                yield from _annotation_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _annotation_keys(item)


def _external_dns_annotations(root: Path) -> list[tuple[str, str]]:
    """(relative path, annotation key) per external-dns annotation under `root`."""
    found = []
    for path in sorted(root.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            for key in _annotation_keys(doc):
                if _EXTERNAL_DNS_ANNOTATION_RE.match(key):
                    found.append((str(path.relative_to(root.parent)), key))
    return sorted(found)


def _off_prefix(found: list[tuple[str, str]], prefix: str) -> list[str]:
    return [f"{rel}: {key}" for rel, key in found if not key.startswith(prefix)]


def test_external_dns_pins_the_annotation_prefix():
    doc = yaml.safe_load(_EXTERNAL_DNS_RELEASE.read_text())
    extra_args = ((doc.get("spec") or {}).get("values") or {}).get("extraArgs") or []
    assert _ANNOTATION_PREFIX_ARG in extra_args, (
        f"external-dns dropped {_ANNOTATION_PREFIX_ARG}: it stops seeing the "
        "annotations the manifests carry and prunes the records it owns"
    )


def test_external_dns_annotations_match_the_pinned_prefix():
    """The flag and the annotations are one invariant: a key spelled with the
    other prefix is invisible to external-dns, so the record is never published
    and nothing reports it."""
    found = _external_dns_annotations(REPO / "kubernetes")
    assert found, "no external-dns annotation under kubernetes/ — this gate examined nothing"
    offenders = _off_prefix(found, _ANNOTATION_PREFIX)
    assert not offenders, (
        f"external-dns annotations outside {_ANNOTATION_PREFIX}, the prefix the "
        "external-dns HelmRelease pins: external-dns never sees them and "
        "publishes no record for them:\n  " + "\n  ".join(offenders)
    )


def test_an_annotation_outside_the_pinned_prefix_is_reported(tmp_path):
    """Mutation case: the 0.22 default spelling, and a nested pod-template
    annotation the walk still reaches."""
    root = tmp_path / "kubernetes"
    root.mkdir()
    (root / "route.yaml").write_text(
        "kind: IngressRoute\nmetadata:\n  annotations:\n"
        "    external-dns.kubernetes.io/hostname: app.example.com\n"
        "    kubernetes.io/ingress.class: traefik\n"
    )
    (root / "deployment.yaml").write_text(
        "kind: Deployment\nspec:\n  template:\n    metadata:\n      annotations:\n"
        f"        {_ANNOTATION_PREFIX}target: example.com\n"
    )
    found = _external_dns_annotations(root)
    assert len(found) == 2
    assert _off_prefix(found, _ANNOTATION_PREFIX) == [
        "kubernetes/route.yaml: external-dns.kubernetes.io/hostname"
    ]


# ClusterIssuer names: configs/cluster-issuer.yaml declares them, ~20 issuerRef
# fields name one by literal string. A typo or a staging/prod mix-up resolves
# and issues nothing.

CLUSTER_ISSUER_DIR = REPO / "kubernetes/infrastructure/configs"
MANIFEST_ROOT = REPO / "kubernetes"


def _declared_cluster_issuers(root: Path) -> set[str]:
    names = set()
    for path in sorted(root.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "ClusterIssuer":
                name = (doc.get("metadata") or {}).get("name")
                if name:
                    names.add(str(name))
    return names


def _cluster_issuer_refs(node, rel: str, found: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        ref = node.get("issuerRef")
        if isinstance(ref, dict) and ref.get("kind") == "ClusterIssuer" and ref.get("name"):
            found.append((rel, str(ref["name"])))
        for value in node.values():
            _cluster_issuer_refs(value, rel, found)
    elif isinstance(node, list):
        for value in node:
            _cluster_issuer_refs(value, rel, found)


def cluster_issuer_refs(root: Path) -> list[tuple[str, str]]:
    """(relative path, issuerRef name) per ClusterIssuer reference under `root`."""
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.yaml")):
        rel = str(path.relative_to(root.parent))
        for doc in yaml.safe_load_all(path.read_text()):
            _cluster_issuer_refs(doc, rel, found)
    return sorted(found)


def unknown_cluster_issuer_refs(
    refs: list[tuple[str, str]], declared: set[str]
) -> list[str]:
    return [
        f"{rel}: issuerRef {name!r} is no declared ClusterIssuer"
        for rel, name in refs
        if name not in declared
    ]


def test_both_sides_of_the_cluster_issuer_check_are_populated():
    assert _declared_cluster_issuers(CLUSTER_ISSUER_DIR), (
        f"{CLUSTER_ISSUER_DIR.relative_to(REPO)} declares no ClusterIssuer, so the "
        "name parity arm below would compare against an empty set"
    )
    assert cluster_issuer_refs(MANIFEST_ROOT), (
        "no issuerRef names a ClusterIssuer under kubernetes/ — this gate examined nothing"
    )


def test_every_issuer_ref_names_a_declared_cluster_issuer():
    problems = unknown_cluster_issuer_refs(
        cluster_issuer_refs(MANIFEST_ROOT), _declared_cluster_issuers(CLUSTER_ISSUER_DIR)
    )
    assert not problems, (
        "issuerRef names no ClusterIssuer this repo declares; cert-manager leaves "
        "the Certificate pending and issues nothing:\n  " + "\n  ".join(problems)
    )


def test_an_unknown_or_nested_issuer_ref_is_reported(tmp_path):
    """Mutation case: a typo, and a ClusterIssuer under a nested directory the
    declaration walk still reaches."""
    configs = tmp_path / "configs/nested"
    configs.mkdir(parents=True)
    (configs / "issuer.yaml").write_text(
        "apiVersion: cert-manager.io/v1\nkind: ClusterIssuer\n"
        "metadata:\n  name: letsencrypt-prod\n"
    )
    declared = _declared_cluster_issuers(tmp_path / "configs")
    assert declared == {"letsencrypt-prod"}

    root = tmp_path / "kubernetes"
    root.mkdir()
    (root / "cert.yaml").write_text(
        "kind: Certificate\nspec:\n  issuerRef:\n"
        "    name: letsencrypt-prd\n    kind: ClusterIssuer\n"
        "---\n"
        "kind: Certificate\nspec:\n  issuerRef:\n"
        "    name: letsencrypt-prod\n    kind: ClusterIssuer\n"
    )
    refs = cluster_issuer_refs(root)
    assert len(refs) == 2
    assert unknown_cluster_issuer_refs(refs, declared) == [
        "kubernetes/cert.yaml: issuerRef 'letsencrypt-prd' is no declared ClusterIssuer"
    ]


def test_a_namespaced_issuer_ref_is_out_of_scope(tmp_path):
    """kind: Issuer resolves in its own namespace, not against the ClusterIssuer set."""
    root = tmp_path / "kubernetes"
    root.mkdir()
    (root / "cert.yaml").write_text(
        "kind: Certificate\nspec:\n  issuerRef:\n    name: selfsigned\n    kind: Issuer\n"
    )
    assert cluster_issuer_refs(root) == []


# A lint include's `targets:` names the paths it scans and its `changes:` names
# the paths that create the job. A target widened without a matching `changes:`
# entry gives a job that never runs on the commits it exists for.
def targets_without_changes(entry: dict) -> list[str]:
    """Targets in one include that no `changes:` glob covers."""
    inputs = entry.get("inputs") or {}
    targets = str(inputs.get("targets") or "").split()
    globs = [str(g) for g in inputs.get("changes") or []]
    return sorted(
        t for t in targets
        if t != "." and not any(g == t or g.startswith(t.rstrip("/") + "/") for g in globs)
    )


def test_every_lint_include_triggers_on_the_paths_it_scans():
    ci = load_ci_doc(REPO / ".gitlab-ci.yml")
    checked = 0
    for entry in ci.get("include") or []:
        if not isinstance(entry, dict):
            continue
        inputs = entry.get("inputs") or {}
        if not (inputs.get("targets") and inputs.get("changes")):
            continue
        checked += 1
        missing = targets_without_changes(entry)
        assert not missing, (
            f"{entry.get('file')} scans {missing} but its `changes:` list never "
            "fires on those paths, so the job is skipped on the very commits it "
            "exists for — widen `changes:` alongside `targets:`"
        )
    assert checked, "no include passes both `targets` and `changes` — this gate read nothing"


def test_a_target_outside_the_changes_list_is_detected():
    """The mutation the gate exists for: a target widened on its own."""
    entry = {"inputs": {"targets": "scripts kubernetes", "changes": ["scripts/**/*.py"]}}
    assert targets_without_changes(entry) == ["kubernetes"]
    entry["inputs"]["changes"].append("kubernetes/**/*.py")
    assert targets_without_changes(entry) == []
