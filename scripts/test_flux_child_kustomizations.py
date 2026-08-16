#!/usr/bin/env python3
"""Unit tests for scripts/flux-child-kustomizations.py, plus the substitution
contract the same set of Kustomizations has to satisfy.

Two consumers (task flux:reconcile, scripts/deploy-verify.sh) used to
hand-maintain the child Kustomization list and both omitted
`infrastructure-crds`. These tests pin that the derived list covers every
Kustomization the cluster directory actually declares and orders it by
dependsOn, so adding a stage can never silently drop out of either consumer.

TestSubstituteFrom gates a second invariant over the same files: every stage
after `sources` must substitute from BOTH cluster-versions and cluster-config,
each `optional: false`. It lives here rather than in flux:lint because
flux:lint structurally cannot see it — that task exports the union of both
ConfigMaps and envsubsts every tree with all of them, regardless of what each
Kustomization declares.

Run with pytest:
    pytest scripts/test_flux_child_kustomizations.py -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "flux-child-kustomizations.py"
REPO = SCRIPT.parent.parent
CLUSTER_DIR = REPO / "kubernetes" / "clusters" / "weisssrv"


def _load():
    spec = importlib.util.spec_from_file_location("flux_child_kustomizations", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(directory: Path | None = None) -> list[str]:
    cmd = [sys.executable, str(SCRIPT)]
    if directory is not None:
        cmd += ["--dir", str(directory)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.split()


class TestAgainstTheRealCluster:
    def test_every_declared_kustomization_is_listed(self):
        # The drift this catches: a new *.yaml stage under clusters/weisssrv/
        # that neither consumer knows about.
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
        # The specific omission that shipped.
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
    ConfigMaps, both non-optional (CLAUDE.md § Task Runner).

    A stage that lists only cluster-versions lints green — flux:lint substitutes
    every tree with the union of both — and then ships a literal
    `${cluster_internal_domain}` into a live manifest, which Flux renders as an
    empty string. check-cluster-literals.py cannot catch it either: it uses
    substituteFrom to DERIVE which trees to scan, so a Kustomization missing
    cluster-config is excluded from the very scan that would flag its literals.
    """

    REQUIRED = {"cluster-versions", "cluster-config"}
    # infrastructure-sources is where both ConfigMaps live, so it has nothing to
    # substitute from; flux-system is the upstream bootstrap Kustomization.
    EXEMPT = {"infrastructure-sources", "flux-system"}
    # Tenant wiring files are not platform stages. The canonical template in
    # kubernetes/clusters/weisssrv/tenants/README.md deliberately lists
    # cluster-config ONLY (and only when the tenant uses placeholders at all):
    # a tenant pins its own image versions rather than tracking this cluster's,
    # so requiring cluster-versions here would contradict the documented shape.
    EXEMPT_TREES = ("kubernetes/clusters/weisssrv/tenants/",)

    def _declared(self):
        import yaml

        found = []
        for path in sorted(REPO.glob("kubernetes/**/*.yaml")):
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


class TestSynthetic:
    def _write(self, tmp_path: Path, name: str, depends: list[str]) -> None:
        dep_block = ""
        if depends:
            dep_block = "  dependsOn:\n" + "".join(f"    - name: {d}\n" for d in depends)
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

    def test_non_kustomization_docs_are_ignored(self, tmp_path):
        self._write(tmp_path, "stage-a", [])
        (tmp_path / "cm.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: not-a-stage\n"
        )
        assert _run(tmp_path) == ["stage-a"]

    def test_empty_dir_exits_non_zero(self, tmp_path):
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1

    def test_dependency_cycle_still_terminates(self, tmp_path):
        self._write(tmp_path, "a", ["b"])
        self._write(tmp_path, "b", ["a"])
        mod = _load()
        assert sorted(mod.child_kustomizations(tmp_path)) == ["a", "b"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
