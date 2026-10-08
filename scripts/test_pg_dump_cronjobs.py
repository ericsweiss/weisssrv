"""Tests that the embedded pg-dump scripts report success only once a dump is in
place. The hindsight sidecar calls dump() from an `&&`/`||` list, which suspends
errexit for the whole body, so each publish step carries its own guard.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import assert_all_parsed, k8s_documents, reported

REPO = Path(__file__).resolve().parent.parent
APPS = REPO / "kubernetes" / "apps"
# One subject per app that dumps a PostgreSQL to the offsite-eligible export.
SUBJECTS = {
    "kubernetes/apps/authentik/pg-dump.yaml",
    "kubernetes/apps/hindsight/deployment.yaml",
    "kubernetes/apps/recipes/pg-dump.yaml",
}
# The unguarded shape: mv bare inside a function whose caller suspends
# errexit, so a failed publish still prints `wrote` and returns 0.
BUGGY_DUMP = """\
set -o pipefail
dump() {
  out=/backups/demo.sql.gz
  if pg_dump | gzip -c > "$out.tmp"; then
    mv "$out.tmp" "$out"
    echo "wrote $out"
  else
    rm -f "$out.tmp"; echo "pg-dump failed" >&2; return 1
  fi
}
dump || exit 7
"""


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _containers(doc: dict) -> list[dict]:
    spec = doc.get("spec") or {}
    pod = spec.get("template", {}).get("spec") or {}
    job = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec")
    return (pod.get("containers") or []) + ((job or {}).get("containers") or [])


def dump_scripts() -> dict[str, dict]:
    """{repo path: the pg-dump container}, plus the files that would not parse."""
    found: dict[str, dict] = {}
    docs, unreadable = k8s_documents(APPS)
    assert_all_parsed(unreadable, "pg-dump script")
    for path, doc in docs:
        for container in _containers(doc):
            if container.get("name") == "pg-dump":
                found[reported(path)] = container
    return found


def body_of(container: dict) -> str:
    args = container.get("args") or []
    assert len(args) == 1, f"expected one inline script, got {len(args)}"
    return args[0]


def dump_function(body: str) -> str:
    """The script down to the end of its dump() definition, so the scheduling
    driver's `while true` loop stays out of the harness."""
    lines = body.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "dump() {"]
    assert len(starts) == 1, "no single dump() definition to extract"
    indent = len(lines[starts[0]]) - len(lines[starts[0]].lstrip())
    for end in range(starts[0] + 1, len(lines)):
        if lines[end].strip() == "}" and len(lines[end]) - len(lines[end].lstrip()) == indent:
            return "\n".join(lines[: end + 1])
    raise AssertionError("dump() is never closed")


def relocate(script: str, root: Path) -> str:
    """The script with its absolute data paths moved under a temp root."""
    return script.replace("/pg0", f"{root}/pg0").replace("/backups", f"{root}/backups")


@pytest.fixture()
def harness(tmp_path: Path):
    """A temp /pg0 + /backups, a stub PATH, and a bash runner over the two."""
    (tmp_path / "backups").mkdir()
    instance = tmp_path / "pg0" / "instances" / "hindsight" / "instance.json"
    instance.parent.mkdir(parents=True)
    instance.write_text(json.dumps({"password": "stub"}))
    pg_bin = tmp_path / "pg0" / "installation" / "17" / "bin"
    pg_bin.mkdir(parents=True)
    _stub(pg_bin, "pg_dump", "printf 'SQL\\n'")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _stub(stubs, "pg_dump", "printf 'SQL\\n'")
    _stub(stubs, "pg_isready", "exit 0")

    def run(script: str, flags: str = "-c", **env) -> subprocess.CompletedProcess:
        full = dict(os.environ, RETENTION="2", PGDATABASE="demo")
        full.update({k: str(v) for k, v in env.items()})
        full["PATH"] = f"{stubs}:{full['PATH']}"
        return subprocess.run(
            ["bash", flags, relocate(script, tmp_path)],
            capture_output=True, text=True, env=full,
        )

    run.root = tmp_path
    run.stubs = stubs
    run.dumps = lambda: sorted(p.name for p in (tmp_path / "backups").glob("*.sql.gz"))
    return run


