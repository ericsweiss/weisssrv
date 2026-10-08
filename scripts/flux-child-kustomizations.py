#!/usr/bin/env python3
"""Child Flux Kustomizations in dependsOn order from kubernetes/clusters/weisssrv/*.yaml,
never `flux-system`. Usage: [--dir DIR] [--paths] [--allow-missing-paths] [--exclude NAME].
Exit 0 clean, 1 a stage declares no spec.path, 2 the gate could not read its subject.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO / "kubernetes" / "clusters" / "weisssrv"
# The bootstrap Kustomization reconciles the Flux controllers themselves, so
# feeding its path to a render corpus judges them against consumer policy.
DEFAULT_EXCLUDE = frozenset({"flux-system"})


def _parse(
    directory: Path,
    exclude: frozenset[str] | set[str] | None = None,
    unreadable: list | None = None,
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return (name -> dependsOn names, name -> spec.path) for the directory.

    A file that will not parse goes to `unreadable`: callers derive the stage
    list from stdout, so dropping one would silently stop validating its tree.
    """
    excluded = DEFAULT_EXCLUDE if exclude is None else exclude
    deps: dict[str, set[str]] = {}
    paths: dict[str, str] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            if unreadable is None:
                raise
            unreadable.append((path, exc))
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") != "Kustomization":
                continue
            if not str(doc.get("apiVersion", "")).startswith("kustomize.toolkit"):
                continue
            name = (doc.get("metadata") or {}).get("name")
            if not name or name in excluded:
                continue
            spec = doc.get("spec") or {}
            deps[name] = {
                d["name"]
                for d in (spec.get("dependsOn") or [])
                if isinstance(d, dict) and d.get("name")
            }
            source = str(spec.get("path") or "")
            if source.startswith("./"):
                source = source[2:]
            if source:
                paths[name] = source
    return deps, paths


def _order(deps: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """(dependency-ordered names, names caught in a dependsOn cycle)."""
    ordered: list[str] = []
    cycled: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(
            n for n, d in remaining.items() if not (d & set(remaining)) - {n}
        )
        if not ready:
            # A cycle would loop forever; emit what is left deterministically so
            # the names still print for diagnosis.
            cycled = sorted(remaining)
            ready = cycled
        for name in ready:
            ordered.append(name)
            del remaining[name]
    return ordered, cycled


def child_kustomizations(directory: Path) -> list[str]:
    return _order(_parse(directory)[0])[0]


def child_kustomization_paths(directory: Path) -> list[tuple[str, str]]:
    """(name, spec.path) in dependsOn order, for the stages that declare a path."""
    deps, paths = _parse(directory)
    return [(name, paths[name]) for name in _order(deps)[0] if name in paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Child Flux Kustomizations in dependsOn order.")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="cluster directory to read")
    parser.add_argument(
        "--paths", action="store_true", help="print `name<TAB>spec.path` instead of names"
    )
    parser.add_argument(
        "--allow-missing-paths",
        action="store_true",
        help="with --paths, print the paths there are instead of failing on a "
        "Kustomization that declares none",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="NAME",
        help="also skip this Kustomization; repeatable (%s is always skipped)"
        % ", ".join(sorted(DEFAULT_EXCLUDE)),
    )
    args = parser.parse_args(argv)

    unreadable: list = []
    deps, paths = _parse(
        args.dir, set(DEFAULT_EXCLUDE) | set(args.exclude or ()), unreadable=unreadable
    )
    if unreadable:
        for path, exc in unreadable:
            print(f"{path}: {exc}", file=sys.stderr)
        print(
            "    Fix: Flux would reject the file — make it parse, or move it out "
            "of the cluster directory.",
            file=sys.stderr,
        )
        return 2
    names, cycled = _order(deps)
    if not names:
        # Exit 2: a wrong --dir or an empty cluster directory is an operator
        # error, which callers must not read as a finding about the cluster.
        print(
            f"ERROR: no Flux Kustomizations found under {args.dir} — point --dir "
            "at the cluster directory",
            file=sys.stderr,
        )
        return 2
    if args.paths:
        # Consumers loop over these paths to dry-run every stage, so a dropped
        # one silently stops being validated.
        skipped = [name for name in names if name not in paths]
        pairs = [(name, paths[name]) for name in names if name in paths]
        if skipped:
            print(
                f"WARNING: {len(skipped)} Kustomization(s) declare no spec.path and "
                "are not listed: " + ", ".join(skipped),
                file=sys.stderr,
            )
        if skipped and not args.allow_missing_paths:
            return 1
        if not pairs:
            print(
                f"ERROR: no Flux Kustomization under {args.dir} declares a "
                "spec.path, so there is nothing to print",
                file=sys.stderr,
            )
            return 2
        print("\n".join(f"{name}\t{path}" for name, path in pairs))
    else:
        print("\n".join(names))
    # The printed order is still deterministic, but it no longer satisfies
    # dependsOn: a caller that reconciles or validates in it would do so out of
    # order, so this is a failure, not a note.
    if cycled:
        print(
            "ERROR: dependsOn cycle among "
            + ", ".join(cycled)
            + " - the printed order is NOT dependency-satisfying",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
