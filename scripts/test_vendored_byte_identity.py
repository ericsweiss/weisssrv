"""Every file vendored from weisssrv-lib must still be byte-identical to it.

Nothing else notices when a vendored copy drifts: the fix the library shipped is
simply absent, and the next re-vendor silently reverts whatever was edited here.

**The copy relationship is recorded once, in the library** —
`weisssrv-lib/scripts/vendored-paths.yml`, read by
`weisssrv-lib/scripts/check-vendored-copies.py`. This module drives that gate
rather than keeping a second list: a file the library starts or stops shipping
reaches this gate at the next bump instead of waiting for someone to remember to
re-list it here. It covers more than `scripts/` (the shared lint profiles live at
this repo's root) and it distinguishes vendored copies from declared forks,
asserting a fork still differs AND that the library side has not moved since the
fork was last reconciled.

The library checkout comes from `$WEISSSRV_LIB_PATH`, else a sibling
`../weisssrv-lib`. There is no skip-when-missing path: an unavailable checkout
fails, because a gate that quietly disables itself is not a gate. Blobs are read
at the ref `.gitlab-ci.yml` pins, falling back to the checkout's working tree
when that ref is not in the checkout yet — the release tag is cut after the
library MR merges, so a pre-release run compares against the branch it will be
tagged from.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
CONSUMER = "weisssrv"
GATE_RELPATH = "scripts/check-vendored-copies.py"

# Config files carry site data, not library code, so a same-named one is not a
# vendored copy. `test_` files are this repo's own smoke suites.
_SITE_DATA_SUFFIXES = {".yml", ".yaml", ".env", ".conf", ".toml", ".json"}


def _lib_root() -> Path:
    explicit = os.environ.get("WEISSSRV_LIB_PATH")
    candidate = Path(explicit) if explicit else REPO.parent / "weisssrv-lib"
    if (candidate / GATE_RELPATH).is_file():
        return candidate
    raise AssertionError(
        f"no weisssrv-lib checkout with {GATE_RELPATH} found (set $WEISSSRV_LIB_PATH, "
        f"or place one at {REPO.parent / 'weisssrv-lib'}). This gate never skips."
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
        ["git", "-C", str(lib), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
    ).returncode == 0


def _run_gate(*extra: str) -> subprocess.CompletedProcess:
    lib = _lib_root()
    argv = [
        sys.executable,
        str(lib / GATE_RELPATH),
        "--consumer",
        CONSUMER,
        "--repo-root",
        str(REPO),
        "--lib-path",
        str(lib),
        *extra,
    ]
    return subprocess.run(argv, capture_output=True, text=True)


def registered_consumer_paths() -> list[str]:
    """Repo-relative paths the library registers for this consumer, or [] when
    no library checkout is reachable.

    Collection-time helper for test_vendored_smoke.py's parametrisation. It is
    deliberately the one place that tolerates a missing checkout: the
    never-skip assertion belongs to the gate tests below, and duplicating it at
    import time would turn a clear failure into a collection error.
    """
    try:
        _lib_root()
    except AssertionError:
        return []
    result = _run_gate("--list")
    if result.returncode != 0:
        return []
    return [
        parts[1] for parts in (line.split("\t") for line in result.stdout.splitlines())
        if len(parts) >= 3 and parts[0] == "vendored"
    ]


@pytest.fixture(scope="module")
def registered() -> list[tuple[str, str, str]]:
    """(kind, consumer_path, lib_path) for every entry registered for weisssrv."""
    result = _run_gate("--list")
    assert result.returncode == 0, (
        f"the library gate could not read its registry for {CONSUMER}:\n{result.stderr}"
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    assert rows, f"the library registry declares no copies for {CONSUMER}"
    return rows


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


def test_registered_copies_are_reconciled(registered):
    """The gate itself: every vendored copy identical, every fork still a fork
    and still reconciled against the library blob it was forked from.

    Compared at the PINNED ref, never at whatever the checkout happens to have.
    Copies that match an unreleased working tree while the pin still names the
    previous tag are a real inconsistency — the pipeline installs the pin — so
    that state fails here and is resolved by bumping the pin, not by relaxing
    the comparison.
    """
    ref = _pinned_ref()
    at_pin = _ref_available(_lib_root(), ref)
    result = _run_gate(*(["--ref", ref] if at_pin else []))
    assert result.returncode != 2, f"the vendored-copy gate could not run:\n{result.stderr}"
    if result.returncode == 0:
        return

    # Distinguish the two failures that read identically but need opposite
    # fixes: copies that match no version of the library (re-vendor them) from
    # copies that match its working tree while the pin lags (bump the pin).
    hint = (
        "Re-vendor from weisssrv-lib and review the diff; site data belongs in the "
        "script's config file, never in the copy. A fork must ABSORB the library's "
        "change, then have its reconciled_sha256 updated in the library registry."
    )
    if at_pin and _run_gate().returncode == 0:
        hint = (
            f"These copies ARE current with the library working tree — they match it "
            f"exactly — but WEISSSRV_LIB_REF still pins {ref}, and the pin is what "
            f"the pipeline installs. This is the expected pre-release state while a "
            f"library tag is being cut. Resolve it by bumping the pin (WEISSSRV_LIB_REF, "
            f"ansible/requirements.yml, the terraform module ?ref= pins) once the tag "
            f"exists — not by re-vendoring backwards."
        )
    raise AssertionError(f"{result.stdout}{result.stderr}\n{hint}")


def test_every_registered_copy_exists_here(registered):
    """A registry entry is a claim about this repo's layout, so a moved or
    deleted copy has to surface here rather than as a silent no-op."""
    missing = sorted(path for _kind, path, _lib in registered if not (REPO / path).is_file())
    assert not missing, (
        f"registered as vendored/forked from weisssrv-lib but absent here: {missing}. "
        "Either re-vendor them, or get the entry dropped from the library registry "
        "(a consumer's layout is a library-release event)."
    )


def test_every_library_twin_is_registered(registered):
    """Local smoke test: a script sharing a name with a library script must be
    covered by the registry.

    The library cannot notice a file this repo added; without this, copying a
    library script in by hand and never registering it leaves it ungated — the
    exact failure the gate exists to prevent.
    """
    lib = _lib_root()
    lib_names = {p.name for p in (lib / "scripts").iterdir() if p.is_file()}
    covered = {Path(path).name for _kind, path, _lib in registered}
    undeclared = sorted(
        p.name
        for p in SCRIPTS.iterdir()
        if p.is_file()
        and p.name in lib_names
        and p.name not in covered
        and p.suffix not in _SITE_DATA_SUFFIXES
        and not p.name.startswith("test_")
    )
    assert not undeclared, (
        "scripts with a weisssrv-lib twin that the library registry does not cover: "
        f"{undeclared} — add them to weisssrv-lib/scripts/vendored-paths.yml under "
        f"consumers.{CONSUMER}, or rename them so they are not mistaken for copies."
    )
