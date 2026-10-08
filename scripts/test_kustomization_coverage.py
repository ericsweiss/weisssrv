"""Coverage for scripts/check-kustomization-coverage.py.

The gate's arms are driven against fixture trees; the live tree is checked
through the gate itself so a broken walk cannot pass quietly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
KUBERNETES = REPO / "kubernetes"

gate = load_script("check-kustomization-coverage.py")

EXEMPT = gate.EXEMPT
Vacuous = gate.Vacuous
_GENERATOR_KEYS = gate._GENERATOR_KEYS
_walk = gate._walk
carries_an_object = gate.carries_an_object
empty_listed_manifests = gate.empty_listed_manifests
inert_kustomizations = gate.inert_kustomizations
load_kustomization = gate.load_kustomization
missing_references = gate.missing_references
referenced_paths = gate.referenced_paths
unlisted_siblings = gate.unlisted_siblings


def test_the_live_tree_passes_the_gate():
    """One call, so the gate's own vacuity floor guards the live assertions."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/check-kustomization-coverage.py")],
        capture_output=True, text=True, check=False, cwd=REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_tree_with_almost_no_kustomization_is_exit_2(tmp_path: Path):
    """Mutation case: a walk that stopped matching must not pass as clean."""
    (tmp_path / "kubernetes").mkdir()
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/check-kustomization-coverage.py"),
         "--repo-root", str(tmp_path)],
        capture_output=True, text=True, check=False, cwd=REPO,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "broken walk" in result.stderr


def test_exemptions_name_files_that_exist():
    missing = [p for p in EXEMPT if not (REPO / p).is_file()]
    assert not missing, f"EXEMPT names paths that are gone: {missing}"


def test_an_unlisted_sibling_is_reported(tmp_path: Path):
    """Mutation case: the orphan this gate exists to catch."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "networkpolicy.yaml").write_text("kind: NetworkPolicy\n")
    (app / "kustomization.yaml").write_text("resources:\n  - release.yaml\n")
    assert unlisted_siblings(tmp_path / "kubernetes") == [
        "kubernetes/apps/demo/networkpolicy.yaml"
    ]


def test_an_unlisted_json_sibling_is_reported(tmp_path: Path):
    """kustomize renders JSON resources, so an unlisted one is just as inert."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "service.json").write_text('{"kind": "Service"}\n')
    (app / "kustomization.yaml").write_text("resources:\n  - release.yaml\n")
    assert unlisted_siblings(tmp_path / "kubernetes") == [
        "kubernetes/apps/demo/service.json"
    ]


def test_a_listed_sibling_is_not_reported(tmp_path: Path):
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "kustomization.yaml").write_text("resources:\n  - ./release.yaml\n")
    assert unlisted_siblings(tmp_path / "kubernetes") == []


def test_the_yml_spelling_is_walked_and_skipped(tmp_path: Path):
    """A directory whose aggregator is kustomization.yml is gated too, and the
    aggregator itself is never reported as an orphan."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "networkpolicy.yaml").write_text("kind: NetworkPolicy\n")
    (app / "kustomization.yml").write_text("resources:\n  - release.yaml\n")
    assert unlisted_siblings(tmp_path / "kubernetes") == [
        "kubernetes/apps/demo/networkpolicy.yaml"
    ]


def test_a_remote_resource_is_not_read_as_a_local_path(tmp_path: Path):
    """A remote reference resolved against the directory would put a bogus path
    in the referenced set, which the listed-path-exists arm would then fail on."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "kustomization.yaml").write_text(
        "resources:\n"
        "  - release.yaml\n"
        "  - https://example.com/manifest.yaml\n"
        "  - github.com/org/repo//dir?ref=v1\n"
        "  - git@example.com:org/repo.git//dir\n"
        # Only the forced-protocol prefix classifies the first of these, and
        # only the `?ref=` the second, so each arm stays load-bearing.
        "  - git::gitlab.example.com/org/repo\n"
        "  - gitlab.example.com/org/repo?ref=v1\n"
    )
    assert referenced_paths(app / "kustomization.yaml") == {
        (app / "release.yaml").resolve()
    }
    assert unlisted_siblings(tmp_path / "kubernetes") == []
    assert missing_references(tmp_path / "kubernetes") == []


@pytest.mark.parametrize("generator", _GENERATOR_KEYS)
@pytest.mark.parametrize("entry", ["./values.yaml", "renamed.yaml=values.yaml"])
def test_generator_and_patch_forms_count_as_references(
    tmp_path: Path, generator: str, entry: str
):
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "dashboard.json").write_text("{}\n")
    (app / "values.yaml").write_text("a: 1\n")
    (app / "patch.yaml").write_text("kind: Deployment\n")
    (app / "kustomization.yaml").write_text(
        "patches:\n"
        "  - path: patch.yaml\n"
        "replacements:\n"
        "  - path: replacement.yaml\n"
        "openapi:\n"
        "  path: schema.json\n"
        "helmCharts:\n"
        "  - name: demo\n"
        "    valuesFile: chart-values.yaml\n"
        f"{generator}:\n"
        "  - name: demo\n"
        "    files:\n"
        f"      - {entry}\n"
    )
    (app / "replacement.yaml").write_text("source: {}\n")
    (app / "schema.json").write_text("{}\n")
    (app / "chart-values.yaml").write_text("a: 1\n")
    assert unlisted_siblings(tmp_path / "kubernetes") == ["kubernetes/apps/demo/dashboard.json"]
    assert missing_references(tmp_path / "kubernetes") == []


