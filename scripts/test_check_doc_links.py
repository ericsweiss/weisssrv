"""Tests for scripts/check-doc-links.py (the offline Markdown link gate).

Exercises the relative-`.md`-link resolution and the URL/anchor/non-md
exclusions, plus a smoke check that the real repo docs currently pass.

Run via `pytest scripts/` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "check-doc-links.py"

# Import the hyphenated-name module the same way test_check_versions.py does.
_spec = importlib.util.spec_from_file_location("check_doc_links", _SCRIPT)
assert _spec and _spec.loader
cdl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdl)


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestBrokenLinks:
    def test_resolving_link_passes(self, tmp_path: Path):
        _write(tmp_path / "docs" / "01-a.md", "see [b](02-b.md)\n")
        _write(tmp_path / "docs" / "02-b.md", "# B\n")
        assert cdl.broken_links([tmp_path / "docs" / "01-a.md"]) == []

    def test_missing_target_is_broken(self, tmp_path: Path):
        src = _write(tmp_path / "docs" / "01-a.md", "see [gone](99-missing.md)\n")
        broken = cdl.broken_links([src])
        assert len(broken) == 1
        assert broken[0][0] == src
        assert "99-missing.md" in broken[0][1]

    def test_parent_relative_link_resolves(self, tmp_path: Path):
        _write(tmp_path / "README.md", "top\n")
        # A link from docs/ up to the repo root README resolves correctly.
        src = _write(tmp_path / "docs" / "01-a.md", "[root](../README.md)\n")
        assert cdl.broken_links([src]) == []

    def test_anchor_is_stripped_before_resolving(self, tmp_path: Path):
        _write(tmp_path / "docs" / "02-b.md", "# B\n")
        src = _write(tmp_path / "docs" / "01-a.md", "[b](02-b.md#section)\n")
        assert cdl.broken_links([src]) == []

    def test_title_after_target_is_stripped(self, tmp_path: Path):
        _write(tmp_path / "docs" / "02-b.md", "# B\n")
        src = _write(tmp_path / "docs" / "01-a.md", '[b](02-b.md "The B doc")\n')
        assert cdl.broken_links([src]) == []


class TestExclusions:
    def test_urls_ignored(self, tmp_path: Path):
        src = _write(
            tmp_path / "docs" / "01-a.md",
            "[x](https://example.com/nope.md) [y](mailto:a@b.md)\n",
        )
        assert cdl.broken_links([src]) == []

    def test_anchor_only_ignored(self, tmp_path: Path):
        src = _write(tmp_path / "docs" / "01-a.md", "[jump](#heading)\n")
        assert cdl.broken_links([src]) == []

    def test_non_md_target_ignored(self, tmp_path: Path):
        # A dangling non-.md link (image / dir) is out of scope, not a failure.
        src = _write(tmp_path / "docs" / "01-a.md", "[img](diagram.png) [d](../scripts/)\n")
        assert cdl.broken_links([src]) == []


class TestDocFiles:
    def test_collects_docs_and_top_level_readmes(self, tmp_path: Path):
        _write(tmp_path / "docs" / "01-a.md", "a\n")
        _write(tmp_path / "docs" / "sub" / "02-b.md", "b\n")
        _write(tmp_path / "README.md", "r\n")
        _write(tmp_path / "CLAUDE.md", "c\n")
        names = {p.name for p in cdl.doc_files(tmp_path)}
        assert {"01-a.md", "02-b.md", "README.md", "CLAUDE.md"} <= names


class TestRealRepo:
    def test_repo_docs_have_no_broken_links(self):
        # The gate must be green on the tree it ships with (preventive check).
        files = cdl.doc_files(REPO)
        assert files, "expected repo docs to be discovered"
        broken = cdl.broken_links(files)
        assert broken == [], f"broken links in repo docs: {broken}"
