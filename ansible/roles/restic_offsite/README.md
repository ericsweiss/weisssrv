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

`run` (timer/OnSuccess target), `restore <name> [snap] [dir]`,
`verify [--full]` (`restic check`), `snapshots`, `prune`, `status`. Single-
instance `flock`; run/restore/verify/prune share the lock.

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
   `rclone_crypt_password`); SSE-B2 is a redundant extra. rclone deletes by
   *hiding*, and the B2 lifecycle (terraform/b2) expires hidden versions, so a
   capability-restricted key (no `deleteFiles`) still prunes.

## Secrets

`b2_key_id`, `b2_application_key`, `rclone_crypt_password` come from the
`secrets:` dict (1Password item **B2 Archive Backup**). The env file
(`RESTIC_PASSWORD`) and `rclone.conf` (B2 key) render `0600` with `no_log`.

## Install / versions

`restic` + `rclone` install from the Debian archive (`state: present`) so the
molecule scenario stays hermetic. `restic_version` / `rclone_version` are
**advisory** apt pins (empty = distro) — set to an exact Debian version to
hard-pin.

## Molecule

Hermetic: a **local restic file repo** (no B2/network), `bind_mode: direct` (no
ZFS), and a fake `.zfs/snapshot` tree. Exercises a real `run` (freshness guard →
backup → metrics), the stale-source abort (`success=0`), and statically pins the
zvol-clone / subcommand / restic-flag / metric-name contract.
