"""Cover scripts/check-credential-age.py: schedule parsing, matching, verdicts.

The real docs/15 table is parsed too, so a reshaped policy section fails here
rather than reporting "nothing was overdue" against the live vault.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-credential-age.py"
DOC = REPO / "docs" / "15-credential-rotation.md"

mod = load_script("check-credential-age.py")

NOW = "2026-10-09T00:00:00Z"

SCHEDULE = """
## Scheduled Rotation Policy

Prose the parser steps over, mentioning `*` in backticks.

| Item (1Password title, glob) | Max age | Reason |
|---|---|---|
| `*` | 180d | Default |
| `*SSH Key*` | 365d | High impact |
| `AdGuard Home` | 365d | Local only |
| `Tailscale Auth Key` | exempt | One-time use |

## Troubleshooting

| Item | Max age | Reason |
|---|---|---|
| `Decoy` | 1d | A table in another section |
"""


def _doc(tmp_path: Path, body: str = SCHEDULE) -> Path:
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "15-credential-rotation.md").write_text(body, encoding="utf-8")
    return tmp_path


def _items(*pairs: tuple[str, str]) -> list[dict]:
    return [{"title": t, "updated_at": u} for t, u in pairs]


def _run(root: Path, items: list[dict], now: str = NOW) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--repo-root", str(root),
            "--items-json", "-",
            "--now", now,
        ],
        input=json.dumps(items),
        capture_output=True,
        text=True,
        check=False,
    )


# --- schedule parsing -------------------------------------------------------


def test_the_header_row_is_not_read_as_a_policy_row(tmp_path):
    """Its first cell carries a backticked `*`, so a text-matched skip would
    take `Max age` as an age and fail the whole report."""
    rows = mod.parse_schedule(_doc(tmp_path) / "docs" / "15-credential-rotation.md")
    assert [r.max_days for r in rows] == [180, 365, 365, None]


def test_a_table_in_another_section_is_out_of_scope(tmp_path):
    rows = mod.parse_schedule(_doc(tmp_path) / "docs" / "15-credential-rotation.md")
    assert not any("Decoy" in p for r in rows for p in r.patterns)


def test_an_unparseable_age_is_vacuous(tmp_path):
    body = SCHEDULE.replace("| `*` | 180d |", "| `*` | 6 months |")
    path = _doc(tmp_path, body) / "docs" / "15-credential-rotation.md"
    with pytest.raises(mod.Vacuous, match="6 months"):
        mod.parse_schedule(path)


def test_a_section_without_the_table_is_vacuous(tmp_path):
    body = "## Scheduled Rotation Policy\n\nNothing but prose.\n"
    path = _doc(tmp_path, body) / "docs" / "15-credential-rotation.md"
    with pytest.raises(mod.Vacuous, match="no parseable row"):
        mod.parse_schedule(path)


def test_a_missing_section_is_vacuous(tmp_path):
    path = _doc(tmp_path, "# Nothing here\n") / "docs" / "15-credential-rotation.md"
    with pytest.raises(mod.Vacuous, match="Scheduled Rotation Policy"):
        mod.parse_schedule(path)


# --- pattern precedence -----------------------------------------------------


def test_a_literal_outranks_a_glob(tmp_path):
    rows = mod.parse_schedule(_doc(tmp_path) / "docs" / "15-credential-rotation.md")
    assert mod.claim(rows, "AdGuard Home").max_days == 365
    assert mod.claim(rows, "Tailscale Auth Key").max_days is None


def test_the_longer_glob_outranks_the_catch_all(tmp_path):
    rows = mod.parse_schedule(_doc(tmp_path) / "docs" / "15-credential-rotation.md")
    assert mod.claim(rows, "DNS-01 SSH Key").max_days == 365
    assert mod.claim(rows, "Cloudflare DNS Token").max_days == 180


def test_matching_ignores_case(tmp_path):
    rows = mod.parse_schedule(_doc(tmp_path) / "docs" / "15-credential-rotation.md")
    assert mod.claim(rows, "adguard home").max_days == 365


# --- ages -------------------------------------------------------------------


def test_a_naive_timestamp_is_read_as_utc():
    now = dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc)
    assert mod.age_days({"title": "x", "updated_at": "2026-10-04T00:00:00"}, now) == 5


def test_created_at_stands_in_for_a_never_edited_item():
    now = dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc)
    assert mod.age_days({"title": "x", "created_at": "2026-09-09T00:00:00Z"}, now) == 30


def test_an_item_with_no_timestamp_is_vacuous():
    now = dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc)
    with pytest.raises(mod.Vacuous, match="no updated_at"):
        mod.age_days({"title": "Mystery"}, now)


# --- exit contract ----------------------------------------------------------


def test_everything_inside_policy_is_exit_0(tmp_path):
    done = _run(
        _doc(tmp_path),
        _items(("GitLab API Token", "2026-09-01T00:00:00Z"),
               ("Tailscale Auth Key", "2019-01-01T00:00:00Z")),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 within policy" in done.stdout
    assert "1 exempt" in done.stdout


def test_an_overdue_item_is_exit_1_and_named(tmp_path):
    done = _run(_doc(tmp_path), _items(("Cloudflare DNS Token", "2026-01-01T00:00:00Z")))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "OVERDUE  Cloudflare DNS Token" in done.stdout
    assert "max 180d" in done.stdout


def test_the_most_overdue_item_is_listed_first(tmp_path):
    done = _run(
        _doc(tmp_path),
        _items(("Alpha Token", "2026-02-01T00:00:00Z"),
               ("Beta Token", "2024-02-01T00:00:00Z")),
    )
    overdue = [ln for ln in done.stdout.splitlines() if "OVERDUE" in ln]
    assert [ln.split("OVERDUE  ")[1].split(":")[0] for ln in overdue] == ["Beta Token", "Alpha Token"]


def test_an_item_no_row_covers_is_a_finding(tmp_path):
    """Without a catch-all row, an unmatched item is reported, not skipped."""
    body = SCHEDULE.replace("| `*` | 180d | Default |\n", "")
    done = _run(_doc(tmp_path, body), _items(("Orphan Token", "2026-10-08T00:00:00Z")))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "NO POLICY  Orphan Token" in done.stdout


def test_an_empty_item_list_is_exit_2(tmp_path):
    done = _run(_doc(tmp_path), [])
    assert done.returncode == 2, done.stdout + done.stderr
    assert "nothing was inspected" in done.stderr


def test_an_empty_tree_is_exit_2(tmp_path):
    """The gate contract: could-not-inspect is 2, never 1."""
    done = _run(tmp_path, _items(("Anything", "2026-10-08T00:00:00Z")))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "Traceback" not in done.stderr


def test_a_vault_read_is_never_attempted_without_the_schedule(tmp_path, monkeypatch):
    """`op` is reached only after the policy parses, so a doc typo cannot be
    reported as a vault failure."""
    monkeypatch.setattr(mod, "_op_item_list", lambda vault: pytest.fail("op was called"))
    assert mod.main(["--repo-root", str(tmp_path)]) == 2


# --- the real policy --------------------------------------------------------


def test_the_live_schedule_table_parses():
    rows = mod.parse_schedule(DOC)
    assert len(rows) >= 5, "docs/15 § Scheduled Rotation Policy lost its rows"
    assert any(r.patterns == ["*"] for r in rows), (
        "docs/15 § Scheduled Rotation Policy has no `*` row, so every item with "
        "no explicit row reports as unscheduled"
    )


def test_the_live_schedule_covers_the_documented_inventory():
    """Every item title docs/15 lists is claimed by some row."""
    rows = mod.parse_schedule(DOC)
    text = DOC.read_text(encoding="utf-8")
    block = text[text.index("### Inventory"):text.index("### Item detail")]
    titles = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        first = line.strip("|").split("|")[0].strip()
        if first in ("Item", "") or set(first) <= set("-: "):
            continue
        titles += [part.strip().strip("`") for part in first.split(",")]
    assert len(titles) >= 40, "the inventory scan found almost nothing"
    assert [t for t in titles if mod.claim(rows, t) is None] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