class TestSubjectsAreFound:
    def test_every_dump_script_is_read(self):
        assert set(dump_scripts()) == SUBJECTS, (
            "the pg-dump container set moved; this suite would certify scripts "
            "it never read"
        )

    def test_each_runs_bash_with_errexit_and_pipefail(self):
        for path, container in sorted(dump_scripts().items()):
            command = container.get("command") or []
            assert command[0] == "/bin/bash", f"{path} does not run bash"
            assert "e" in command[1], (
                f"{path} drops errexit from its bash flags, so an unguarded "
                "failure runs on to the next step"
            )
            assert "set -o pipefail" in body_of(container), (
                f"{path} sets no pipefail, so a failed pg_dump still gzips to 0"
            )


class TestHindsightSidecar:
    """Its dump() runs with errexit suspended, so only its own guards hold."""

    @staticmethod
    def _script(call: str = "dump || exit 7") -> str:
        container = dump_scripts()["kubernetes/apps/hindsight/deployment.yaml"]
        return f"{dump_function(body_of(container))}\n{call}\n"

    def test_a_dump_is_published_and_reported(self, harness):
        proc = harness(self._script())
        assert proc.returncode == 0, proc.stderr
        assert "wrote " in proc.stdout
        assert len(harness.dumps()) == 1

    def test_retention_keeps_the_newest(self, harness):
        for name in ("hindsight-20260101-000000", "hindsight-20260102-000000"):
            (harness.root / "backups" / f"{name}.sql.gz").write_text("old")
        proc = harness(self._script(), RETENTION=1)
        assert proc.returncode == 0, proc.stderr
        assert len(harness.dumps()) == 1

    def test_a_failed_publish_is_not_reported_as_success(self, harness):
        """mv fails, the dump never lands, and the caller has to see it."""
        _stub(harness.stubs, "mv", "exit 1")
        proc = harness(self._script())
        assert proc.returncode == 7, (
            f"dump() returned success with no dump published: {proc.stdout}"
        )
        assert "wrote " not in proc.stdout
        assert harness.dumps() == []

    def test_a_failed_prune_is_not_reported_as_success(self, harness):
        """A retention pipeline that cannot delete leaves the directory growing."""
        for name in ("hindsight-20260101-000000", "hindsight-20260102-000000"):
            (harness.root / "backups" / f"{name}.sql.gz").write_text("old")
        _stub(harness.stubs, "rm", "exit 1")
        proc = harness(self._script(), RETENTION=1)
        assert proc.returncode == 7, (
            f"dump() returned success with the prune failed: {proc.stdout}"
        )

    def test_the_harness_catches_the_unguarded_shape(self, harness):
        """The mutation: without the mv guard the harness must report a false
        success, or the three tests above prove nothing."""
        _stub(harness.stubs, "mv", "exit 1")
        proc = harness(BUGGY_DUMP)
        assert proc.returncode == 0
        assert "wrote " in proc.stdout
        assert harness.dumps() == []


class TestCronJobScripts:
    """The one-shot CronJobs keep errexit in force, so bash itself is the guard."""

    SCRIPTS = SUBJECTS - {"kubernetes/apps/hindsight/deployment.yaml"}

    def test_a_dump_is_published_and_reported(self, harness):
        for path in sorted(self.SCRIPTS):
            proc = harness(body_of(dump_scripts()[path]), flags="-ec")
            assert proc.returncode == 0, f"{path}: {proc.stderr}"
            assert "done; kept:" in proc.stdout, path
            assert len(harness.dumps()) == 1, path
            for leftover in (harness.root / "backups").glob("*.sql.gz"):
                leftover.unlink()

    def test_a_failed_publish_fails_the_job(self, harness):
        _stub(harness.stubs, "mv", "exit 1")
        for path in sorted(self.SCRIPTS):
            proc = harness(body_of(dump_scripts()[path]), flags="-ec")
            assert proc.returncode != 0, (
                f"{path} exited 0 with no dump published: {proc.stdout}"
            )
            assert "done; kept:" not in proc.stdout, path
            assert harness.dumps() == [], path

    def test_errexit_is_what_holds_them(self, harness):
        """Dropping -e makes the same script run past a failed mv to its success
        line, which is why the flag assertion above is part of the contract."""
        _stub(harness.stubs, "mv", "exit 1")
        for path in sorted(self.SCRIPTS):
            proc = harness(body_of(dump_scripts()[path]))
            assert "done; kept:" in proc.stdout, (
                f"{path} stopped at the failed mv without errexit, so the test "
                f"above does not prove errexit is what holds it: {proc.stderr}"
            )


def test_the_retention_count_is_set_for_every_script():
    """RETENTION is read unquoted in arithmetic; an unset one prunes everything."""
    for path, container in sorted(dump_scripts().items()):
        names = {entry["name"] for entry in container.get("env") or []}
        assert "RETENTION" in names, f"{path} reads RETENTION from no env entry"
