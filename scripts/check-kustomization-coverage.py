#!/usr/bin/env python3
"""Every manifest beside a kustomization is named by it, and every name exists.

A file no kustomization lists is invisible to Flux, a name with no file behind it
fails `kustomize build`, and an emptied one renders nothing. Exit 0/1/2.
"""
from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

REPO = Path(__file__).resolve().parent.parent
TREE = "kubernetes"
# A walk finding fewer than this is a broken walk, not a tidy cluster.
MIN_KUSTOMIZATIONS = 30


class Vacuous(Exception):
    """A subject the gate could not inspect — exit 2, never a finding."""

# kustomize accepts either spelling, so a gate that reads one walks past the other.
KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml")
# Manifest extensions kustomize renders. JSON carries resources and the
# Grafana dashboards a configMapGenerator reads.
MANIFEST_GLOBS = ("*.yaml", "*.yml", "*.json")

# Keys whose entries name a path kustomize reads.
_PATH_LIST_KEYS = (
    "resources",
    "bases",
    "components",
    "crds",
    "configurations",
    "transformers",
    "generators",
    "patchesStrategicMerge",
    "patchesJson6902",
    "patches",
    "replacements",
)
_GENERATOR_KEYS = ("configMapGenerator", "secretGenerator")
_GENERATOR_FILE_KEYS = ("files", "envs", "env")

# A kustomization contributes through any of these, so an empty resource list is
# inert only when every one of them is empty too.
_CONTENT_KEYS = _PATH_LIST_KEYS + _GENERATOR_KEYS + (
    "images",
    "labels",
    "helmCharts",
    "openapi",
)

# "kubernetes/<path>": reason — a manifest deliberately not rendered here.
EXEMPT: dict[str, str] = {}


def _is_remote(value: str) -> bool:
    """A kustomize remote reference, which names no file under kubernetes/."""
    if "://" in value or value.startswith(("github.com/", "git@", "git::", "ssh://")):
        return True
    if "?" in value:  # ?ref=, ?timeout=, ?submodules=
        return True
    head, separator, _ = value.partition("//")
    if separator and head and not head.startswith("."):  # host/org/repo//path
        return True
    return any(part.endswith(".git") for part in value.split("/"))


def _entry_names(entry) -> set[str]:
    """The local path an entry names, as written."""
    value = entry.get("path") if isinstance(entry, dict) else entry
    if not isinstance(value, str) or not value:
        return set()
    if "\n" in value:  # patchesStrategicMerge accepts an inline patch document
        return set()
    return set() if _is_remote(value) else {posixpath.normpath(value)}


def load_kustomization(kustomization: Path) -> dict:
    """The kustomization document, or an operator-readable failure.

    Parsed from the handle so PyYAML's mark names the file rather than
    "<unicode string>".
    """
    try:
        with kustomization.open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise Vacuous(f"{kustomization}: {error}") from error
    if not isinstance(doc, dict):
        raise Vacuous(f"{kustomization}: not a mapping")
    return doc


def reference_names(doc: dict) -> set[str]:
    """Every sibling-relative path the kustomization document names."""
    names: set[str] = set()
    for key in _PATH_LIST_KEYS:
        for entry in doc.get(key) or []:
            names |= _entry_names(entry)
    for key in _GENERATOR_KEYS:
        for entry in doc.get(key) or []:
            if not isinstance(entry, dict):
                continue
            for sub in _GENERATOR_FILE_KEYS:
                value = entry.get(sub)
                for item in ([value] if isinstance(value, str) else value) or []:
                    # Generator file entries may be spelled `key=path`.
                    names.add(posixpath.normpath(str(item).split("=", 1)[-1]))
    for chart in doc.get("helmCharts") or []:
        if isinstance(chart, dict) and isinstance(chart.get("valuesFile"), str):
            names.add(posixpath.normpath(chart["valuesFile"]))
    openapi = doc.get("openapi")
    if isinstance(openapi, dict) and isinstance(openapi.get("path"), str):
        names.add(posixpath.normpath(openapi["path"]))
    return names


