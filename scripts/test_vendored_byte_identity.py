"""Every file vendored from weisssrv-lib must still be byte-identical to it.

Drives the library's check-vendored-copies.py against scripts/vendored-manifest.yml
at the ref .gitlab-ci.yml pins; a checkout without that ref reports unverified.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ci_yaml import NullTagCILoader as _CILoader
from ci_yaml import load_ci, parse_ci

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
MANIFEST = SCRIPTS / "vendored-manifest.yml"
GATE_RELPATH = "scripts/check-vendored-copies.py"
# Where `task lib:sync` puts the checkout (Taskfile.yml's LIB_DIR).
LOCAL_CHECKOUT = ".weisssrv-lib"

# Config files carry site data, not library code, so a same-named one is not a
# vendored copy. `test_` files are this repo's own smoke suites.
_SITE_DATA_SUFFIXES = {".yml", ".yaml", ".env", ".conf", ".toml", ".json"}


def _lib_root() -> Path:
    candidates = []
    explicit = os.environ.get("WEISSSRV_LIB_PATH")
    if explicit:
        candidates.append(Path(explicit))
    candidates += [REPO / LOCAL_CHECKOUT, REPO.parent / "weisssrv-lib"]
    for candidate in candidates:
        if (candidate / GATE_RELPATH).is_file():
            return candidate
    raise AssertionError(
        f"no weisssrv-lib checkout with {GATE_RELPATH} found — run `task lib:sync` "
        f"(it clones one into {LOCAL_CHECKOUT}/ at the pinned ref) or set "
        "$WEISSSRV_LIB_PATH. This gate never skips."
    )


def parse_ci_doc(text: str) -> dict:
    """The jobs document of pipeline YAML in memory, `!` tags nulled."""
    return parse_ci(text, loader=_CILoader)


def load_ci_doc(path) -> dict:
    """The jobs document of a pipeline file, `!` tags nulled."""
    return load_ci(path, loader=_CILoader)


def test_the_loader_nulls_every_gitlab_tag():
    """The suites importing _CILoader read `!reference` as absent, not as data:
    a loader that kept the node would change what they assert."""
    doc = parse_ci_doc('job:\n  rules: !reference [.anchor, rules]\n  tags: !mine [a]\n')
    assert doc["job"] == {"rules": None, "tags": None}


def _pinned_ref() -> str:
    ci = load_ci_doc(REPO / ".gitlab-ci.yml")
    ref = (ci.get("variables") or {}).get("WEISSSRV_LIB_REF")
    assert ref, ".gitlab-ci.yml variables.WEISSSRV_LIB_REF is the single source of the pin"
    return str(ref)


def _ref_available(lib: Path, ref: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(lib), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
    ).returncode == 0


def _lib_script_names(lib: Path, ref: str) -> set[str]:
    """Names under the library's `scripts/` at `ref`, or in its working tree when
    the checkout has no such ref. A file the pin does not ship is not yet a twin.
    """
    if not _ref_available(lib, ref):
        return {p.name for p in (lib / "scripts").iterdir() if p.is_file()}
    listing = subprocess.run(
        ["git", "-C", str(lib), "ls-tree", "--name-only", f"{ref}:scripts"],
        capture_output=True,
        text=True,
    )
    assert listing.returncode == 0, (
        f"could not list scripts/ at {ref} in {lib}:\n{listing.stderr}"
    )
    return {name for name in listing.stdout.split() if name}


def _run_gate(
    *extra: str, manifest: Path | None = None, repo_root: Path | None = None
) -> subprocess.CompletedProcess:
    lib = _lib_root()
    argv = [
        sys.executable,
        str(lib / GATE_RELPATH),
        "--manifest",
        str(manifest or MANIFEST),
        "--repo-root",
        str(repo_root or REPO),
        "--lib-path",
        str(lib),
        *extra,
    ]
    return subprocess.run(argv, capture_output=True, text=True)


def _lib_blob(relpath: str) -> str:
    """One library file at the pinned ref, else from the checkout's tree."""
    lib, ref = _lib_root(), _pinned_ref()
    if _ref_available(lib, ref):
        blob = subprocess.run(
            ["git", "-C", str(lib), "show", f"{ref}:{relpath}"],
            capture_output=True, text=True,
        )
        assert blob.returncode == 0, f"{ref}:{relpath} is not in {lib}:\n{blob.stderr}"
        return blob.stdout
    return (lib / relpath).read_text(encoding="utf-8")


