#!/usr/bin/env python3
"""
Unit tests for version-check-ci.py — the MR-pipeline wrapper around
check-versions.py.

version-check-ci.py runs in every MR pipeline but had zero coverage. These
tests pin the contract CI depends on:

  - exit code semantics: 0 up-to-date / 1 updates available / 2 errors
    (reconciled from the parsed --json summary)
  - the parse-failure branch writes a self-describing error stub artifact
    (not a 0-byte / raw-text file that reads like a successful empty report)
  - the MR comment includes BOTH an Updates and an Errors section when both
    are present (a transient single-service error must not suppress the
    actionable update table, or vice versa)
  - held updates are excluded from the update table via the
    `not svc.get('held')` guard (MetalLB hold)

Run with pytest (preferred):
    pytest scripts/test_version_check_ci.py -v

unittest fallback is provided for environments without pytest.
"""

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import pytest
    PYTEST_AVAILABLE = True
except ImportError:
    PYTEST_AVAILABLE = False

# Import the hyphen-named module via importlib (same pattern as
# test_check_versions.py).
_script_path = Path(__file__).parent / "version-check-ci.py"
_spec = importlib.util.spec_from_file_location("version_check_ci", _script_path)
version_check_ci = importlib.util.module_from_spec(_spec)
sys.modules["version_check_ci"] = version_check_ci
_spec.loader.exec_module(version_check_ci)