def referenced_paths(kustomization: Path) -> set[Path]:
    """Every sibling-relative path the kustomization names, resolved."""
    base = kustomization.parent
    return {(base / name).resolve() for name in reference_names(load_kustomization(kustomization))}


def carries_an_object(manifest: Path) -> bool:
    """A manifest renders nothing unless some document carries a `kind`."""
    try:
        with manifest.open(encoding="utf-8") as handle:
            return any(
                isinstance(doc, dict) and doc.get("kind")
                for doc in yaml.safe_load_all(handle)
            )
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise Vacuous(f"{manifest}: {error}") from error


def _walk(root: Path) -> list[Path]:
    """Every kustomization under root, in either spelling."""
    found: list[Path] = []
    for name in KUSTOMIZATION_NAMES:
        found += root.rglob(name)
    return sorted(found)


def unlisted_siblings(root: Path) -> list[str]:
    """Manifests beside a kustomization that it does not name."""
    orphans = []
    for kustomization in _walk(root):
        referenced = referenced_paths(kustomization)
        siblings = sorted(
            {s for glob in MANIFEST_GLOBS for s in kustomization.parent.glob(glob)}
        )
        for sibling in siblings:
            if sibling.name in KUSTOMIZATION_NAMES:
                continue
            if sibling.resolve() not in referenced:
                orphans.append(str(sibling.relative_to(root.parent)))
    return orphans


def missing_references(root: Path) -> list[str]:
    """`kustomization -> name` pairs naming a local path that is not on disk."""
    missing = []
    for kustomization in _walk(root):
        base = kustomization.parent
        for name in sorted(reference_names(load_kustomization(kustomization))):
            if not (base / name).exists():
                missing.append(f"{kustomization.relative_to(root.parent)} -> {name}")
    return missing


def empty_listed_manifests(root: Path) -> list[str]:
    """`kustomization -> name` pairs listing a file that carries no object."""
    empty = []
    for kustomization in _walk(root):
        base = kustomization.parent
        doc = load_kustomization(kustomization)
        names: set[str] = set()
        for key in ("resources", "bases"):
            for entry in doc.get(key) or []:
                names |= _entry_names(entry)
        for name in sorted(names):
            manifest = base / name
            if manifest.is_file() and not carries_an_object(manifest):
                empty.append(f"{kustomization.relative_to(root.parent)} -> {name}")
    return empty


def inert_kustomizations(root: Path) -> list[str]:
    """Kustomizations that contribute nothing, so kustomize renders an empty stage."""
    inert = []
    for kustomization in _walk(root):
        doc = load_kustomization(kustomization)
        if not any(doc.get(key) for key in _CONTENT_KEYS):
            inert.append(str(kustomization.relative_to(root.parent)))
    return inert

ARMS = (
    (
        unlisted_siblings,
        "manifests on disk that no kustomization names, so kustomize never "
        "renders them and Flux never applies them",
    ),
    (
        missing_references,
        "kustomizations naming a local path that is not on disk, so "
        "`kustomize build` fails on the stage",
    ),
    (
        empty_listed_manifests,
        "kustomizations listing a manifest that carries no object, so kustomize "
        "renders nothing from it and Flux prunes what it used to apply",
    ),
    (
        inert_kustomizations,
        "kustomizations that render nothing, so Flux prunes every object the "
        "stage used to apply",
    ),
)


def check(root: Path) -> list[str]:
    """Findings across every arm, over the manifest tree under `root`."""
    tree = root / TREE
    if not tree.is_dir():
        raise Vacuous(f"{TREE}/ is not a directory under {root}")
    found = _walk(tree)
    if len(found) < MIN_KUSTOMIZATIONS:
        raise Vacuous(
            f"only {len(found)} kustomization(s) under {tree} — the walk found "
            "almost nothing, which is a broken walk rather than a clean tree"
        )
    problems: list[str] = []
    for arm, label in ARMS:
        hits = [hit for hit in arm(tree) if hit not in EXEMPT]
        if hits:
            problems.append(f"{label}: {hits}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kustomization coverage gate")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    try:
        problems = check(args.repo_root)
    except Vacuous as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("Kustomization coverage findings:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Every manifest is listed by its kustomization, and every name resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
