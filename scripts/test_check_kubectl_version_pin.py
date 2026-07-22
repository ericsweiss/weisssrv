"""Tests for scripts/check-kubectl-version-pin.py (the kubectl/k3s skew gate).

Exercises the version extraction + the +/-1 minor skew classification, plus a
smoke check that the real repo pin currently passes.

Run via `pytest scripts/` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "check-kubectl-version-pin.py"

# Import the hyphenated-name module the same way test_check_doc_links.py does.
_spec = importlib.util.spec_from_file_location("check_kubectl_version_pin", _SCRIPT)
assert _spec and _spec.loader
ckp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ckp)


def _ci(major: int, minor: int) -> str:
    return f'    KUBECTL_URL="https://dl.k8s.io/release/v{major}.{minor}.4/bin/linux/amd64/kubectl"\n'


def _cm(major: int, minor: int) -> str:
    return f"data:\n  k3s_version: v{major}.{minor}.1+k3s1\n"


class TestCheck:
    def test_equal_minor_passes(self):
        code, msg = ckp.check(_ci(1, 33), _cm(1, 33))
        assert code == 0
        assert "within the supported" in msg

    def test_one_minor_below_passes(self):
        code, _ = ckp.check(_ci(1, 32), _cm(1, 33))
        assert code == 0

    def test_one_minor_above_passes(self):
        code, _ = ckp.check(_ci(1, 34), _cm(1, 33))
        assert code == 0

    def test_two_minor_skew_fails(self):
        code, msg = ckp.check(_ci(1, 31), _cm(1, 33))
        assert code == 1
        assert "outside Kubernetes' supported" in msg

    def test_major_mismatch_fails(self):
        code, msg = ckp.check(_ci(2, 33), _cm(1, 33))
        assert code == 1
        assert "outside Kubernetes' supported" in msg

    def test_missing_kubectl_pin_fails(self):
        code, msg = ckp.check("no pin here\n", _cm(1, 33))
        assert code == 1
        assert "kubectl pin" in msg

    def test_missing_k3s_version_fails(self):
        code, msg = ckp.check(_ci(1, 33), "data:\n  other: 1\n")
        assert code == 1
        assert "k3s_version" in msg


class TestRealRepo:
    def test_repo_pin_is_within_skew(self):
        # The gate must be green on the tree it ships with (preventive check).
        code, msg = ckp.check(ckp.CI_YAML.read_text(), ckp.VERSIONS_CM.read_text())
        assert code == 0, msg
