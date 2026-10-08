"""No new site domain literal in the scripts this repo runs, or the Taskfile tree.

The corpus is every `.sh` and non-test `.py` under scripts/, recursively, plus the
Taskfile tree; check-cluster-literals.py covers manifests. EXEMPT only shrinks.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The two domains cluster-config.yaml owns as cluster_internal_domain and
# cluster_external_domain.
_DOMAIN_RE = re.compile(r"\b(?:esweiss|ericsweiss)\.com\b")

EXEMPT = {
    "scripts/verify-gitlab.sh": (
        "smoke probe URLs; convert with cluster-config-value.sh"
    ),
    "scripts/verify-immich.sh": (
        "smoke probe URLs; convert with cluster-config-value.sh"
    ),
    "scripts/verify-nextcloud.sh": (
        "smoke probe URLs; convert with cluster-config-value.sh"
    ),
    "taskfiles/home-assistant.yml": (
        "smoke probe URLs; convert with cluster-config-value.sh"
    ),
    "taskfiles/wg-easy.yml": (
        "header comment naming the admin hostname, not an address the task "
        "resolves"
    ),
    "taskfiles/flux.yml": (
        "flux bootstrap targets the GitLab host, which is the forge rather than "
        "a cluster-config value"
    ),
    "taskfiles/terraform.yml": (
        "TF_HTTP_* GitLab state-backend URLs; the forge host is not a "
        "cluster-config value"
    ),
    "Taskfile.yml": (
        "GitLab project-ID pointer and the weisssrv-lib clone hint, both forge "
        "addresses rather than cluster-config values"
    ),
}


SCRIPT_SUFFIXES = {".sh", ".py"}


def _is_script(path: Path) -> bool:
    """A file the repo runs. Test modules are out: their literals are fixtures
    and expected values, not addresses a script resolves."""
    return (
        path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in SCRIPT_SUFFIXES
        and not path.name.startswith("test_")
    )


def targets(root: Path) -> list[Path]:
    """Every file the gate reads: scripts under scripts/, plus the Taskfile tree."""
    files = sorted(p for p in (root / "scripts").rglob("*") if _is_script(p))
    # Both suffixes: Task includes either, so one spelling would leave a
    # namespace file unscanned.
    files += sorted({p for s in ("yml", "yaml") for p in (root / "taskfiles").glob(f"*.{s}")})
    if (root / "Taskfile.yml").is_file():
        files.append(root / "Taskfile.yml")
    return files


def offenders(root: Path, exempt: set[str]) -> list[str]:
    """`path:line` for every domain literal in a non-exempt file."""
    found = []
    for path in targets(root):
        rel = path.relative_to(root).as_posix()
        if rel in exempt:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if _DOMAIN_RE.search(line):
                found.append(f"{rel}:{number}")
    return found


def test_the_whole_corpus_is_present_to_scan():
    """A half that stopped matching would narrow the gate to the other one
    without failing."""
    reached = {path.relative_to(REPO).as_posix() for path in targets(REPO)}
    for suffix in sorted(SCRIPT_SUFFIXES):
        assert any(rel.endswith(suffix) for rel in reached), (
            f"scripts/ holds no {suffix} scripts to scan"
        )
    assert "Taskfile.yml" in reached, "the root Taskfile is outside the scan"
    assert any(rel.startswith("taskfiles/") for rel in reached), (
        "the taskfiles/ tree is outside the scan"
    )


def test_every_exemption_names_a_scanned_file():
    """A key the collector never reaches waives nothing, so the literal it was
    written for goes unreported."""
    reached = {path.relative_to(REPO).as_posix() for path in targets(REPO)}
    stray = sorted(rel for rel in EXEMPT if rel not in reached)
    assert not stray, (
        f"EXEMPT names paths outside the scan: {stray} — keys are relative to "
        "the repository root, so a bare basename never matches"
    )


def test_no_unexempted_script_spells_a_site_domain():
    assert not offenders(REPO, set(EXEMPT)), (
        f"site domain literals in {offenders(REPO, set(EXEMPT))} — read the "
        "value with scripts/cluster-config-value.sh instead, or add the file "
        "to EXEMPT with the reason it cannot be converted yet."
    )


def test_every_exemption_is_still_needed():
    """An exemption outliving its literal keeps the next one invisible."""
    stale = sorted(
        rel
        for rel in EXEMPT
        if (REPO / rel).is_file() and not _DOMAIN_RE.search((REPO / rel).read_text())
    )
    assert not stale, f"these files no longer carry a literal: {stale} — drop them"

    missing = sorted(rel for rel in EXEMPT if not (REPO / rel).is_file())
    assert not missing, f"EXEMPT names files that no longer exist: {missing}"


def test_the_collector_reports_a_new_literal(tmp_path):
    """Mutation case: the gate must fail on the file it is meant to catch."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "taskfiles").mkdir()
    (tmp_path / "scripts/clean.sh").write_text('curl "https://${DOMAIN}/health"\n')
    (tmp_path / "scripts/drifted.sh").write_text('curl "https://git.esweiss.com/x"\n')
    (tmp_path / "scripts/ext.sh").write_text('curl "https://auth.ericsweiss.com/"\n')
    (tmp_path / "taskfiles/drifted.yml").write_text("  - echo home.esweiss.com\n")
    assert offenders(tmp_path, set()) == [
        "scripts/drifted.sh:1",
        "scripts/ext.sh:1",
        "taskfiles/drifted.yml:1",
    ]
    assert (
        offenders(
            tmp_path,
            {"scripts/drifted.sh", "scripts/ext.sh", "taskfiles/drifted.yml"},
        )
        == []
    )


def test_the_collector_reaches_python_gates_and_subdirectories(tmp_path):
    """Mutation case for the recursive, two-suffix walk: a `.py` gate and a
    helper in a subdirectory are both in scope; caches and test modules are
    not."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "gate.py").write_text('BASE = "https://api.esweiss.com"\n')
    (scripts / "lib").mkdir()
    (scripts / "lib" / "clean.sh").write_text('printf "%s\\n" "${DOMAIN}"\n')
    (scripts / "lib" / "drifted.py").write_text('HOST = "git.esweiss.com"\n')
    (scripts / "test_fixture.py").write_text('HOST = "git.esweiss.com"\n')
    (scripts / "__pycache__").mkdir()
    (scripts / "__pycache__" / "gate.py").write_text('BASE = "https://api.esweiss.com"\n')
    assert offenders(tmp_path, set()) == [
        "scripts/gate.py:1",
        "scripts/lib/drifted.py:1",
    ]
    assert offenders(tmp_path, {"scripts/gate.py", "scripts/lib/drifted.py"}) == []