def _completed(stdout: str, stderr: str = "", returncode: int = 0):
    """Build a fake subprocess.CompletedProcess-shaped object."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


class _FakeOpen:
    """Captures all open(..., 'w') writes by filename so tests can assert on
    the version-report.json artifact without touching the real filesystem."""

    def __init__(self):
        self.files: dict[str, io.StringIO] = {}

    def __call__(self, name, mode="r", *args, **kwargs):
        buf = io.StringIO()
        # Don't let StringIO.close() discard the buffer before we read it.
        buf.close = lambda: None  # type: ignore[method-assign]
        self.files[name] = buf
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=buf)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def contents(self, name: str) -> str:
        return self.files[name].getvalue()


def _run_main(stdout, *, env, stderr="", returncode=0):
    """Run version_check_ci.main() with subprocess/open/post mocked.

    Returns (exit_code, fake_open, posted_bodies).
    """
    fake_open = _FakeOpen()
    posted: list[str] = []

    with patch.object(version_check_ci.subprocess, "run",
                      return_value=_completed(stdout, stderr, returncode)), \
         patch("builtins.open", fake_open), \
         patch.object(version_check_ci, "post_mr_comment",
                      side_effect=lambda body: posted.append(body)), \
         patch.dict(version_check_ci.os.environ, env, clear=True):
        try:
            version_check_ci.main()
            code = 0
        except SystemExit as e:
            code = e.code if e.code is not None else 0

    return code, fake_open, posted


def _payload(services, summary_overrides=None):
    """Build a check-versions.py --json payload."""
    summary = {
        "total": len(services),
        "up_to_date": sum(
            1 for s in services if not s.get("update_available") and not s.get("error")
        ),
        "updates_available": sum(
            1 for s in services if s.get("update_available") and not s.get("held")
        ),
        "updates_held": sum(
            1 for s in services if s.get("update_available") and s.get("held")
        ),
        "errors": sum(1 for s in services if s.get("error")),
    }
    if summary_overrides:
        summary.update(summary_overrides)
    return json.dumps({"summary": summary, "services": services})


class TestExitCodes(unittest.TestCase):
    """Exit code reconciliation from the parsed --json summary."""

    def test_all_up_to_date_exits_zero(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
             "update_available": False},
        ])
        # check-versions.py would have returned 0 too; main() reconciles.
        code, _, _ = _run_main(payload, env={}, returncode=0)
        self.assertEqual(code, 0)

    def test_updates_available_exits_one(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=1)
        self.assertEqual(code, 1)

    def test_errors_exit_two(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": None,
             "update_available": False, "error": "boom"},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=2)
        self.assertEqual(code, 2)

    def test_errors_take_precedence_over_updates(self):
        """When both updates and errors are present, errors win → exit 2."""
        payload = _payload([
            {"name": "Up", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
            {"name": "Bad", "current_version": "2.0", "latest_version": None,
             "update_available": False, "error": "boom"},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=1)
        self.assertEqual(code, 2)


class TestParseFailureStub(unittest.TestCase):
    """A non-JSON check-versions.py stdout writes a self-describing stub
    artifact and exits 2 (not a silent empty/raw-text report)."""

    def test_unparseable_output_writes_stub_and_exits_two(self):
        code, fake_open, _ = _run_main(
            "this is not json", env={}, stderr="traceback here", returncode=0
        )
        self.assertEqual(code, 2)
        artifact = json.loads(fake_open.contents("version-report.json"))
        # Self-describing: carries the parse error plus the raw streams.
        self.assertIn("error", artifact)
        self.assertIn("not parseable", artifact["error"])
        self.assertEqual(artifact["stdout"], "this is not json")
        self.assertEqual(artifact["stderr"], "traceback here")

    def test_valid_output_persists_raw_json_artifact(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
             "update_available": False},
        ])
        _, fake_open, _ = _run_main(payload, env={}, returncode=0)
        # The artifact is the raw validated stdout, round-trips to the payload.
        self.assertEqual(
            json.loads(fake_open.contents("version-report.json")),
            json.loads(payload),
        )


class TestMrComment(unittest.TestCase):
    """MR comment composition (only inside an MR pipeline)."""

    MR_ENV = {"CI_MERGE_REQUEST_IID": "42"}

    def test_no_comment_outside_mr_pipeline(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
        ])
        _, _, posted = _run_main(payload, env={}, returncode=1)
        self.assertEqual(posted, [], "must not post a comment outside an MR")

    def test_comment_has_both_updates_and_errors_sections(self):
        payload = _payload([
            {"name": "Up", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True, "notes": "minor bump"},
            {"name": "Bad", "current_version": "2.0", "latest_version": None,
             "update_available": False, "error": "connection refused"},
        ])
        _, _, posted = _run_main(payload, env=self.MR_ENV, returncode=1)
        self.assertEqual(len(posted), 1)
        body = posted[0]
        self.assertIn("### Updates available", body)
        self.assertIn("### Errors", body)
        # The update row and the error row both appear.
        self.assertIn("Up", body)
        self.assertIn("Bad", body)
        self.assertIn("connection refused", body)

    def test_held_updates_excluded_from_update_table(self):
        """A held service with update_available=True must not appear in the
        update table (the `not svc.get('held')` guard)."""
        payload = _payload([
            {"name": "MetalLB", "current_version": "0.15.3",
             "latest_version": "0.16.0", "update_available": True,
             "held": True, "notes": "intentionally held back"},
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
        ])
        # summary.updates_available counts only the non-held Foo → exit 1.
        code, _, posted = _run_main(payload, env=self.MR_ENV, returncode=1)
        self.assertEqual(code, 1)
        self.assertEqual(len(posted), 1)
        body = posted[0]
        self.assertIn("### Updates available", body)
        self.assertIn("Foo", body)
        # MetalLB is held → excluded from the actionable update table.
        self.assertNotIn("MetalLB", body)

    def test_all_held_no_actionable_updates_no_comment(self):
        """If the only update is held, there are no actionable updates and no
        errors → exit 0 and no MR comment."""
        payload = _payload([
            {"name": "MetalLB", "current_version": "0.15.3",
             "latest_version": "0.16.0", "update_available": True,
             "held": True, "notes": "held"},
        ])
        code, _, posted = _run_main(payload, env=self.MR_ENV, returncode=0)
        self.assertEqual(code, 0)
        self.assertEqual(posted, [], "held-only run posts nothing")

    def test_parse_failure_posts_failure_section_in_mr(self):
        """In an MR pipeline, an unparseable output posts a 'failed' section
        (no structured services to itemize)."""
        _, _, posted = _run_main(
            "garbage", env=self.MR_ENV, stderr="boom", returncode=0
        )
        self.assertEqual(len(posted), 1)
        self.assertIn("### Version check failed", posted[0])
        self.assertIn("boom", posted[0])


class TestSubprocessFailureModes(unittest.TestCase):
    """Timeout / exec failure of the underlying check-versions.py call."""

    def test_timeout_exits_two(self):
        import subprocess
        with patch.object(version_check_ci.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="x", timeout=300)), \
             patch.dict(version_check_ci.os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                version_check_ci.main()
        self.assertEqual(cm.exception.code, 2)

    def test_oserror_exits_two(self):
        with patch.object(version_check_ci.subprocess, "run",
                          side_effect=OSError("no such file")), \
             patch.dict(version_check_ci.os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                version_check_ci.main()
        self.assertEqual(cm.exception.code, 2)


class TestPostMrComment(unittest.TestCase):
    """post_mr_comment guards on missing credentials and posts otherwise."""

    def test_skips_when_credentials_incomplete(self):
        # No env at all → returns without attempting a request.
        with patch.object(version_check_ci, "urlopen") as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, {}, clear=True):
            version_check_ci.post_mr_comment("hi")
        mock_urlopen.assert_not_called()

    def test_posts_when_all_credentials_present(self):
        env = {
            "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
            "CI_PROJECT_ID": "1",
            "CI_MERGE_REQUEST_IID": "42",
            "GITLAB_API_TOKEN": "tok",
        }
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = b""
        with patch.object(version_check_ci, "urlopen", return_value=resp) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, env, clear=True):
            version_check_ci.post_mr_comment("hello")
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        # The note is posted to the MR notes endpoint with the token header.
        self.assertIn("/merge_requests/42/notes", req.full_url)
        self.assertEqual(req.headers.get("Private-token"), "tok")


if __name__ == "__main__":
    if PYTEST_AVAILABLE:
        pytest.main([__file__, "-v"])
    else:
        unittest.main(verbosity=2)
