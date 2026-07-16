# Proxmox Backup Role

Declaratively manages Proxmox storage entries (`storage.cfg`) and vzdump
backup jobs (`jobs.cfg`) via `pvesh`, so the nightly-backup configuration
lives in git instead of only in the GUI. Owns the `tank-proxmox` NFS backup
target and its nightly vzdump job on pve-nas-01.

## What This Role Manages

- **Storage entries** (`/storage` API): creates missing entries; reconciles
  the mutable properties (`content`, `options`) on drift with a set-based
  comparison (Proxmox reorders comma lists); **fails loud** when a
  create-fixed property (`server`, `export`, `path`) drifts, printing the
  manual recreation procedure — Proxmox cannot update those in place.
- **vzdump backup jobs** (`/cluster/backup` API): creates missing jobs with
  the configured `id`; reconciles `storage`, `schedule`, `mode`, `compress`,
  `enabled`, guest selection (`all`, `vmid`, `exclude`), `prune-backups`,
  notification and mail settings, and the `script` hookscript path on drift.
  Unmanaged live jobs are **warned about, never deleted** (same policy as
  proxmox_ha's orphaned replication jobs).
- **vzdump metrics hookscript**: a job's `script` key points at
  `/usr/local/bin/vzdump-metrics-hook.sh`, which writes
  `vzdump_backup_last_run_success` + `_last_success_timestamp_seconds` to the
  node_exporter textfile dir (feeding the `VzdumpBackupFailed`/`Stale` alerts).
  The script is deployed to **every** Proxmox host by `node_exporter_host` — a
  cluster-wide `all` job runs on each node for its local guests and invokes the
  hook there, so it must exist fleet-wide, not only where this role runs.

## Configuration

Variables live in `host_vars/pve-nas-01.yml`:

```yaml
pve_storage:
  - id: tank-proxmox
    type: nfs
    server: pve-nas-01.esweiss.com  # hostname, not IP — the wildcard cert has no IP SAN
    export: /tank-proxmox
    content: "snippets,backup,vztmpl,iso"
    options: "vers=4.2,xprtsec=tls"

pve_vzdump_jobs:
  - id: backup-10f33360-a177  # matches the live jobs.cfg id -> the role ADOPTS the job
    storage: tank-proxmox
    schedule: "03:30"
    mode: snapshot
    compress: zstd
    all: true
    bwlimit: 30720                # optional: cap per-node backup I/O (KiB/s)
    prune_backups: "keep-daily=7,keep-weekly=4,keep-monthly=3"
    notification_mode: notification-system
    enabled: true
    script: /usr/local/bin/vzdump-metrics-hook.sh  # deployed by node_exporter_host
```

**Adopting a GUI-created job**: set the entry's `id` to the live job id
(`pvesh get /cluster/backup --output-format json`) and mirror the live values
— the role then reconciles instead of creating a duplicate.

**Migrating a storage entry from a legacy IP to hostname+TLS** (done for
`tank-proxmox` on 2026-07-14; keep for the next entry that needs it — one-time,
outside a backup window; the fixed-property assert enforces that this has
happened before the role converges):

```bash
ssh pve-nas-01 "sudo pvesh delete /storage/tank-proxmox"  # config entry only, data untouched
# then re-run the role to create it from config (hostname + xprtsec=tls)
```

Prerequisite for `xprtsec=tls`: the nas_storage export for `/tank-proxmox`
must allow TLS and tlshd must run on the mounting host (see the nfs_tls
role); mount by **hostname** — the `*.esweiss.com` cert has no IP SAN.

## Deployment

Cluster-wide config (`/etc/pve`) — wire the role into a play that targets a
single node (pve-nas-01). The role no-ops while both variable lists are empty.
The node-local metrics hookscript a job's `script` key references is deployed
separately, to every Proxmox host, by `node_exporter_host`.

## Files

- `tasks/main.yml` - Storage + vzdump job reconciliation via pvesh
- `defaults/main.yml` - Default empty lists + variable shapes

## Dependencies

- Proxmox VE node with `pvesh` (cluster quorate for /etc/pve writes)
- For NFS-over-TLS targets: nfs_tls role deployed on server and mounting host
