"""Every vendored file must be byte-identical to weisssrv-lib's copy.

Nothing else notices when a vendored copy drifts: the fix the library shipped is
simply absent, and the next re-vendor silently reverts whatever was edited here.
This gate makes both directions visible — a copy that drifted, and a copy the
library stopped shipping.

The library checkout comes from `$WEISSSRV_LIB_PATH`, else a sibling
`../weisssrv-lib`. There is no skip-when-missing path: an unavailable checkout
fails, because a gate that quietly disables itself is not a gate. Blobs are read
at the ref `.gitlab-ci.yml` pins (`git show <ref>:scripts/<name>`), falling back
to the checkout's working tree when that ref is not in the checkout yet — the
release tag is cut after the library MR merges, so a pre-release run compares
against the branch it will be tagged from.

VENDORED covers scripts/ where both sides share a path. VENDORED_PATHS is for
vendored files that land somewhere else here than in the library — the shared
lint profiles, which the tools discover by their conventional name at the repo
root. Without it a config vendored to the root has no gate at all: `task
lint:ruff` and the CI python-lint job both pass `--config ruff.toml`, so a lib
bump that tightens the shared profile silently does not apply here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Copied verbatim from weisssrv-lib/scripts/. Site data lives in the config
# files these read, never in the copy.
VENDORED = {
    "b2-bucket-drift.py",
    "check-deploy-coverage.sh",
    "check-doc-links.py",
    "check-hpa-vpa-invariant.py",
    "check-kubectl-version-pin.py",
    "check-lib-pins.py",
    "check-taskfile.sh",
    "check-versions.py",
    "find-pve-host-for-vm.sh",
    "find-reachable-host.sh",
    "flux-render.sh",
    "generate-hosts-env.py",
    "generate-versions-configmap.py",
    "kubeconform-skipped.py",
    "lint-prometheus-config.sh",
    "molecule-retry.sh",
    "resolve-tool.sh",
    "sanitize-junit-expected-failures.py",
    "shell-lib.sh",
    "validate-helm-values.py",
    "version-bump-mr.py",
    "version-check-ci.py",
}

# Vendored files whose path differs between the two repos: {local -> library}.
# The library groups its shared lint profiles under lint/; the tools here
# discover them by their conventional name at the repo root.
VENDORED_PATHS = {
    "ruff.toml": "lint/ruff.toml",
}

# Deliberate forks of a library script, with the reason. Re-converge by getting
# the difference upstream, not by editing the library copy from here.
FORKED = {
    "extract-prometheus-config.py": (
        "rules extraction must union the HelmRelease with the standalone "
        "PrometheusRule manifests under observability/rules/; the library copy "
        "reads the HelmRelease alone, which this cluster no longer populates"
    ),
}

_SITE_DATA_SUFFIXES = {".yml", ".yaml", ".env", ".conf", ".toml", ".json"}


def _lib_root() -> Path:
    explicit = os.environ.get("WEISSSRV_LIB_PATH")
    candidates = [Path(explicit)] if explicit else [REPO.parent / "weisssrv-lib"]
    for path in candidates:
        if (path / "scripts").is_dir():
            return path
    raise AssertionError(
        "no weisssrv-lib checkout found (set $WEISSSRV_LIB_PATH, or place one at "
        f"{REPO.parent / 'weisssrv-lib'}). This gate never skips."
    )


class _CILoader(yaml.SafeLoader):
    """SafeLoader tolerating GitLab's `!reference` tags, subclassed so the
    constructor is not registered on the global SafeLoader."""


_CILoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _pinned_ref() -> str:
    ci = yaml.load((REPO / ".gitlab-ci.yml").read_text(), Loader=_CILoader) or {}
    ref = (ci.get("variables") or {}).get("WEISSSRV_LIB_REF")
    assert ref, ".gitlab-ci.yml variables.WEISSSRV_LIB_REF is the single source of the pin"
    return str(ref)


def _ref_available(lib: Path, ref: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(lib), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
    ).returncode == 0


def _lib_bytes(lib: Path, ref: str | None, rel: str) -> bytes | None:
    """The library's bytes for a repo-relative path, at `ref` or the worktree."""
    if ref:
        run = subprocess.run(
            ["git", "-C", str(lib), "show", f"{ref}:{rel}"], capture_output=True
        )
        return run.stdout if run.returncode == 0 else None
    path = lib / rel
    return path.read_bytes() if path.is_file() else None


