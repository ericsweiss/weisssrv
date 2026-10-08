"""The Origin column in scripts/README.md matches vendored-manifest.yml."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
README = REPO / "scripts" / "README.md"
MANIFEST = REPO / "scripts" / "vendored-manifest.yml"

# `| `name` | description | origin |` — the shape every script table row uses.
# The name cell covers extensionless and subdirectory helpers, not just .py/.sh.
ROW = re.compile(r"^\|\s*`([\w.\-/]+)`\s*\|.*\|\s*(local|vendored|forked)\s*\|\s*$")


def _consumer_paths(entries) -> set[str]:
    """Consumer-side paths of one manifest block (bare string or lib/consumer)."""
    paths = set()
    for entry in entries or []:
        if isinstance(entry, str):
            paths.add(entry)
        elif isinstance(entry, dict):
            paths.add(entry.get("consumer") or entry["lib"])
    return paths


def manifest_origins() -> dict[str, str]:
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    origins = {}
    for origin, key in (("vendored", "vendored"), ("forked", "forked")):
        for path in _consumer_paths(doc.get(key)):
            origins[Path(path).name] = origin
    return origins


def documented_origins() -> dict[str, str]:
    found = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            found[Path(match.group(1)).name] = match.group(2)
    return found


def test_the_table_is_not_empty():
    """A regex that stopped matching would make every assertion below vacuous."""
    assert len(documented_origins()) > 20


def mismatches(documented: dict[str, str], manifest: dict[str, str]) -> dict:
    """name -> (README says, manifest says) for every row that disagrees."""
    return {
        name: (declared, manifest.get(name, "local"))
        for name, declared in documented.items()
        if declared != manifest.get(name, "local")
    }


def test_every_documented_origin_matches_the_manifest():
    wrong = mismatches(documented_origins(), manifest_origins())
    assert not wrong, (
        "scripts/README.md Origin disagrees with scripts/vendored-manifest.yml "
        f"(name: README says, manifest says): {wrong}"
    )


def test_a_reclassified_script_is_caught():
    """Mutation case: a vendored script documented as local must fail."""
    manifest = manifest_origins()
    assert manifest["check-lib-pins.py"] == "vendored"
    documented = dict(documented_origins(), **{"check-lib-pins.py": "local"})
    assert mismatches(documented, manifest) == {
        "check-lib-pins.py": ("local", "vendored")
    }
