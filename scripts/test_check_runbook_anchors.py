"""The runbook-anchor gate fails on a missing file, a missing anchor, or no checks."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from script_loader import load_path

SCRIPT = Path(__file__).resolve().parent / "check-runbook-anchors.py"
REPO = SCRIPT.parent.parent

RULES = """\
    apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    spec:
      groups:
        - name: storage
          rules:
            - alert: NASSwapNotClearing
              annotations:
                runbook_url: '${cluster_runbook_base_url}/%s'
                summary: 'NAS swap not clearing'
"""

DOC = """\
    # ZFS Storage Configuration

    ### NAS memory management

    Check `journalctl -t swap-clean`.

    ```bash
    # ### Not a heading: inside a fence
    ```
"""


def _load():
    return load_path(SCRIPT)


@pytest.fixture(scope="module")
def gate():
    return _load()


def _corpus(tmp_path: Path, url_suffix: str) -> tuple[Path, Path]:
    rules_dir = tmp_path / "rules"
    docs_dir = tmp_path / "docs"
    rules_dir.mkdir()
    docs_dir.mkdir()
    (rules_dir / "storage.yaml").write_text(textwrap.dedent(RULES) % url_suffix)
    (docs_dir / "06-zfs.md").write_text(textwrap.dedent(DOC))
    return rules_dir, docs_dir


def _run(rules_dir: Path, docs_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--rules-dir", str(rules_dir),
         "--docs-dir", str(docs_dir)],
        capture_output=True, text=True, cwd=REPO,
    )


def test_a_resolving_anchor_passes(tmp_path):
    result = _run(*_corpus(tmp_path, "06-zfs.md#nas-memory-management"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 runbook_url" in result.stdout


def test_an_unanchored_url_to_a_real_file_passes(tmp_path):
    result = _run(*_corpus(tmp_path, "06-zfs.md"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_missing_anchor_fails(tmp_path):
    result = _run(*_corpus(tmp_path, "06-zfs.md#nas-memory-mangement"))
    assert result.returncode == 1
    assert "no section anchored #nas-memory-mangement" in result.stdout


def test_a_missing_file_fails(tmp_path):
    result = _run(*_corpus(tmp_path, "99-nonexistent.md#anything"))
    assert result.returncode == 1
    assert "docs/99-nonexistent.md does not exist" in result.stdout


def test_a_url_outside_the_substitution_base_fails(tmp_path):
    rules_dir, docs_dir = _corpus(tmp_path, "06-zfs.md")
    (rules_dir / "storage.yaml").write_text(
        (rules_dir / "storage.yaml").read_text().replace(
            "${cluster_runbook_base_url}/06-zfs.md", "../docs/06-zfs.md"
        )
    )
    result = _run(rules_dir, docs_dir)
    assert result.returncode == 1
    assert "must start with" in result.stdout


def test_an_external_runbook_url_is_skipped(tmp_path):
    rules_dir, docs_dir = _corpus(tmp_path, "06-zfs.md")
    (rules_dir / "storage.yaml").write_text(
        (rules_dir / "storage.yaml").read_text().replace(
            "${cluster_runbook_base_url}/06-zfs.md",
            "https://runbooks.prometheus-operator.dev/runbooks/kubernetes/kubecpuovercommit/",
        )
    )
    result = _run(rules_dir, docs_dir)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_loki_ruler_rule_in_a_subdirectory_is_checked(tmp_path):
    """The gate's subject is the whole observability tree, not just rules/."""
    rules_dir, docs_dir = _corpus(tmp_path, "06-zfs.md#nas-memory-management")
    loki = rules_dir / "loki"
    loki.mkdir()
    (loki / "unifi-syslog.yaml").write_text(
        textwrap.dedent(RULES) % "06-zfs.md#nas-memory-mangement"
    )
    result = _run(rules_dir, docs_dir)
    assert result.returncode == 1
    assert "no section anchored #nas-memory-mangement" in result.stdout


def test_a_yml_suffixed_rule_file_is_checked(tmp_path):
    """Both YAML suffixes are scanned; a .yml rule file is not exempt."""
    rules_dir, docs_dir = _corpus(tmp_path, "06-zfs.md#nas-memory-management")
    (rules_dir / "mail.yml").write_text(
        textwrap.dedent(RULES) % "06-zfs.md#nas-memory-mangement"
    )
    result = _run(rules_dir, docs_dir)
    assert result.returncode == 1
    assert "no section anchored #nas-memory-mangement" in result.stdout


def test_an_underscore_heading_resolves(tmp_path):
    """The upstream slugger keeps `_`, so such an anchor is not dangling."""
    rules_dir, docs_dir = _corpus(tmp_path, "06-zfs.md#tuning-usgsyn_cookies")
    (docs_dir / "06-zfs.md").write_text("# ZFS\n\n### Tuning usg.syn_cookies\n")
    result = _run(rules_dir, docs_dir)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_empty_rules_dir_fails_rather_than_passing_vacuously(tmp_path):
    rules_dir = tmp_path / "empty-rules"
    rules_dir.mkdir()
    result = _run(rules_dir, tmp_path)
    assert result.returncode == 1
    assert "no runbook_url annotations" in result.stdout


def test_a_fenced_heading_is_not_an_anchor(gate, tmp_path):
    doc = tmp_path / "06-zfs.md"
    doc.write_text(textwrap.dedent(DOC))
    found = gate.anchors(doc)
    assert "nas-memory-management" in found
    assert "not-a-heading-inside-a-fence" not in found


def test_repeated_headings_get_github_suffixes(gate, tmp_path):
    doc = tmp_path / "dup.md"
    doc.write_text("## Recovery\n\n## Recovery\n")
    assert gate.anchors(doc) == {"recovery", "recovery-1"}


def test_slug_drops_inline_markup(gate):
    assert gate.slug("Kernel `192-byte` **slab** leak") == "kernel-192-byte-slab-leak"
    assert gate.slug("[docs/12](12-runbooks.md) entry") == "docs12-entry"


def test_slug_keeps_underscores(gate):
    """GitHub and GitLab keep `_`, so dropping it would report a live link dangling."""
    assert gate.slug("Role: `nas_storage`") == "role-nas_storage"


def test_the_repo_rules_resolve():
    """The gate against the real tree, so a doc rename shows up here."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=REPO
    )
    assert result.returncode == 0, result.stdout + result.stderr