def test_a_bases_only_kustomization_references_its_target(tmp_path: Path):
    """kustomize still builds the legacy `bases:` key."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "kustomization.yaml").write_text("bases:\n  - release.yaml\n")
    assert unlisted_siblings(tmp_path / "kubernetes") == []


def test_a_missing_patch_path_is_reported(tmp_path: Path):
    """Mutation case for the existence arm: a name with no file behind it."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "kustomization.yaml").write_text(
        "resources:\n  - release.yaml\npatches:\n  - path: no-such-patch.yaml\n"
    )
    assert missing_references(tmp_path / "kubernetes") == [
        "kubernetes/apps/demo/kustomization.yaml -> no-such-patch.yaml"
    ]


def test_a_malformed_kustomization_names_itself(tmp_path: Path):
    """A PyYAML traceback with no file name makes the operator bisect the tree."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    broken = app / "kustomization.yaml"
    broken.write_text("resources:\n  - [unclosed\n")
    with pytest.raises(Vacuous, match=str(broken)):
        load_kustomization(broken)

def test_a_listed_manifest_with_no_object_is_reported(tmp_path: Path):
    """Mutation case: a manifest emptied in place stays listed and lints clean."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "networkpolicy.yaml").write_text("---\n# emptied, object moved elsewhere\n")
    (app / "kustomization.yaml").write_text(
        "resources:\n  - release.yaml\n  - networkpolicy.yaml\n"
    )
    assert empty_listed_manifests(tmp_path / "kubernetes") == [
        "kubernetes/apps/demo/kustomization.yaml -> networkpolicy.yaml"
    ]
    assert missing_references(tmp_path / "kubernetes") == []
    assert unlisted_siblings(tmp_path / "kubernetes") == []


def test_an_emptied_resource_list_is_reported(tmp_path: Path):
    """Mutation case: kustomize exits 0 on an emptied list, and the cluster-side
    Kustomization then prunes every object the stage applied."""
    stage = tmp_path / "kubernetes" / "infrastructure" / "demo"
    stage.mkdir(parents=True)
    (stage / "kustomization.yaml").write_text("resources: []\n")
    assert inert_kustomizations(tmp_path / "kubernetes") == [
        "kubernetes/infrastructure/demo/kustomization.yaml"
    ]


def test_a_patch_only_kustomization_is_not_reported(tmp_path: Path):
    """An overlay legally lists no resources: it contributes components and patches."""
    overlay = tmp_path / "kubernetes" / "apps" / "demo"
    overlay.mkdir(parents=True)
    (overlay / "patch.yaml").write_text("kind: Deployment\n")
    (overlay / "kustomization.yaml").write_text(
        "components:\n  - ../../components/demo\npatches:\n  - path: patch.yaml\n"
    )
    assert inert_kustomizations(tmp_path / "kubernetes") == []


def test_a_listed_json_manifest_with_an_object_is_not_reported(tmp_path: Path):
    """kustomize renders a JSON resource, and safe_load_all parses it."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "service.json").write_text('{"kind": "Service"}\n')
    (app / "kustomization.yaml").write_text("resources:\n  - service.json\n")
    assert empty_listed_manifests(tmp_path / "kubernetes") == []


def test_a_listed_directory_is_not_read_as_a_manifest(tmp_path: Path):
    """A `resources:` entry naming a directory has its own kustomization."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    (app / "base").mkdir(parents=True)
    (app / "base" / "release.yaml").write_text("kind: HelmRelease\n")
    (app / "base" / "kustomization.yaml").write_text("resources:\n  - release.yaml\n")
    (app / "kustomization.yaml").write_text("resources:\n  - base\n")
    assert empty_listed_manifests(tmp_path / "kubernetes") == []


def test_an_unparseable_listed_manifest_names_itself(tmp_path: Path):
    """An unreadable resource is an operator error, named by path."""
    app = tmp_path / "kubernetes" / "apps" / "demo"
    app.mkdir(parents=True)
    broken = app / "release.yaml"
    broken.write_text("kind: [unclosed\n")
    (app / "kustomization.yaml").write_text("resources:\n  - release.yaml\n")
    with pytest.raises(Vacuous, match=str(broken)):
        empty_listed_manifests(tmp_path / "kubernetes")


def test_a_transformer_only_kustomization_is_reported(tmp_path: Path):
    """Mutation case: transformers reshape what a kustomization lists, so one
    that lists only transformers builds empty and the stage is pruned."""
    stage = tmp_path / "kubernetes" / "infrastructure" / "demo"
    stage.mkdir(parents=True)
    (stage / "patch.yaml").write_text("kind: Deployment\n")
    (stage / "kustomization.yaml").write_text(
        "resources: []\n"
        "images:\n  - name: demo\n    newTag: v1\n"
        "commonLabels:\n  app: demo\n"
        "patches:\n  - path: patch.yaml\n"
    )
    assert inert_kustomizations(tmp_path / "kubernetes") == [
        "kubernetes/infrastructure/demo/kustomization.yaml: lists only transformers"
    ]


def test_a_transformer_only_component_is_not_reported(tmp_path: Path):
    """A Component only reshapes its parent's list, so patches alone are content."""
    component = tmp_path / "kubernetes" / "components" / "demo"
    component.mkdir(parents=True)
    (component / "patch.yaml").write_text("kind: Deployment\n")
    (component / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1alpha1\n"
        "kind: Component\n"
        "patches:\n  - path: patch.yaml\n"
    )
    assert inert_kustomizations(tmp_path / "kubernetes") == []
