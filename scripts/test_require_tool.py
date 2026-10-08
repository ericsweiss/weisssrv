"""conftest.require_tool fails rather than skips under $CI.

A binary-driven gate that skips in CI certifies a check the job never ran, so
the fail-vs-skip choice is itself gated here.
"""
from __future__ import annotations

import pytest
from conftest import require_tool

MISSING = "weisssrv-no-such-binary"
GATE = "fixture gate"


def test_a_present_tool_returns_quietly():
    require_tool("python3", GATE)


def test_a_missing_tool_fails_loudly_under_ci(monkeypatch):
    monkeypatch.setenv("CI", "1")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        require_tool(MISSING, GATE, "Install it in the job image.")
    assert MISSING in str(excinfo.value)
    assert "Install it in the job image." in str(excinfo.value)


def test_a_missing_tool_only_skips_off_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_tool(MISSING, GATE)


def test_the_suite_sees_the_ci_variable(monkeypatch):
    """Scrubbing $CI from the test environment would turn every require_tool
    call back into a silent skip."""
    import os

    monkeypatch.setenv("CI", "true")
    assert os.environ.get("CI") == "true"
