#!/usr/bin/env python3
"""Report 1Password items older than the rotation schedule docs/15 declares.

Read-only: titles and `updated_at` through `op item list`, never a field value.
The coverage gate asks whether a credential has a rotation path, this one when.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEDULE_DOC = "docs/15-credential-rotation.md"
SCHEDULE_SECTION = "Scheduled Rotation Policy"
# The vault every consumer in docs/15 reads. The boot-unlock and admin vaults
# are separate grants, so they are opt-in with a second --vault.
DEFAULT_VAULTS = ("Homelab",)

EXEMPT = "exempt"
_AGE_RE = re.compile(r"^(\d+)d$")
_BACKTICKED = re.compile(r"`([^`\n]+)`")


class Vacuous(Exception):
    """The report could not inspect its subject — exit 2, never a silent pass."""


class Row:
    """One schedule row: the title patterns it claims and the age it allows."""

    def __init__(self, patterns: list[str], max_days: int | None, order: int):
        self.patterns = patterns
        self.max_days = max_days
        self.order = order

    def match(self, title: str) -> str | None:
        """The most specific of this row's patterns that covers `title`."""
        hits = [p for p in self.patterns if fnmatch.fnmatch(title.lower(), p.lower())]
        return max(hits, key=_specificity) if hits else None


def _specificity(pattern: str) -> tuple[int, int]:
    """Rank a pattern: a literal outranks a glob, then the longer literal wins."""
    literal = len(pattern.replace("*", "").replace("?", ""))
    return (0 if set("*?") & set(pattern) else 1, literal)


def _section(text: str, heading: str) -> str:
    """The `## heading` section, up to the next `## ` heading."""
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        raise Vacuous(f"{SCHEDULE_DOC} has no `## {heading}` section")
    rest = text[match.end() :]
    nxt = re.search(r"^##\s+", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def parse_schedule(doc: Path) -> list[Row]:
    """The schedule table as rows, in document order."""
    try:
        text = doc.read_text(encoding="utf-8")
    except OSError as exc:
        raise Vacuous(f"{doc}: unreadable ({exc.__class__.__name__}: {exc})") from exc
    rows: list[Row] = []
    # Rows are taken only after the `|---|` separator, so the header line is
    # skipped structurally rather than by guessing at its text.
    in_table = False
    for line in _section(text, SCHEDULE_SECTION).splitlines():
        if not line.startswith("|"):
            in_table = in_table and not line.strip()
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and all(c and set(c) <= set("-: ") for c in cells):
            in_table = True
            continue
        patterns = _BACKTICKED.findall(cells[0]) if in_table else []
        if not patterns:
            continue
        rows.append(Row(patterns, _max_days(cells[1], patterns), len(rows)))
    if not rows:
        raise Vacuous(
            f"{SCHEDULE_DOC} § {SCHEDULE_SECTION} holds no parseable row — "
            "each needs backticked item patterns and a `<N>d` or `exempt` age"
        )
    return rows


def _max_days(cell: str, patterns: list[str]) -> int | None:
    if cell.lower() == EXEMPT:
        return None
    age = _AGE_RE.match(cell)
    if not age:
        raise Vacuous(
            f"{SCHEDULE_DOC} § {SCHEDULE_SECTION}: row {patterns} has max age "
            f"{cell!r}, which is neither `<N>d` nor `{EXEMPT}`"
        )
    return int(age.group(1))


def claim(rows: list[Row], title: str) -> Row | None:
    """The row owning `title`: most specific pattern, earliest row on a tie."""
    hits = [(_specificity(p), -r.order, r) for r, p in ((r, r.match(title)) for r in rows) if p]
    return max(hits, key=lambda h: h[:2])[2] if hits else None


def load_items(source: str | None, vaults: list[str]) -> list[dict]:
    """Items as `{title, updated_at}` dicts, from a JSON capture or the CLI."""
    if source is not None:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
        payload = _decode(raw, source)
    else:
        payload = []
        for vault in vaults:
            payload.extend(_op_item_list(vault))
    if not isinstance(payload, list) or not payload:
        raise Vacuous("the item list is empty — nothing was inspected")
    return payload


def _decode(raw: str, label: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Vacuous(f"{label}: not JSON ({exc})") from exc


def _op_item_list(vault: str):
    if not shutil.which("op"):
        raise Vacuous("1Password CLI (`op`) not on PATH")
    cmd = ["op", "item", "list", "--vault", vault, "--format", "json"]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:]
        raise Vacuous(f"`op item list --vault {vault}` failed: {' '.join(tail)}")
    return _decode(done.stdout, f"op item list --vault {vault}")


def age_days(item: dict, now: dt.datetime) -> int:
    stamp = item.get("updated_at") or item.get("created_at")
    title = item.get("title", "<untitled>")
    if not isinstance(stamp, str):
        raise Vacuous(f"{title!r} carries no updated_at — age is unknowable")
    try:
        when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Vacuous(f"{title!r}: updated_at {stamp!r} is not ISO 8601 ({exc})") from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (now - when).days


def report(items: list[dict], rows: list[Row], now: dt.datetime) -> tuple[list[str], list[str]]:
    """({overdue lines}, {unclaimed titles}) — the two kinds of finding."""
    overdue: list[tuple[int, str]] = []
    unclaimed: list[str] = []
    exempt = ok = 0
    for item in sorted(items, key=lambda i: str(i.get("title", ""))):
        title = str(item.get("title", "<untitled>"))
        days = age_days(item, now)
        row = claim(rows, title)
        if row is None:
            unclaimed.append(title)
        elif row.max_days is None:
            exempt += 1
        elif days > row.max_days:
            overdue.append((days - row.max_days, f"{title}: {days}d old, max {row.max_days}d"))
        else:
            ok += 1
    print(f"{ok} within policy, {len(overdue)} overdue, {exempt} exempt, {len(unclaimed)} unscheduled")
    return [line for _, line in sorted(overdue, reverse=True)], unclaimed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--schedule", default=SCHEDULE_DOC, help="path under --repo-root")
    parser.add_argument("--items-json", help="a captured `op item list --format json`; - for stdin")
    parser.add_argument("--vault", action="append", help=f"repeatable; default {list(DEFAULT_VAULTS)}")
    parser.add_argument("--now", help="ISO 8601 instant to age against (tests)")
    args = parser.parse_args(argv)

    try:
        rows = parse_schedule(Path(args.repo_root) / args.schedule)
        now = (
            dt.datetime.fromisoformat(args.now.replace("Z", "+00:00"))
            if args.now
            else dt.datetime.now(dt.timezone.utc)
        )
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        items = load_items(args.items_json, args.vault or list(DEFAULT_VAULTS))
        overdue, unclaimed = report(items, rows, now)
    except Vacuous as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for line in overdue:
        print(f"  OVERDUE  {line}")
    for title in unclaimed:
        print(f"  NO POLICY  {title}: no row in {SCHEDULE_DOC} § {SCHEDULE_SECTION} covers it")
    if overdue or unclaimed:
        print(f"\nRotate per {SCHEDULE_DOC}, or amend § {SCHEDULE_SECTION} if the policy moved.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
