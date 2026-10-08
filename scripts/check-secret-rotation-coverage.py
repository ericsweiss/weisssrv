#!/usr/bin/env python3
"""Assert every ESO-managed credential has a rotation path in docs/15.

Each `remoteRef.key` is named in its item-inventory section, and each ExternalSecret
reached by flux-rotate-secret.sh, the ESO workload section or DECLARED_MANUAL.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

MANIFEST_TREE = "kubernetes"
DOC = "docs/15-credential-rotation.md"
ROTATE_SCRIPT = "scripts/flux-rotate-secret.sh"

# The `## ` section that owns each claim. Searching the whole document would
# count a name mentioned in an unrelated section, a code fence or another
# item's prose as documentation of its own rotation.
ITEM_SECTION = "Required 1Password Items"
WORKLOAD_SECTION = "Kubernetes workloads (External Secrets Operator)"

# ExternalSecrets no automated rotation path reaches, each with a reason. The
# set is frozen: a NEW ExternalSecret cannot join it without this file changing,
# and an entry leaves when a rotation path grows (docs/16 § Open work).
DECLARED_MANUAL: dict[str, str] = {
    "observability/alertmanager-config": "no written procedure yet",
    "observability/loki-push-auth": "no written procedure yet",
    "observability/observability-secrets": "no written procedure yet",
    "tailscale/tailscale-operator-oauth": "no written procedure yet",
    "cloudflare-api-token": "ClusterExternalSecret; docs/15 § Cloudflare DNS Token",
}


class Vacuous(Exception):
    """The gate could not inspect its subject — exit 2, never a silent pass."""


def external_secrets(root: Path) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Return ({ns/name: file}, {remoteRef key: file}, unreadable files)."""
    names: dict[str, str] = {}
    keys: dict[str, str] = {}
    skipped: list[str] = []
    for path in sorted((root / MANIFEST_TREE).rglob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except (OSError, yaml.YAMLError) as exc:
            skipped.append(
                f"{rel}: unreadable ({exc.__class__.__name__}: {exc}) — "
                "this gate could not inspect it"
            )
            continue
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") not in (
                "ExternalSecret",
                "ClusterExternalSecret",
            ):
                continue
            meta = doc.get("metadata") or {}
            if doc.get("kind") == "ClusterExternalSecret":
                # Cluster-scoped, so no namespace: the name an operator rotates
                # is the per-namespace externalSecretName it fans out.
                spec = doc.get("spec") or {}
                names.setdefault(str(spec.get("externalSecretName") or meta.get("name")), rel)
            else:
                names.setdefault(f"{meta.get('namespace', '?')}/{meta.get('name')}", rel)
            for key in _remote_keys(doc):
                keys.setdefault(key, rel)
    return names, keys, skipped


def _remote_keys(node) -> list[str]:
    """Every 1Password item title the document reads, at any nesting depth."""
    found: list[str] = []
    if isinstance(node, dict):
        for field in ("remoteRef", "extract", "find"):
            ref = node.get(field)
            if isinstance(ref, dict) and ref.get("key"):
                found.append(str(ref["key"]))
        for value in node.values():
            found += _remote_keys(value)
    elif isinstance(node, list):
        for item in node:
            found += _remote_keys(item)
    return found


def doc_sections(text: str) -> dict[str, str]:
    """`## heading` -> that section's body, nested headings included."""
    return {
        part.splitlines()[0].strip(): part
        for part in re.split(r"^## ", text, flags=re.M)[1:]
        if part.strip()
    }


def documents(name: str, text: str) -> bool:
    """Whole-name match: a longer name never covers a shorter one. The boundary
    class carries `-` and `/` besides word characters, so a space-separated vault
    title can still match inside a longer phrase.
    """
    pattern = r"(?<![A-Za-z0-9_/-])" + re.escape(name) + r"(?![A-Za-z0-9_/-])"
    return re.search(pattern, text) is not None


def check(root: Path) -> list[str]:
    return check_detailed(root)[0]


def check_detailed(root: Path) -> tuple[list[str], int, int]:
    """(problems, vault keys seen, ExternalSecrets seen) — the counts the
    success line prints, so the manifest tree is walked once."""
    names, keys, skipped = external_secrets(root)
    if not names:
        raise Vacuous(
            f"no ExternalSecret found under {MANIFEST_TREE}/ — "
            "a gate that checks nothing is not a gate"
        )
    sections = doc_sections((root / DOC).read_text())
    missing = [s for s in (ITEM_SECTION, WORKLOAD_SECTION) if s not in sections]
    if missing:
        raise Vacuous(
            f"{DOC} has no `## {'` / `## '.join(missing)}` section — the gate "
            "cannot tell where a credential is documented"
        )
    items = sections[ITEM_SECTION]
    workloads = sections[WORKLOAD_SECTION]
    rotate = (root / ROTATE_SCRIPT).read_text()

    problems = list(skipped)
    for key, rel in sorted(keys.items()):
        if not documents(key, items):
            problems.append(
                f"{rel}: remoteRef.key {key!r} is named nowhere under "
                f"`## {ITEM_SECTION}` in {DOC} — the vault item has no "
                "documented rotation"
            )
    for name, rel in sorted(names.items()):
        if documents(name, rotate) or documents(name, workloads) or name in DECLARED_MANUAL:
            continue
        problems.append(
            f"{rel}: ExternalSecret {name} is reached by no rotation path — "
            f"add a case to {ROTATE_SCRIPT}, a refresh line under "
            f"`## {WORKLOAD_SECTION}` in {DOC}, or an entry to "
            "DECLARED_MANUAL with a reason"
        )
    for name in sorted(DECLARED_MANUAL):
        if name not in names:
            problems.append(
                f"DECLARED_MANUAL names {name}, which no ExternalSecret declares — "
                "drop the stale entry"
            )
    return problems, len(keys), len(names)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rotation coverage for ESO secrets")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    root = Path(args.repo_root)

    try:
        problems, key_count, name_count = check_detailed(root)
    except Vacuous as exc:
        print(f"check-secret-rotation-coverage inspected nothing: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"check-secret-rotation-coverage could not read a file: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("Credentials outside the documented rotation lifecycle:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(
        f"Rotation coverage OK ({key_count} vault items in {DOC}, "
        f"{name_count} ExternalSecrets, {len(DECLARED_MANUAL)} declared manual)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
