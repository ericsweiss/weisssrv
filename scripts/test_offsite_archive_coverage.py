#!/usr/bin/env python3
"""Drift guard: the offsite (restic -> B2) and archive dataset lists must agree.

The original gate read `ansible/roles/{restic_offsite,nas_storage}/...`; both
roles moved to the weisssrv.infra collection, and with them the defaults. What
did NOT move is the data: this cluster's two lists now live side by side in
`ansible/inventories/prod/host_vars/pve-nas-01.yml`, so the invariant is
re-expressed against the inventory and needs no collection checkout.

The durability invariant (docs/42): restic reads the newest `archsync-*`
snapshot of each source, and its freshness guard aborts when that snapshot is
stale. An offsite source that is NOT an archive source therefore has no snapshot
to read and would silently never upload.

Both directions are asserted:

  * every restic source is covered by an archive source (itself or an ancestor —
    archive replication is recursive, which is how the two file-bearing zvols
    `tank/{immich,nextcloud}-data/disk` are covered by their parents);
  * every archive source is either an offsite source or listed in
    ARCHIVE_ONLY with a reason, so dropping a dataset from the restic list
    cannot pass as "intentional" without saying so.

Run with pytest:
    pytest scripts/test_offsite_archive_coverage.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
NAS_HOST_VARS = REPO / "ansible/inventories/prod/host_vars/pve-nas-01.yml"

# Archive sources deliberately NOT uploaded offsite, dataset -> reason. Every
# entry is a decision recorded in docs/42; adding one is the reviewable act.
ARCHIVE_ONLY = {
    "tank/proxmox": (
        "vzdump guest images: nightly-fresh, poorly dedupable .zst — excluded "
        "from B2 on cost grounds (docs/42 Cost). The guests' own logical dumps "
        "under tank/backups/apps are what ride offsite."
    ),
    "tank/immich-data": (
        "the photo library lives inside the guest filesystem on the child zvol "
        "tank/immich-data/disk, which IS an offsite source (a file walk cannot "
        "see a live zvol's block device)."
    ),
    "tank/nextcloud-data": (
        "same shape as tank/immich-data: the user files ride offsite through "
        "the child zvol tank/nextcloud-data/disk."
    ),
}


def _host_vars() -> dict:
    return yaml.safe_load(NAS_HOST_VARS.read_text()) or {}


def restic_offsite_datasets() -> set[str]:
    """ZFS datasets in the restic_offsite include set.

    `restic_offsite_sources[].mountpoint` is `/mnt/<dataset>` (strip the
    prefix); `restic_offsite_zvol_sources[].zvol` is the dataset name verbatim.
    """
    data = _host_vars()
    datasets = {
        src["mountpoint"].removeprefix("/mnt/")
        for src in data.get("restic_offsite_sources") or []
    }
    datasets |= {z["zvol"] for z in data.get("restic_offsite_zvol_sources") or []}
    return datasets


def archive_datasets() -> set[str]:
    """Datasets in nas_storage_archive_backup_sources (recursive raw send)."""
    return set(_host_vars().get("nas_storage_archive_backup_sources") or [])


def _covered(dataset: str, sources: set[str]) -> bool:
    """True if `dataset` or any ancestor is in `sources` (recursive send)."""
    parts = dataset.split("/")
    return any("/".join(parts[:i]) in sources for i in range(1, len(parts) + 1))


class TestOffsiteArchiveParity:
    def test_lists_are_non_empty(self):
        # Guard against a parser returning set() and the coverage tests passing
        # vacuously — a renamed inventory key must fail loudly, not silently.
        assert restic_offsite_datasets(), "parsed no restic_offsite datasets"
        assert archive_datasets(), "parsed no nas_storage_archive_backup_sources"

    def test_every_offsite_source_is_an_archive_source(self):
        restic, archive = restic_offsite_datasets(), archive_datasets()
        uncovered = sorted(d for d in restic if not _covered(d, archive))
        assert not uncovered, (
            "restic_offsite sources with no archive coverage — restic reads the "
            "newest archsync-* snapshot of each source, so these would silently "
            f"never upload. Add them to nas_storage_archive_backup_sources: {uncovered}\n"
            f"  restic offsite: {sorted(restic)}\n"
            f"  archive sources: {sorted(archive)}"
        )

    def test_every_archive_source_is_offsite_or_documented(self):
        restic, archive = restic_offsite_datasets(), archive_datasets()
        undocumented = sorted(
            d
            for d in archive
            if d not in ARCHIVE_ONLY
            and not any(_covered(r, {d}) for r in restic)
        )
        assert not undocumented, (
            "archive sources that reach no offsite source and carry no recorded "
            "reason — either add them to restic_offsite_sources or give them an "
            f"ARCHIVE_ONLY entry saying why they stay on-site: {undocumented}"
        )

    def test_archive_only_entries_are_still_archive_sources(self):
        """A stale exemption hides the next real gap."""
        stale = sorted(set(ARCHIVE_ONLY) - archive_datasets())
        assert not stale, f"ARCHIVE_ONLY names datasets no longer archived: {stale}"

    @pytest.mark.parametrize("dataset", sorted(ARCHIVE_ONLY))
    def test_archive_only_entries_carry_a_reason(self, dataset):
        assert ARCHIVE_ONLY[dataset].strip(), f"{dataset}: empty reason"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
