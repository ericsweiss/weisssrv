"""Every library template this pipeline includes is passed the inputs it declares.

check-lib-pins.py compares refs only. This drives the library's
check-include-contract.py over .gitlab-ci.yml at the pinned ref instead.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from test_vendored_byte_identity import _lib_root, _pinned_ref, _ref_available

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
GATE_RELPATH = "scripts/check-include-contract.py"
CI_FILE = ".gitlab-ci.yml"


def _gate(tree: Path) -> Path:
    """The gate to run: this repo's vendored copy once it exists, else the
    library checkout's. The pinned tree has it only after the bump that offers
    it, so a pin predating the gate is a skip, not a failure."""
    for candidate in (SCRIPTS / Path(GATE_RELPATH).name, tree / GATE_RELPATH,
                      _lib_root() / GATE_RELPATH):
        if candidate.is_file():
            return candidate
    pytest.skip(f"no {GATE_RELPATH} in this repo or the weisssrv-lib checkout")


@pytest.fixture(scope="module")
def lib_tree(tmp_path_factory) -> Path:
    """The library tree the `project:` includes resolve in, at the pinned ref.

    A checkout without the ref falls back to its working tree, as the
    vendored-copy gate does.
    """
    lib = _lib_root()
    ref = _pinned_ref()
    if not _ref_available(lib, ref):
        return lib
    out = tmp_path_factory.mktemp("lib-at-pin")
    archive = subprocess.run(
        ["git", "-C", str(lib), "archive", ref, "ci"], capture_output=True
    )
    assert archive.returncode == 0, (
        f"could not archive ci/ at {ref} from {lib}: {archive.stderr.decode()}"
    )
    extract = subprocess.run(
        ["tar", "-x", "-C", str(out)], input=archive.stdout, capture_output=True
    )
    assert extract.returncode == 0, f"could not extract ci/ at {ref}: {extract.stderr.decode()}"
    return out


def _run(gate: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(gate), *args], capture_output=True, text=True
    )


def test_the_pipeline_matches_the_include_contract(lib_tree):
    gate = _gate(lib_tree)
    run = _run(gate, "--repo-root", str(REPO), "--ci-file", CI_FILE,
               "--lib-path", str(lib_tree))
    assert run.returncode == 0, (
        f"{CI_FILE} violates the input contract of the templates it includes at "
        f"{_pinned_ref()}:\n{run.stdout}{run.stderr}"
    )
    assert "checked" in run.stdout, (
        f"the gate reported no included file checked:\n{run.stdout}{run.stderr}"
    )


def test_an_omitted_required_input_is_reported(tmp_path, lib_tree):
    """Mutation case: the default-less input this gate exists to catch."""
    gate = _gate(lib_tree)
    (tmp_path / "template.yml").write_text(
        "spec:\n  inputs:\n    image:\n---\ndemo:\n  stage: test\n  script:\n    - true\n"
    )
    (tmp_path / CI_FILE).write_text("include:\n  - local: template.yml\n")
    run = _run(gate, "--repo-root", str(tmp_path), "--ci-file", CI_FILE)
    assert run.returncode == 1, (
        f"an include passing no value for a REQUIRED input must fail the gate:\n"
        f"{run.stdout}{run.stderr}"
    )
    assert "requires input 'image'" in run.stdout + run.stderr


def test_an_undeclared_input_is_reported(tmp_path, lib_tree):
    """Mutation case: a key the included template does not declare."""
    gate = _gate(lib_tree)
    (tmp_path / "template.yml").write_text(
        "demo:\n  stage: test\n  script:\n    - true\n"
    )
    (tmp_path / CI_FILE).write_text(
        "include:\n  - local: template.yml\n    inputs:\n      typo: 1\n"
    )
    run = _run(gate, "--repo-root", str(tmp_path), "--ci-file", CI_FILE)
    assert run.returncode == 1, (
        f"an undeclared inputs: key must fail the gate:\n{run.stdout}{run.stderr}"
    )
    assert "declares no input 'typo'" in run.stdout + run.stderr