@pytest.fixture(scope="module")
def lib_source() -> tuple[Path, str | None]:
    lib = _lib_root()
    ref = _pinned_ref()
    return lib, ref if _ref_available(lib, ref) else None


def test_lib_checkout_carries_the_pinned_ref():
    """Drift reported against a ref the copies never came from misleads whoever
    re-vendors, so the fallback is announced rather than assumed."""
    lib = _lib_root()
    ref = _pinned_ref()
    if not _ref_available(lib, ref):
        pytest.skip(
            f"{lib} has no {ref} yet (the library tag is cut when its MR merges); "
            "byte-identity was compared against the checkout's working tree"
        )


@pytest.mark.parametrize("name", sorted(VENDORED))
def test_vendored_copy_is_byte_identical(name, lib_source):
    lib, ref = lib_source
    upstream = _lib_bytes(lib, ref, f"scripts/{name}")
    assert upstream is not None, (
        f"{name} is declared vendored but the library does not ship it at "
        f"{ref or 'HEAD'} — drop it, or move it to a local script"
    )
    local = (SCRIPTS / name).read_bytes()
    assert local == upstream, (
        f"scripts/{name} differs from weisssrv-lib at {ref or 'HEAD'}. Re-copy it "
        "and review the diff; site data belongs in this script's config file, "
        "not in the copy."
    )


@pytest.mark.parametrize("local_rel", sorted(VENDORED_PATHS))
def test_relocated_vendored_file_is_byte_identical(local_rel, lib_source):
    """The same gate for files vendored to a DIFFERENT path than the library's.

    ruff.toml is the whole reason this exists: .gitlab-ci.yml documents it as
    "the library's shared profile vendored to this repo's root", and both the
    CI python-lint job and `task lint:ruff` pass it as `--config ruff.toml`.
    Comparing only same-named scripts/ entries left it entirely ungated.
    """
    lib, ref = lib_source
    lib_rel = VENDORED_PATHS[local_rel]
    upstream = _lib_bytes(lib, ref, lib_rel)
    assert upstream is not None, (
        f"{local_rel} is declared vendored from {lib_rel} but the library does "
        f"not ship it at {ref or 'HEAD'} — drop the entry, or fix the path"
    )
    local_path = REPO / local_rel
    assert local_path.is_file(), f"{local_rel} is declared vendored but is missing here"
    assert local_path.read_bytes() == upstream, (
        f"{local_rel} differs from weisssrv-lib's {lib_rel} at {ref or 'HEAD'}. "
        f"Re-copy it; the shared lint profile is the library's to change."
    )


@pytest.mark.parametrize("name", sorted(FORKED))
def test_forked_copy_still_differs(name, lib_source):
    """A fork that has become identical is a fork that should be re-vendored."""
    lib, ref = lib_source
    upstream = _lib_bytes(lib, ref, f"scripts/{name}")
    if upstream is None:
        pytest.skip(f"the library no longer ships {name}; the fork is now a local script")
    assert (SCRIPTS / name).read_bytes() != upstream, (
        f"scripts/{name} is byte-identical to the library again — move it into "
        "VENDORED and delete the FORKED entry"
    )


def test_every_library_twin_is_declared(lib_source):
    """A script with a library twin must be declared vendored or forked.

    Without this, re-vendoring a file and forgetting to list it leaves it
    ungated, which is the failure the whole gate exists to prevent.
    """
    lib, _ref = lib_source
    lib_names = {p.name for p in (lib / "scripts").iterdir() if p.is_file()}
    declared = VENDORED | set(FORKED)
    undeclared = sorted(
        p.name
        for p in SCRIPTS.iterdir()
        if p.is_file()
        and p.name in lib_names
        and p.name not in declared
        and p.suffix not in _SITE_DATA_SUFFIXES
        and not p.name.startswith("test_")
    )
    assert not undeclared, (
        "scripts with a weisssrv-lib twin that are neither VENDORED nor FORKED: "
        f"{undeclared}"
    )
