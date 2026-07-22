#!/usr/bin/env python3
"""Drift guard: every offsite (restic → B2) source must also be an archive source.

Two hand-kept dataset lists must never diverge:

  * restic_offsite include set — `restic_offsite_sources` (mountpoints) +
    `restic_offsite_zvol_sources` (zvols), in
    ansible/roles/restic_offsite/defaults/main.yml. These are the datasets
    restic uploads to Backblaze B2.
  * nas_storage archive `SRC_LIST` — the bash array in
    ansible/roles/nas_storage/templates/archive-backupctl.sh.j2. These are the
    datasets the local `archive` raw-`zfs send -w` replication covers.

restic reads the newest `archsync-*` snapshot of each source and its freshness
guard aborts if that snapshot is stale (docs/42) — so an offsite source that is
NOT an archive source has no snapshot to read and would silently never upload.
This test fails the moment someone adds a restic source without adding the
matching archive source.

Archive replication is recursive (`zfs send -R` includes children), so a restic
source is "covered" if it, OR any ancestor dataset, is in `SRC_LIST` — that is
how the two file-bearing zvols (`tank/immich-data/disk`,
`tank/nextcloud-data/disk`) are covered by their parents in `SRC_LIST`.

Run with pytest:
    pytest scripts/test_offsite_archive_coverage.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RESTIC_DEFAULTS = REPO / "ansible" / "roles" / "restic_offsite" / "defaults" / "main.yml"
ARCHIVE_J2 = (
    REPO / "ansible" / "roles" / "nas_storage" / "templates" / "archive-backupctl.sh.j2"
)


def restic_offsite_datasets() -> set[str]:
    """ZFS datasets in the restic_offsite include set.

    `restic_offsite_sources[].mountpoint` is `/mnt/<dataset>` (strip the prefix);
    `restic_offsite_zvol_sources[].zvol` is the dataset name verbatim.
    """
    data = yaml.safe_load(RESTIC_DEFAULTS.read_text())
    datasets = {
        src["mountpoint"].removeprefix("/mnt/")
        for src in data["restic_offsite_sources"]
    }
    datasets |= {z["zvol"] for z in data["restic_offsite_zvol_sources"]}
    return datasets


def archive_src_list() -> set[str]:
    """Datasets in the nas_storage archive `SRC_LIST=(...)` bash array.

    Anchored to the array *definition* (`^SRC_LIST=(` ... `^)`) so the many
    `"${SRC_LIST[@]}"` usages elsewhere in the template are not mis-parsed.
    """
    text = ARCHIVE_J2.read_text()
    match = re.search(r"^SRC_LIST=\((.*?)^\)", text, re.DOTALL | re.MULTILINE)
    assert match, f"SRC_LIST=( ... ) array not found in {ARCHIVE_J2.name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _covered(dataset: str, src_list: set[str]) -> bool:
    """True if `dataset` or any ancestor dataset is in `src_list` (recursive send)."""
    parts = dataset.split("/")
    return any("/".join(parts[:i]) in src_list for i in range(1, len(parts) + 1))


class TestOffsiteSubsetOfArchive:
    def test_lists_are_non_empty(self):
        # Guard against a parser returning {} and the coverage test passing vacuously.
        assert restic_offsite_datasets(), "parsed no restic_offsite datasets"
        assert archive_src_list(), "parsed no archive SRC_LIST datasets"

    def test_every_offsite_source_is_an_archive_source(self):
        restic = restic_offsite_datasets()
        archive = archive_src_list()
        uncovered = sorted(d for d in restic if not _covered(d, archive))
        assert not uncovered, (
            "restic_offsite sources with no archive SRC_LIST coverage "
            f"(add them to nas_storage SRC_LIST): {uncovered}\n"
            f"  restic offsite: {sorted(restic)}\n"
            f"  archive SRC_LIST: {sorted(archive)}"
        )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
