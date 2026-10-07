"""Coverage for check-molecule-image-pin.py.

The live tree is expected to be pinned correctly, so every arm runs against a
fixture repo: the gate's value is that it FAILS on drift.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
gate = load_script("check-molecule-image-pin.py")

IMAGE = "registry.esweiss.com/eric/weisssrv-lib/molecule-test"


def molecule_yml(tag: str) -> str:
    return textwrap.dedent(
        f"""\
        driver:
          name: docker
        platforms:
          - name: instance
            image: ${{MOLECULE_TEST_IMAGE:-{IMAGE}:{tag}}}
        """
    )


def build_repo(tmp_path: Path, ref: str, scenarios: dict[str, str]) -> Path:
    (tmp_path / ".gitlab-ci.yml").write_text(f"variables:\n  WEISSSRV_LIB_REF: {ref}\n")
    for scenario, tag in scenarios.items():
        suite, name = scenario.split("/", 1)
        path = tmp_path / "ansible/integration-tests" / suite / "molecule" / name
        path.mkdir(parents=True)
        (path / "molecule.yml").write_text(molecule_yml(tag))
    return tmp_path


def test_matching_pin_passes(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/default": "v1.2.3"})
    assert gate.check("v1.2.3", repo) == []


def test_drift_fails(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/default": "v1.0.0"})
    problems = gate.check("v1.2.3", repo)
    assert len(problems) == 1
    assert "'v1.0.0'" in problems[0]


def test_a_non_default_scenario_is_covered(tmp_path):
    """The sibling matrix gate accepts any scenario name, so this one must too."""
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/upgrade": "v1.0.0"})
    problems = gate.check("v1.2.3", repo)
    assert len(problems) == 1
    assert "upgrade/molecule.yml" in problems[0]


def test_no_literals_is_an_error_not_a_pass(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {})
    assert gate.check("v1.2.3", repo) == ["no molecule image literals found — has the fallback moved?"]


def test_fix_rewrites_every_scenario(tmp_path):
    repo = build_repo(
        tmp_path, "v1.2.3", {"dns-stack/default": "v1.0.0", "mail-stack/upgrade": "v1.1.0"}
    )
    assert gate.fix("v1.2.3", repo) == 2
    assert gate.check("v1.2.3", repo) == []


def test_ci_file_retargets_the_whole_operation(tmp_path):
    """--ci-file selects the tree that is read AND rewritten, never this repo."""
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/default": "v1.0.0"})
    assert gate.main(["--ci-file", str(repo / ".gitlab-ci.yml")]) == 1
    assert gate.main(["--ci-file", str(repo / ".gitlab-ci.yml"), "--fix"]) == 0
    assert "v1.2.3" in (repo / "ansible/integration-tests/dns-stack/molecule/default/molecule.yml").read_text()


def test_a_non_release_ref_is_rejected(tmp_path):
    repo = build_repo(tmp_path, "main", {"dns-stack/default": "v1.2.3"})
    with pytest.raises(SystemExit):
        gate.declared_ref(repo / ".gitlab-ci.yml")