def registered_consumer_entries() -> list[tuple[str, str]]:
    """(kind, repo-relative path) for every manifest entry, or [] with no library
    checkout. The one place that tolerates a missing checkout: the never-skip
    assertion belongs to the gate tests, not to collection time.
    """
    try:
        _lib_root()
    except AssertionError:
        return []
    result = _run_gate("--list")
    if result.returncode != 0:
        return []
    return [
        (parts[0], parts[1])
        for parts in (line.split("\t") for line in result.stdout.splitlines())
        if len(parts) >= 3
    ]


def registered_consumer_paths() -> list[str]:
    """Repo-relative paths registered as byte-identical copies (kind vendored)."""
    return [path for kind, path in registered_consumer_entries() if kind == "vendored"]


@pytest.fixture(scope="module")
def registered() -> list[tuple[str, str, str]]:
    """(kind, consumer_path, lib_path) for every entry the manifest lists."""
    result = _run_gate("--list")
    assert result.returncode == 0, (
        f"the library gate could not read {MANIFEST}:\n{result.stderr}"
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    assert rows, f"{MANIFEST} declares no copies"
    return rows


def test_lib_checkout_carries_the_pinned_ref():
    """Drift against a ref the copies never came from misleads whoever
    re-vendors. Under $CI the working-tree fallback is not acceptable: it
    certifies the copies against an unreleased library state.
    """
    lib = _lib_root()
    ref = _pinned_ref()
    if _ref_available(lib, ref):
        return
    message = (
        f"{lib} has no {ref} (the library tag is cut when its MR merges); "
        "byte-identity was compared against the checkout's working tree"
    )
    if os.environ.get("CI"):
        pytest.fail(
            message + ". Fetch the library tags in the job image, or bump "
            "WEISSSRV_LIB_REF to a tag that exists."
        )
    pytest.skip(message)


def test_registered_copies_are_reconciled(registered):
    """Every vendored copy is identical and every fork still reconciled, at the pin."""
    ref = _pinned_ref()
    at_pin = _ref_available(_lib_root(), ref)
    result = _run_gate(*(["--ref", ref] if at_pin else []))
    assert result.returncode != 2, f"the vendored-copy gate could not run:\n{result.stderr}"
    if result.returncode == 0:
        if not at_pin:
            # Byte-identity is a claim about the pin, so a clean working-tree
            # comparison is unverified, never a pass.
            pytest.skip(
                f"{_lib_root()} has no {ref}, so the copies were compared against its "
                f"working tree: clean there does not certify them at {ref}"
            )
        return

    # Distinguish the two failures that read identically but need opposite
    # fixes: copies that match no version of the library (re-vendor them) from
    # copies that match its working tree while the pin lags (bump the pin).
    hint = (
        "Re-vendor from weisssrv-lib and review the diff; site data belongs in the "
        "script's config file, never in the copy. A fork must ABSORB the library's "
        "change, then have its reconciled_sha256 updated in scripts/vendored-manifest.yml."
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


def test_the_engine_reports_a_mutated_copy(registered, tmp_path):
    """Mutation case: the live tree only ever exercises the clean direction, so
    the reporting branch is proven against a scratch copy taken from the library.
    """
    lib_path, consumer = next(
        (lib, path) for kind, path, lib in registered
        if kind == "vendored" and path.endswith(".py")
    )
    content = _lib_blob(lib_path)
    manifest = tmp_path / "scripts" / "vendored-manifest.yml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(f"vendored:\n  - lib: {lib_path}\n    consumer: {consumer}\n")
    copy = tmp_path / consumer
    copy.parent.mkdir(parents=True, exist_ok=True)
    at_pin = ["--ref", _pinned_ref()] if _ref_available(_lib_root(), _pinned_ref()) else []

    copy.write_text(content)
    clean = _run_gate(*at_pin, manifest=manifest, repo_root=tmp_path)
    assert clean.returncode == 0, (
        f"a scratch copy taken from the library is reported as drifted:\n"
        f"{clean.stdout}{clean.stderr}"
    )

    copy.write_text(content.replace("\n", "\n# drift\n", 1))
    drifted = _run_gate(*at_pin, manifest=manifest, repo_root=tmp_path)
    assert drifted.returncode == 1, (
        f"one mutated byte in {consumer} was not reported (exit "
        f"{drifted.returncode}):\n{drifted.stdout}{drifted.stderr}"
    )
    assert consumer in drifted.stdout + drifted.stderr, (
        f"the drift report does not name {consumer}:\n{drifted.stdout}{drifted.stderr}"
    )


def test_every_registered_copy_exists_here(registered):
    """A manifest entry is a claim about this repo's layout, so a moved or
    deleted copy has to surface here rather than as a silent no-op."""
    missing = sorted(path for _kind, path, _lib in registered if not (REPO / path).is_file())
    assert not missing, (
        f"listed as vendored/forked from weisssrv-lib but absent here: {missing}. "
        "Either re-vendor them, or drop the entry from scripts/vendored-manifest.yml "
        "in the same commit — the manifest is this repo's to edit."
    )


def test_every_library_twin_is_registered(registered):
    """A script sharing a name with a library script at the pin is in the manifest."""
    lib = _lib_root()
    lib_names = _lib_script_names(lib, _pinned_ref())
    assert lib_names, (
        f"no file listed under scripts/ at {_pinned_ref()} in {lib} — every "
        "twin comparison below would pass over nothing"
    )
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
        "scripts with a weisssrv-lib twin that scripts/vendored-manifest.yml does not "
        f"cover: {undeclared} — add them there, or rename them so they are not "
        "mistaken for copies."
    )


MOLECULE_SHARED = "ansible_collections/weisssrv/infra/molecule-shared"


def _lib_offer(lib: Path, ref: str) -> set[str]:
    """The library's offer list at `ref`, or its working tree when the checkout
    has no such ref. An entry absent from the offer is not yet registerable."""
    relpath = "scripts/vendorable-paths.yml"
    if _ref_available(lib, ref):
        blob = subprocess.run(
            ["git", "-C", str(lib), "show", f"{ref}:{relpath}"],
            capture_output=True, text=True,
        )
        raw = blob.stdout if blob.returncode == 0 else ""
    else:
        raw = (lib / relpath).read_text() if (lib / relpath).is_file() else ""
    return set((yaml.safe_load(raw) or {}).get("vendorable") or [])


def test_every_shared_molecule_twin_is_registered(registered):
    """A copy of the collection's molecule-shared/ YAML scaffolding is in the manifest."""
    lib = _lib_root()
    offered = _lib_offer(lib, _pinned_ref())
    local = REPO / "ansible" / "molecule"
    if not local.is_dir():
        return
    covered = {path for _kind, path, _lib in registered}
    undeclared = sorted(
        str(p.relative_to(REPO))
        for p in local.rglob("*.yml")
        if p.is_file()
        and f"{MOLECULE_SHARED}/{p.relative_to(local)}" in offered
        and str(p.relative_to(REPO)) not in covered
    )
    assert not undeclared, (
        "copies of the collection's molecule-shared/ files that "
        f"scripts/vendored-manifest.yml does not cover: {undeclared} — register "
        "each one as vendored or forked."
    )


# A vendored gate is byte-identical to the library, so every sibling module it
# imports comes from there too and must be registered beside it. The manifest is
# hand-maintained; this is the derived cross-check that it is complete.
_SIBLING_IMPORT = re.compile(
    r"^\s*(?:from ([A-Za-z0-9_]+) import|import ([A-Za-z0-9_]+)\b)", re.M
)
_SIBLING_LOAD = re.compile(r'parent\s*/\s*"([A-Za-z0-9_.-]+\.py)"')
# Imported by a gate but shipped by the environment, not by scripts/.
_NOT_SIBLINGS = frozenset({"yaml", "jinja2", "pytest", "requests", "urllib3"})


def companion_modules(text: str) -> set[str]:
    """Sibling `scripts/` files a gate's source loads, as file names."""
    found = set()
    for first, second in _SIBLING_IMPORT.findall(text):
        module = (first or second).split(".")[0]
        if module in sys.stdlib_module_names or module in _NOT_SIBLINGS:
            continue
        found.add(f"{module}.py")
    found.update(_SIBLING_LOAD.findall(text))
    return found


def unregistered_companions(sources: dict[str, str], registered: set[str]) -> list[str]:
    """`gate -> companion` pairs where the gate is vendored and the companion is
    not, so a re-vendor or pin bump leaves the gate importing a missing module.
    """
    gaps = []
    for path, text in sorted(sources.items()):
        if path not in registered or not path.endswith((".py", ".sh")):
            continue
        own = Path(path).name
        for companion in sorted(companion_modules(text)):
            if companion == own:
                continue
            if f"scripts/{companion}" not in registered:
                gaps.append(f"{path} imports {companion}")
    return gaps


def test_every_companion_of_a_vendored_gate_is_registered(registered):
    vendored = {path for kind, path, _lib in registered if kind == "vendored"}
    sources = {
        f"scripts/{p.name}": p.read_text(encoding="utf-8")
        for p in SCRIPTS.iterdir()
        if p.is_file() and p.suffix in (".py", ".sh")
    }
    gaps = unregistered_companions(sources, vendored)
    assert not gaps, (
        "vendored gates whose companion module is not registered in "
        f"scripts/vendored-manifest.yml: {gaps} — vendor the companion and add "
        "it to the `vendored:` list in the same commit as the pin bump, or the "
        "gate exits on a missing module."
    )


def test_a_vendored_gate_run_without_its_companion_exits_two(registered, tmp_path):
    """Registration is a manifest claim; this is the runtime half.

    Each gate runs from a directory holding only itself, so the guard naming
    the missing module is the path under test. rc 2 is the operator contract.
    """
    vendored = {path for kind, path, _lib in registered if kind == "vendored"}
    checked = []
    for path in sorted(vendored):
        name = Path(path).name
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        source = (REPO / path).read_text(encoding="utf-8")
        companions = sorted(
            c for c in companion_modules(source)
            if c != name and f"scripts/{c}" in vendored
        )
        if not companions:
            continue
        alone = tmp_path / name
        alone.write_text(source)
        run = subprocess.run(
            [sys.executable, str(alone), "--help"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        alone.unlink()
        assert run.returncode == 2, (
            f"{path} run without {companions} exited {run.returncode}; the "
            "import guard must print to stderr and exit 2:\n"
            + run.stdout + run.stderr
        )
        assert any(c in run.stderr for c in companions), (
            f"{path} does not name the missing companion {companions}:\n{run.stderr}"
        )
        checked.append(path)
    assert checked, (
        "no vendored gate imports a registered companion — this arm inspected "
        "nothing, so the guards are unproven"
    )


def test_the_companion_cross_check_fails_on_an_unregistered_companion():
    """Mutation case: the live tree is clean, so the gate is proven on a fixture."""
    sources = {
        "scripts/check-thing.py": "import gate_common\n",
        "scripts/run-thing.sh": 'python3 - <<EOF\nimport ci_yaml\nEOF\n',
    }
    registered = {"scripts/check-thing.py", "scripts/run-thing.sh"}
    assert unregistered_companions(sources, registered) == [
        "scripts/check-thing.py imports gate_common.py",
        "scripts/run-thing.sh imports ci_yaml.py",
    ]
    assert unregistered_companions(
        sources, registered | {"scripts/gate_common.py", "scripts/ci_yaml.py"}
    ) == []
