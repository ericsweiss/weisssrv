#!/usr/bin/env python3
"""Offline checker for the docs pointers an alert carries.

Resolves each `runbook_url` under the observability tree against the docs tree,
and each `(docs/NN § Heading)` pointer in alert text. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES_DIR = REPO / "kubernetes/infrastructure/observability"
DOCS_DIR = REPO / "docs"

# The Flux substitution variable an in-repo runbook_url is written against.
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


# A `(docs/NN § Heading)` pointer in an alert's description or summary. The
# parentheses bound the section name, so the prose around it is never read as
# part of the heading.
_SECTION_RE = re.compile(r"\((?P<doc>[A-Za-z0-9_./-]+)[^\S\n]*§[^\S\n]*(?P<heading>[^)]+)\)")
_ALERT_RE = re.compile(r"^[^\S\n]*-?[^\S\n]*alert:[^\S\n]*['\"]?(?P<name>[A-Za-z0-9_]+)", re.M)
_MARKUP = str.maketrans("", "", "`*_")
_TRAILING = ".,;:!?\"'()[]-\u2014\u2013"
# Words of overlap that identify a section when neither side is a clean prefix
# of the other: a line wrap cuts the pointer short mid-heading.
_MIN_SHARED_WORDS = 3


def _words(text: str) -> list[str]:
    """Heading or pointer text reduced to comparable words."""
    stripped = _LINK_RE.sub(r"\1", text).translate(_MARKUP)
    return [w for w in (p.strip(_TRAILING).casefold() for p in stripped.split()) if w]


def headings(doc: Path) -> list[list[str]]:
    """Every heading a Markdown file offers, as word lists, fences skipped."""
    found: list[list[str]] = []
    in_fence = False
    for line in doc.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            words = _words(match.group("text"))
            if words:
                found.append(words)
    return found


def _same_section(cited: list[str], heading: list[str]) -> bool:
    """One word list is a prefix of the other, or they agree on enough words."""
    if not cited:
        return False
    if cited[: len(heading)] == heading or heading[: len(cited)] == cited:
        return True
    shared = 0
    for mine, theirs in zip(cited, heading):
        if mine != theirs:
            break
        shared += 1
    return shared >= _MIN_SHARED_WORDS


def _cited_doc(token: str, docs_dir: Path) -> Path | None:
    """The document a pointer names, or None when it cannot be pinned down.

    A bare `docs/06` resolves through the numbered-prefix convention; an
    ambiguous or absent target is left alone.
    """
    rel = token
    prefix = docs_dir.name + "/"
    if rel.startswith(prefix):
        rel = rel[len(prefix):]
    if not rel:
        return None
    if rel.endswith(".md"):
        doc = docs_dir / rel
        return doc if doc.is_file() else None
    matches = sorted(docs_dir.glob(rel + "*.md"))
    return matches[0] if len(matches) == 1 else None


def dangling_sections(rules_dir: Path, docs_dir: Path) -> list[str]:
    """One message per `(docs/NN § Heading)` pointer whose heading is gone."""
    problems: list[str] = []
    cache: dict[Path, list[list[str]]] = {}
    paths = sorted(set(rules_dir.rglob("*.yaml")) | set(rules_dir.rglob("*.yml")))
    for rules in paths:
        text = rules.read_text(encoding="utf-8", errors="replace")
        alerts = [(m.start(), m.group("name")) for m in _ALERT_RE.finditer(text)]
        for match in _SECTION_RE.finditer(text):
            doc = _cited_doc(match.group("doc"), docs_dir)
            if doc is None:
                continue
            if doc not in cache:
                cache[doc] = headings(doc)
            cited = _words(match.group("heading"))
            if any(_same_section(cited, h) for h in cache[doc]):
                continue
            owner = next(
                (name for start, name in reversed(alerts) if start < match.start()),
                "(no alert)",
            )
            line = text.count("\n", 0, match.start()) + 1
            near = difflib.get_close_matches(
                " ".join(cited), [" ".join(h) for h in cache[doc]], n=3, cutoff=0.4
            )
            problems.append(
                "%s:%d: %s — %s has no section named %r%s"
                % (
                    rules.relative_to(rules_dir), line, owner,
                    doc.name, " ".join(match.group("heading").split()),
                    " (nearest: %s)" % ", ".join(near) if near else "",
                )
            )
    return problems


def runbook_urls(rules_dir: Path) -> list[tuple[Path, int, str]]:
    """(file, line number, url) for every runbook_url annotation under rules_dir.

    The whole tree, not a `rules/` subdirectory: a Loki ruler's rule files sit
    beside its chart values and carry the same annotation.
    """
    found: list[tuple[Path, int, str]] = []
    paths = sorted(set(rules_dir.rglob("*.yaml")) | set(rules_dir.rglob("*.yml")))
    for rules in paths:
        for number, line in enumerate(
            rules.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            match = _RUNBOOK_RE.match(line)
            if match:
                found.append((rules, number, match.group("url")))
    return found


def dangling(rules_dir: Path, docs_dir: Path, placeholder: str = BASE_PLACEHOLDER) -> list[str]:
    """One message per runbook_url that does not resolve to a real section."""
    problems: list[str] = []
    cache: dict[Path, set[str]] = {}
    label = docs_dir.name or str(docs_dir)
    for rules, number, url in runbook_urls(rules_dir):
        where = f"{rules.relative_to(rules_dir)}:{number}"
        if url.startswith(_EXTERNAL_PREFIXES):
            continue
        if not url.startswith(placeholder):
            problems.append(
                f"{where}: {url} — an in-repo runbook_url must start with {placeholder}"
            )
            continue
        target, _, anchor = url[len(placeholder):].partition("#")
        doc = docs_dir / target
        if not doc.is_file():
            problems.append(f"{where}: {url} — {label}/{target} does not exist")
            continue
        if not anchor:
            continue
        if doc not in cache:
            cache[doc] = anchors(doc)
        if anchor not in cache[doc]:
            problems.append(
                f"{where}: {url} — {label}/{target} has no section anchored #{anchor}"
            )
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-dir", type=Path, default=RULES_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    parser.add_argument(
        "--base-placeholder",
        default=BASE_PLACEHOLDER,
        help="Substitution prefix every in-repo runbook_url must carry.",
    )
    args = parser.parse_args(argv[1:])

    if not args.rules_dir.is_dir():
        print(f"ERROR: rules directory not found: {args.rules_dir}", file=sys.stderr)
        return 2
    if not args.docs_dir.is_dir():
        print(f"ERROR: docs directory not found: {args.docs_dir}", file=sys.stderr)
        return 2
    urls = runbook_urls(args.rules_dir)
    if not urls:
        print(
            f"ERROR: no runbook_url annotations found under {args.rules_dir} — a gate "
            f"that checks nothing is not a gate; check --rules-dir.",
            file=sys.stderr,
        )
        return 2

    problems = dangling(args.rules_dir, args.docs_dir, args.base_placeholder)
    if problems:
        print(f"ERROR: {len(problems)} runbook_url annotation(s) do not resolve:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    pointers = dangling_sections(args.rules_dir, args.docs_dir)
    if pointers:
        print(f"ERROR: {len(pointers)} docs pointer(s) in alert text name a missing section:")
        for pointer in pointers:
            print(f"  {pointer}")
        return 1

    print(f"OK: {len(urls)} runbook_url annotation(s) resolve to a real section.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
