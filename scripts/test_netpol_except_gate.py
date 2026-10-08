"""The LAN-fence gate still fails on this repo's own policy shapes.

Byte identity and import are covered elsewhere. These run the gate with this
repo's config over a copy of a live policy, proving an emptied `except:` fails.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
GATE = SCRIPTS / "check-netpol-except-parity.py"
CONFIG = SCRIPTS / "netpol-except.yaml"
PUBLIC_EGRESS = "kubernetes/components/netpol-egress-public/allow-egress-public.yaml"


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--config", str(CONFIG), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def policy(tmp_path: Path) -> Path:
    """A copy of the component every public-egress app composes in."""
    source = REPO / PUBLIC_EGRESS
    assert "0.0.0.0/0" in source.read_text(), (
        f"{PUBLIC_EGRESS} no longer carries a /0 egress peer — point this suite "
        "at the policy that does"
    )
    target = tmp_path / "allow-egress-public.yaml"
    shutil.copyfile(source, target)
    return target


def test_the_live_policy_passes(policy: Path) -> None:
    run = _run(policy)
    assert run.returncode == 0, f"{run.stdout}{run.stderr}"


def test_an_emptied_except_list_fails(policy: Path) -> None:
    """Mutation case: the deletion that re-opens the LAN."""
    doc = yaml.safe_load(policy.read_text())
    for rule in doc["spec"]["egress"]:
        for peer in rule.get("to") or []:
            if "ipBlock" in peer:
                peer["ipBlock"]["except"] = []
    policy.write_text(yaml.safe_dump(doc))
    run = _run(policy)
    assert run.returncode == 1, f"{run.stdout}{run.stderr}"
    assert "no except-list" in run.stdout


def test_a_shortened_except_list_fails(policy: Path) -> None:
    """A list that is no longer canonical is a partial fence, not a pass."""
    doc = yaml.safe_load(policy.read_text())
    for rule in doc["spec"]["egress"]:
        for peer in rule.get("to") or []:
            if "ipBlock" in peer and peer["ipBlock"].get("except"):
                peer["ipBlock"]["except"] = peer["ipBlock"]["except"][:1]
    policy.write_text(yaml.safe_dump(doc))
    run = _run(policy)
    assert run.returncode == 1, f"{run.stdout}{run.stderr}"


def test_a_corpus_with_no_policy_is_not_a_pass(tmp_path: Path) -> None:
    """A renamed manifest subtree must exit 2, not green with nothing behind it."""
    (tmp_path / "empty.yaml").write_text("kind: ConfigMap\n")
    run = _run(tmp_path)
    assert run.returncode == 2, f"{run.stdout}{run.stderr}"
