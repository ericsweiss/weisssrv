"""Tests for sanitize-junit-expected-failures.py.

The sanitizer's contract: downgrade ONLY declared negative-path failures in
junit XML (replacing the failure element with a system-out note and fixing the
suite counters), leave undeclared failures red, no-op without declarations,
and refuse DTD-bearing XML (entity-attack guard).
"""
from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "sanitize_junit", Path(__file__).parent / "sanitize-junit-expected-failures.py"
)
sanitize_junit = importlib.util.module_from_spec(_SPEC)
sys.modules["sanitize_junit"] = sanitize_junit
_SPEC.loader.exec_module(sanitize_junit)


JUNIT = """<?xml version='1.0' encoding='utf-8'?>
<testsuites failures="2" errors="0" tests="3">
  <testsuite name="molecule" failures="2" errors="0" tests="3">
    <testcase name="[host] Converge: role : Fail if required storage pool does not exist">
      <failure message="boom">guard fired as designed</failure>
    </testcase>
    <testcase name="[host] Converge: role : A genuinely broken task">
      <failure message="real">unexpected</failure>
    </testcase>
    <testcase name="[host] Converge: role : A passing task"/>
  </testsuite>
</testsuites>
"""


def _write(tmp_path: Path, content: str = JUNIT) -> Path:
    p = tmp_path / "junit" / "run.xml"
    p.parent.mkdir()
    p.write_text(content)
    return p


def _expectations(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "expected-junit-failures.txt"
    p.write_text("# comment\n\n" + "\n".join(lines) + "\n")
    return p


class TestSanitize:
    def test_downgrades_declared_failure_only(self, tmp_path):
        xml = _write(tmp_path)
        downgraded, undeclared = sanitize_junit.sanitize_file(
            xml, ["Fail if required storage pool does not exist"]
        )
        assert downgraded == 1
        assert undeclared == ["[host] Converge: role : A genuinely broken task"]
        root = ET.parse(xml).getroot()
        cases = {tc.get("name"): tc for tc in root.iter("testcase")}
        declared = cases["[host] Converge: role : Fail if required storage pool does not exist"]
        assert declared.find("failure") is None
        assert "expected negative-path failure" in declared.find("system-out").text
        broken = cases["[host] Converge: role : A genuinely broken task"]
        assert broken.find("failure") is not None

    def test_suite_counters_updated(self, tmp_path):
        xml = _write(tmp_path)
        sanitize_junit.sanitize_file(xml, ["Fail if required storage pool"])
        root = ET.parse(xml).getroot()
        assert root.find("testsuite").get("failures") == "1"
        # Aggregate <testsuites> attributes must track too — some consumers
        # read the root counts in preference to per-suite ones.
        assert root.get("failures") == "1"

    def test_no_expectations_is_noop(self, tmp_path):
        xml = _write(tmp_path)
        before = xml.read_text()
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(tmp_path / "absent.txt")]
        )
        assert rc == 0
        assert xml.read_text() == before

    def test_main_downgrades_via_declaration_file(self, tmp_path, capsys):
        xml = _write(tmp_path)
        exp = _expectations(tmp_path, ["Fail if required storage pool does not exist"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "downgraded 1" in out
        assert "undeclared junit failure" in out  # the genuinely broken task warns

    def test_refuses_dtd_bearing_xml(self, tmp_path):
        evil = "<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e 'x'>]><testsuites/>"
        xml = _write(tmp_path, evil)
        with pytest.raises(ValueError, match="DTD/entity"):
            sanitize_junit.sanitize_file(xml, ["anything"])

    def test_load_expectations_skips_comments_and_blanks(self, tmp_path):
        exp = _expectations(tmp_path, ["one", "two"])
        assert sanitize_junit.load_expectations(exp) == ["one", "two"]
        assert sanitize_junit.load_expectations(tmp_path / "nope.txt") == []
