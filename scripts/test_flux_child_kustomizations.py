"""Unit tests for scripts/flux-child-kustomizations.py.

TestSubstituteFrom gates a second invariant over the same files: every stage
after `sources` substitutes from both cluster-versions and cluster-config.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from script_loader import load_path


SCRIPT = Path(__file__).resolve().parent / "flux-child-kustomizations.py"
REPO = SCRIPT.parent.parent
CLUSTER_DIR = REPO / "kubernetes" / "clusters" / "weisssrv"


def _load():
    return load_path(SCRIPT)


def _run(directory: Path | None = None) -> list[str]:
    cmd = [sys.executable, str(SCRIPT)]
    if directory is not None:
        cmd += ["--dir", str(directory)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.split()


class TestUnreadableInput:
    """Callers derive the stage list from stdout, so an unparseable manifest is
    an operator error (exit 2), not a short list."""

    def test_a_malformed_manifest_exits_two(self, tmp_path: Path):
        (tmp_path / "apps.yaml").write_text(
            textwrap.dedent(
                """\
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: apps
                spec:
                  path: ./kubernetes/apps
                """
            )
        )
        (tmp_path / "broken.yaml").write_text("a: [1,\n  b: {\n")
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert res.returncode == 2, res.stdout + res.stderr
        assert "broken.yaml" in res.stderr
        assert "apps" not in res.stdout


class TestAgainstTheRealCluster:
    def test_every_declared_kustomization_is_listed(self):
        import yaml

        declared = set()
        for path in CLUSTER_DIR.glob("*.yaml"):
            with path.open() as fh:
                for doc in yaml.safe_load_all(fh):
                    if (
                        isinstance(doc, dict)
                        and doc.get("kind") == "Kustomization"
                        and str(doc.get("apiVersion", "")).startswith("kustomize.toolkit")
                    ):
                        declared.add(doc["metadata"]["name"])
        assert declared, "no Kustomizations parsed from the cluster dir"
        assert set(_run()) == declared

    def test_infrastructure_crds_is_present(self):
        assert "infrastructure-crds" in _run()

    def test_dependson_order_is_respected(self):
        out = _run()
        for earlier, later in [
            ("infrastructure-sources", "infrastructure-crds"),
            ("infrastructure-crds", "infrastructure-controllers"),
            ("infrastructure-controllers", "infrastructure-configs"),
            ("infrastructure-configs", "apps"),
            ("infrastructure-configs", "infrastructure-observability"),
        ]:
            assert out.index(earlier) < out.index(later), f"{earlier} must precede {later}"


class TestSubstituteFrom:
    """Every Kustomization after `sources` substitutes from exactly the two
    ConfigMaps, both non-optional. A stage missing one lints green and then
    ships a literal `${...}` that Flux renders as an empty string.
    """

    REQUIRED = {"cluster-versions", "cluster-config"}
    # infrastructure-sources is where both ConfigMaps live, so it has nothing to
    # substitute from; flux-system is the upstream bootstrap Kustomization.
    EXEMPT = {"infrastructure-sources", "flux-system"}
    # Tenant wiring files are not platform stages: a tenant pins its own image
    # versions, so kubernetes/clusters/weisssrv/tenants/README.md lists
    # cluster-config only.
    EXEMPT_TREES = ("kubernetes/clusters/weisssrv/tenants/",)

    def _declared(self):
        import yaml

        found = []
        manifests = {
            path
            for suffix in ("*.yaml", "*.yml")
            for path in REPO.glob(f"kubernetes/**/{suffix}")
        }
        for path in sorted(manifests):
            for doc in yaml.safe_load_all(path.read_text()):
                if (
                    isinstance(doc, dict)
                    and doc.get("kind") == "Kustomization"
                    and str(doc.get("apiVersion", "")).startswith("kustomize.toolkit")
                ):
                    found.append((path, doc))
        assert found, "no Flux Kustomizations parsed from kubernetes/"
        return found

    def test_every_stage_substitutes_from_both_configmaps(self):
        problems = []
        for path, doc in self._declared():
            name = doc.get("metadata", {}).get("name", "")
            if name in self.EXEMPT:
                continue
            if any(
                str(path.relative_to(REPO)).startswith(tree) for tree in self.EXEMPT_TREES
            ):
                continue
            entries = (doc.get("spec", {}).get("postBuild", {}) or {}).get("substituteFrom") or []
            names = {e.get("name") for e in entries if e.get("kind") == "ConfigMap"}
            rel = path.relative_to(REPO)
            if names != self.REQUIRED:
                problems.append(f"{rel} ({name}): substitutes from {sorted(names)}")
                continue
            optional = [e.get("name") for e in entries if e.get("optional") is not False]
            if optional:
                problems.append(f"{rel} ({name}): {optional} must be optional: false")
        assert not problems, (
            "Flux Kustomizations with an incomplete substituteFrom:\n  "
            + "\n  ".join(problems)
            + "\n\nEvery stage after infrastructure-sources must list BOTH "
            "cluster-versions and cluster-config with optional: false — a missing "
            "one renders its placeholders as empty strings at reconcile time."
        )

    def test_the_exemptions_still_name_real_kustomizations(self):
        names = {doc.get("metadata", {}).get("name") for _p, doc in self._declared()}
        assert self.EXEMPT <= names, f"stale exemptions: {sorted(self.EXEMPT - names)}"

    # The one nested pair: infrastructure-controllers omits metrics-server from
    # its resources list, so only one Kustomization owns the HelmRelease.
    # Re-adding it there makes each stage's prune target the other's object.
    NESTED_PATHS = {("infrastructure-metrics-server", "infrastructure-controllers")}

    def test_no_stage_path_nests_inside_another(self):
        paths = {}
        for _path, doc in self._declared():
            spec_path = (doc.get("spec", {}) or {}).get("path")
            if spec_path:
                paths[doc["metadata"]["name"]] = spec_path.lstrip("./").rstrip("/")
        assert paths, "parsed no spec.path values — this gate examined nothing"
        overlaps = {
            (inner, outer)
            for inner, inner_path in paths.items()
            for outer, outer_path in paths.items()
            if inner != outer and inner_path.startswith(outer_path + "/")
        }
        unexpected = sorted(overlaps - self.NESTED_PATHS)
        assert not unexpected, (
            "Flux Kustomization paths nest inside another stage's path:\n  "
            + "\n  ".join(f"{i} inside {o}" for i, o in unexpected)
            + "\n\nTwo stages that render the same directory each prune the "
            "other's objects. Move the nested tree out, or add the pair to "
            "NESTED_PATHS with the reason it cannot collide."
        )

    def test_the_nested_exemption_is_still_real(self):
        names = {doc.get("metadata", {}).get("name") for _p, doc in self._declared()}
        for inner, outer in self.NESTED_PATHS:
            assert {inner, outer} <= names, f"stale NESTED_PATHS entry: {inner}/{outer}"


FLUX_KUSTOMIZE_API = "kustomize.toolkit.fluxcd.io/"
BOOTSTRAP_DIR = "flux-system"


def path_less_kustomizations(root: Path) -> tuple[list[str], int]:
    """(Flux Kustomizations carrying no spec.path, documents examined).

    spec.path is optional and defaults to the repository root, so a stage that
    loses it reconciles the whole repo and drops out of every path-walking gate.
    """
    import yaml

    offenders: list[str] = []
    examined = 0
    for path in sorted(root.rglob("*.yaml")):
        if BOOTSTRAP_DIR in path.parts:
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
                continue
            if not str(doc.get("apiVersion") or "").startswith(FLUX_KUSTOMIZE_API):
                continue
            examined += 1
            if not (doc.get("spec") or {}).get("path"):
                name = (doc.get("metadata") or {}).get("name", "?")
                offenders.append(f"{path} ({name})")
    return offenders, examined


def test_every_stage_declares_a_path():
    offenders, examined = path_less_kustomizations(CLUSTER_DIR)
    assert examined, "this gate examined no Flux Kustomization"
    assert not offenders, (
        "Flux Kustomizations with no spec.path — each reconciles the whole "
        "repository and is skipped by every build, kubeconform and corpus "
        "gate:\n  " + "\n  ".join(offenders)
    )


def test_a_stage_that_lost_its_path_is_reported(tmp_path):
    """Mutation case: a path-less stage, plus the two documents the gate leaves
    alone (a plain kustomize Kustomization, a bootstrap one)."""
    boot = tmp_path / BOOTSTRAP_DIR
    boot.mkdir()
    stage = (
        f"apiVersion: {FLUX_KUSTOMIZE_API}v1\nkind: Kustomization\n"
        "metadata:\n  name: apps\nspec:\n  prune: true\n"
    )
    (tmp_path / "apps.yaml").write_text(stage)
    (tmp_path / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n"
    )
    (boot / "gotk-sync.yaml").write_text(stage)
    offenders, examined = path_less_kustomizations(tmp_path)
    assert examined == 1
    assert len(offenders) == 1 and "(apps)" in offenders[0]
    (tmp_path / "apps.yaml").write_text(stage + "  path: ./kubernetes/apps\n")
    assert path_less_kustomizations(tmp_path) == ([], 1)


class TestSynthetic:
    def _write(
        self, tmp_path: Path, name: str, depends: list[str], path: str | None = None
    ) -> None:
        dep_block = ""
        if depends:
            dep_block = "  dependsOn:\n" + "".join(f"    - name: {d}\n" for d in depends)
        if path:
            dep_block += f"  path: ./{path}\n"
        (tmp_path / f"{name}.yaml").write_text(
            textwrap.dedent(
                f"""\
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: {name}
                  namespace: flux-system
                spec:
                  interval: 10m
                """
            )
            + dep_block
        )

    def test_new_stage_appears_without_touching_consumers(self, tmp_path):
        self._write(tmp_path, "stage-a", [])
        self._write(tmp_path, "stage-b", ["stage-a"])
        assert _run(tmp_path) == ["stage-a", "stage-b"]
        self._write(tmp_path, "stage-c", ["stage-b"])
        assert _run(tmp_path) == ["stage-a", "stage-b", "stage-c"]

    def test_paths_helper_parses_the_directory_once(self, tmp_path, monkeypatch):
        """Ordering is split from parsing, so the whole cluster tree is read once."""
        self._write(tmp_path, "stage-a", [], path="kubernetes/infrastructure/sources")
        self._write(tmp_path, "stage-b", ["stage-a"], path="kubernetes/apps")
        module = _load()
        calls = []
        real_parse = module._parse
        monkeypatch.setattr(
            module, "_parse", lambda d: (calls.append(d), real_parse(d))[1]
        )
        assert module.child_kustomization_paths(tmp_path) == [
            ("stage-a", "kubernetes/infrastructure/sources"),
            ("stage-b", "kubernetes/apps"),
        ]
        assert calls == [tmp_path]

    def test_flux_system_is_never_listed(self, tmp_path):
        """Its spec.path is the cluster root, so a render corpus built from these
        paths would judge the Flux controllers against consumer policy."""
        self._write(tmp_path, "stage-a", [], path="kubernetes/apps")
        self._write(tmp_path, "flux-system", [], path=".")
        assert _run(tmp_path) == ["stage-a"]

        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path), "--paths"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "flux-system" not in res.stdout

    def test_exclude_drops_a_named_stage(self, tmp_path):
        self._write(tmp_path, "stage-a", [], path="kubernetes/apps")
        self._write(tmp_path, "stage-b", ["stage-a"], path="kubernetes/infrastructure/sources")
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path), "--exclude", "stage-b"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert res.stdout.split() == ["stage-a"]

    def test_non_kustomization_docs_are_ignored(self, tmp_path):
        self._write(tmp_path, "stage-a", [])
        (tmp_path / "cm.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: not-a-stage\n"
        )
        assert _run(tmp_path) == ["stage-a"]

    def test_empty_dir_is_an_operator_error(self, tmp_path):
        """An empty cluster directory is a wrong --dir, not a finding."""
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 2, res.stdout + res.stderr
        assert "no Flux Kustomizations found" in res.stderr

    def test_a_nonexistent_dir_is_an_operator_error(self, tmp_path):
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path / "gone")],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 2, res.stdout + res.stderr

    def test_dependency_cycle_still_terminates(self, tmp_path):
        self._write(tmp_path, "a", ["b"])
        self._write(tmp_path, "b", ["a"])
        mod = _load()
        assert sorted(mod.child_kustomizations(tmp_path)) == ["a", "b"]

    def _cycle(self, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
        for name, dep in (("a", "b"), ("b", "a")):
            (tmp_path / f"{name}.yaml").write_text(
                textwrap.dedent(
                    f"""\
                    apiVersion: kustomize.toolkit.fluxcd.io/v1
                    kind: Kustomization
                    metadata:
                      name: {name}
                    spec:
                      path: ./kubernetes/{name}
                      dependsOn:
                        - name: {dep}
                    """
                )
            )
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path), *extra],
            capture_output=True,
            text=True,
        )

    def test_dependency_cycle_exits_two(self, tmp_path):
        """The names still print, but every caller orders work by them, so a
        cycle has to be a failure rather than a note on stderr."""
        res = self._cycle(tmp_path)
        assert res.returncode == 2, res.stdout + res.stderr
        assert res.stdout.split() == ["a", "b"]
        assert "dependsOn cycle among a, b" in res.stderr

    def test_dependency_cycle_exits_two_with_paths(self, tmp_path):
        res = self._cycle(tmp_path, "--paths")
        assert res.returncode == 2, res.stdout + res.stderr
        assert "dependsOn cycle" in res.stderr

    def test_an_acyclic_tree_exits_zero(self, tmp_path):
        self._write(tmp_path, "a", [])
        self._write(tmp_path, "b", ["a"])
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "cycle" not in res.stderr


class TestCommandLine:
    """argparse owns the interface, so a malformed flag is a usage error."""

    def test_a_dir_flag_with_no_value_is_a_usage_error(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir"], capture_output=True, text=True
        )
        assert res.returncode == 2
        assert "Traceback" not in res.stderr

    def _write_pair(self, tmp_path: Path) -> None:
        (tmp_path / "stage-a.yaml").write_text(
            textwrap.dedent(
                """\
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: stage-a
                spec:
                  path: ./kubernetes/apps
                """
            )
        )
        (tmp_path / "stage-b.yaml").write_text(
            textwrap.dedent(
                """\
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: stage-b
                spec:
                  dependsOn:
                    - name: stage-a
                  sourceRef:
                    kind: GitRepository
                    name: flux-system
                """
            )
        )

    def test_paths_fails_on_a_stage_that_declares_none(self, tmp_path):
        """A dropped stage would silently stop being dry-run by the callers."""
        self._write_pair(tmp_path)
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path), "--paths"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1, res.stdout + res.stderr
        assert "declare no spec.path" in res.stderr
        assert "stage-b" in res.stderr

    def test_allow_missing_paths_prints_the_rest_in_dependson_order(self, tmp_path):
        self._write_pair(tmp_path)
        res = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dir",
                str(tmp_path),
                "--paths",
                "--allow-missing-paths",
            ],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        # stage-b declares no spec.path, so it is omitted; the './' is stripped.
        assert res.stdout.splitlines() == ["stage-a\tkubernetes/apps"]
        assert "stage-b" in res.stderr

    def test_paths_with_no_spec_path_anywhere_exits_non_zero(self, tmp_path):
        """A stage set that declares no path leaves every consumer loop empty."""
        (tmp_path / "stage-a.yaml").write_text(
            textwrap.dedent(
                """\
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: stage-a
                spec:
                  interval: 10m
                """
            )
        )
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path), "--paths"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1, res.stdout + res.stderr
        assert "spec.path" in res.stderr

    def test_allow_missing_paths_with_nothing_to_print_is_an_operator_error(
        self, tmp_path
    ):
        """Opting out of the finding must not certify an empty path list."""
        (tmp_path / "stage-a.yaml").write_text(
            textwrap.dedent(
                """\
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: stage-a
                spec:
                  interval: 10m
                """
            )
        )
        res = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dir",
                str(tmp_path),
                "--paths",
                "--allow-missing-paths",
            ],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 2, res.stdout + res.stderr
        assert "nothing to print" in res.stderr


_UNITS = {"s": 1, "m": 60, "h": 3600}


def _seconds(value: str) -> int:
    """Parse a Flux/Helm duration ("15m", "1h30m", "90s") into seconds."""
    import re

    parts = re.findall(r"(\d+)([smh])", str(value))
    assert parts, f"unparsable duration {value!r}"
    return sum(int(n) * _UNITS[u] for n, u in parts)


def _release_timeouts(path: Path):
    """Every HelmRelease timeout under a stage path, as (name, seconds)."""
    import yaml

    for f in sorted(path.rglob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if not isinstance(doc, dict) or doc.get("kind") != "HelmRelease":
                continue
            spec = doc.get("spec") or {}
            for candidate in (
                spec.get("timeout"),
                (spec.get("install") or {}).get("timeout"),
                (spec.get("upgrade") or {}).get("timeout"),
            ):
                if candidate:
                    yield (
                        f"{f.relative_to(REPO)}:{doc['metadata']['name']}",
                        _seconds(candidate),
                    )


class TestStageTimeoutHeadroom:
    """A wait:true stage must outlast the slowest release it waits on.

    A floor, not the whole story: install/upgrade remediation retries multiply
    the release timeout, so headroom over one attempt is the minimum.
    """

    def test_every_waiting_stage_outlasts_its_slowest_release(self):
        import yaml

        offenders = []
        for f in sorted(CLUSTER_DIR.glob("*.yaml")):
            for doc in yaml.safe_load_all(f.read_text()):
                if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
                    continue
                spec = doc.get("spec") or {}
                if not spec.get("wait") or not spec.get("path"):
                    continue
                stage_name = doc["metadata"]["name"]
                # An omitted timeout falls back to spec.interval, which is the
                # case this gate exists to catch: never skip such a stage.
                assert spec.get("timeout"), (
                    f"{stage_name} sets wait: true with no spec.timeout — it "
                    "falls back to spec.interval instead of outlasting its "
                    "releases"
                )
                stage_dir = REPO / spec["path"].lstrip("./")
                assert stage_dir.is_dir(), (
                    f"{stage_name} spec.path {spec['path']} is not a directory "
                    "— the gate examined nothing"
                )
                stage = _seconds(spec["timeout"])
                releases = list(_release_timeouts(stage_dir))
                if not releases:
                    # A stage of raw CRs, or one whose releases take the chart
                    # default, has no timeout to outlast.
                    continue
                name, slowest = max(releases, key=lambda r: r[1])
                if stage <= slowest:
                    offenders.append(
                        f"{stage_name} timeout {spec['timeout']} "
                        f"does not exceed {name} ({slowest}s)"
                    )
        assert not offenders, offenders
