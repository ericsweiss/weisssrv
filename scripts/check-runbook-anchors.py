#!/usr/bin/env python3
"""Offline checker for alert `runbook_url` annotations.

Resolves every `runbook_url` under the observability tree against docs/ and
fails on a missing file or an anchor no heading slugs to.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES_DIR = REPO / "kubernetes/infrastructure/observability"
DOCS_DIR = REPO / "docs"

# The Flux substitution variable every in-repo runbook_url is written against.
BASE_PLACEHOLDER = "${cluster_runbook_base_url}/"

_RUNBOOK_RE = re.compile(r"""^\s*runbook_url:\s*['"]?(?P<url>[^'"\s]+)['"]?\s*$""")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EXTERNAL_PREFIXES = ("http://", "https://")


def slug(heading: str) -> str:
    """GitHub's heading slug: inline markup dropped, lowercased, spaces to
    hyphens, everything else but word characters and hyphens removed."""
    text = _LINK_RE.sub(r"\1", heading)
    text = text.replace("`", "").replace("*", "")
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text.strip())


def anchors(doc: Path) -> set[str]:
    """Every anchor a Markdown file offers, deduplicated the way GitHub is:
    a repeated slug gets a `-1`, `-2`, ... suffix."""
    seen: dict[str, int] = {}
    found: set[str] = set()
    in_fence = False
    for line in doc.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        base = slug(match.group("text"))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def runbook_urls(rules_dir: Path) -> list[tuple[Path, int, str]]:
    """(file, line number, url) for every runbook_url annotation under rules_dir."""
    found: list[tuple[Path, int, str]] = []
    for rules in sorted({*rules_dir.rglob("*.yaml"), *rules_dir.rglob("*.yml")}):
        for number, line in enumerate(
            rules.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            match = _RUNBOOK_RE.match(line)
            if match:
                found.append((rules, number, match.group("url")))
    return found


def dangling(rules_dir: Path, docs_dir: Path) -> list[str]:
    """One message per runbook_url that does not resolve to a real section."""
    problems: list[str] = []
    cache: dict[Path, set[str]] = {}
    for rules, number, url in runbook_urls(rules_dir):
        where = f"{rules.name}:{number}"
        if url.startswith(_EXTERNAL_PREFIXES):
            continue
        if not url.startswith(BASE_PLACEHOLDER):
            problems.append(
                f"{where}: {url} — an in-repo runbook_url must start with "
                f"{BASE_PLACEHOLDER}"
            )
            continue
        target, _, anchor = url[len(BASE_PLACEHOLDER):].partition("#")
        doc = docs_dir / target
        if not doc.is_file():
            problems.append(f"{where}: {url} — docs/{target} does not exist")
            continue
        if not anchor:
            continue
        if doc not in cache:
            cache[doc] = anchors(doc)
        if anchor not in cache[doc]:
            problems.append(
                f"{where}: {url} — docs/{target} has no section anchored #{anchor}"
            )
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-dir", type=Path, default=RULES_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    args = parser.parse_args(argv[1:])

    if not args.rules_dir.is_dir():
        print(f"ERROR: rules directory not found: {args.rules_dir}")
        return 1
    urls = runbook_urls(args.rules_dir)
    if not urls:
        print(f"ERROR: no runbook_url annotations found under {args.rules_dir}")
        return 1

    problems = dangling(args.rules_dir, args.docs_dir)
    if problems:
        print(f"ERROR: {len(problems)} runbook_url annotation(s) do not resolve:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"OK: {len(urls)} runbook_url annotation(s) resolve to a real section.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
