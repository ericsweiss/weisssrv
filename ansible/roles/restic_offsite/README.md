# restic_offsite

Nightly **offsite** backup of the high-value, file-walkable estate to
**Backblaze B2** using **restic** (client-side encryption) over **rclone**.
Runs on `pve-nas-01` only. Chained `OnSuccess=` after `archive-backup.service`
so B2 uploads a **consistent point-in-time** that a known-good archive
replication just produced.

Companion to `nas_storage`'s `archive-backupctl` (local pool-to-pool ZFS
replication): archive is the *local* DR copy (raw `zfs send -w`); this role is
the *offsite* copy. See `docs/42-offsite-backup.md`.

## How it reads a consistent snapshot

restic never reads the live datasets. For each source it binds the newest
`archsync-*` snapshot (created by `archive-backupctl`) at a **stable path**
(`/mnt/restic-src/<name>`) so restic's parent-snapshot optimization re-reads
only changed files instead of re-hashing ~1 TB nightly:

- **File-walkable datasets** (`tank/backups`, `tank/share`, `ssd/appdata`,
  `ssd/databases`, `ssd/k3s-etcd`) — `mount --bind -o ro` the
  `.zfs/snapshot/archsync-*` subtree.
- **File-bearing data zvols** (`tank/immich-data/disk`,
  `tank/nextcloud-data/disk`) — a file walk can't see a live zvol, so the
  control script **clones** the newest `archsync-*` snapshot to a throwaway
  sibling zvol and mounts its ext4 read-only (`ro,noload`) at
  `/run/restic-offsite/<name>`. An **EXIT trap** unmounts + destroys every clone
  so a crashed run never strands one.

A **freshness guard** aborts the run (metric `success=0`, no upload) if any
source's newest snapshot is older than `restic_offsite_freshness_max_age_h`
(default 26h) — B2 must never upload a stale tree.

## What is NOT offsited (intentional)

`tank/proxmox` (vzdump images — local + archive DR only; poor dedup, huge),
`tank/media` (huge, non-sensitive), `ssd/appdata/{prometheus,loki}` (own
retention). The zvol-backed app DBs/config are covered by the **logical dumps**
that land on `tank/backups/apps/*` (which the `backups` source walks whole).

## Control script — `restic-offsitectl`

`run [--force]` (timer/OnSuccess target), `restore <name> [snap] [dir]`,
`verify [--full|--auto-subset]` (`restic check`; `--auto-subset` is the rotating
1/12 read-data mode the weekly `restic-offsite-verify.timer` runs), `snapshots`,
`prune`, `status`. Single-instance `flock`; run/restore/verify/prune share the
lock.

`run` carries two guards. The **freshness guard** refuses to upload a tree whose
newest `archsync-*` snapshot is older than `restic_offsite_freshness_max_age_h`
(aborts with `success=0`). The **already-uploaded guard** skips entirely when the
last successful run already covers the newest source snapshot AND every source is
present and fresh — because BOTH triggers (archive-backup's `OnSuccess=` and the
fallback timer) fire every night, and without it the job ran twice. `--force`
overrides the second guard only.

## Metrics (node_exporter textfile)

`/var/lib/node_exporter/restic_offsite.prom`:
`restic_offsite_last_run_success`, `restic_offsite_last_success_timestamp_seconds`
(preserved on failure), `restic_offsite_last_run_duration_seconds`,
`restic_offsite_repo_size_bytes`, `restic_offsite_snapshot_total_bytes`.
`restic_offsite_verify.prom`: `restic_offsite_last_verify_success`,
`restic_offsite_last_verify_timestamp_seconds`. Alerts `ResticOffsiteFailed` /
`ResticOffsiteStale` live in the kube-prometheus-stack rules.

## Security (three independent at-rest layers)

1. Local dataset encryption (dumps land on `tank/backups`, `aes-256-gcm`).
2. Archive replication (raw `zfs send -w` — encrypted-at-rest blobs, no key).
3. Offsite: B2 holds **restic client-side ciphertext** (repo password =
   `restic_repo_password`); SSE-B2 is a redundant extra. rclone deletes by
   *hiding*, and the B2 lifecycle (codified in `scripts/b2-bucket-drift.py` —
   the retired `terraform/b2` module is gone) expires hidden versions at 30 days,
   so a capability-restricted key (no `deleteFiles`) still prunes. The bucket has
   **no Object Lock**, so `restic_offsite_keep_last` is the retention floor that
   keeps corruption from walking every restore point out (docs/42).

## Secrets

`b2_key_id`, `b2_application_key`, `restic_repo_password` come from the
`secrets:` dict (1Password item **B2 Archive Backup**). The env file
(`RESTIC_PASSWORD`) and `rclone.conf` (B2 key) render `0600` with `no_log`.

## Install / versions

`restic` installs from the Debian archive (`state: present`) so the molecule
scenario stays hermetic; `restic_version` is an **advisory** apt pin (empty = distro).

`rclone` does **not**: it installs from the upstream **rclone.org release deb**,
and `rclone_version` + `rclone_deb_sha256` are
**mandatory, asserted** pins (`tasks/main.yml`, "Assert the rclone pin inputs are
sane") — the role refuses to run without a matching checksum. Any stray
`/usr/local/bin/rclone` shadowing the packaged binary is removed.

## Molecule

Hermetic: a **local restic file repo** (no B2/network), `bind_mode: direct` (no
ZFS), and a fake `.zfs/snapshot` tree. Exercises a real `run` (freshness guard →
backup → metrics), the stale-source abort (`success=0`), and statically pins the
zvol-clone / subcommand / restic-flag / metric-name contract.
