# Disaster Recovery

This document covers disaster recovery scenarios for rebuilding the homelab
from backups / git. It is split into three tracks you may need to combine:

1. **Storage** — rebuild ZFS pools + datasets on the NAS (this doc's original scope).
2. **k3s cluster** — bring up the 9-node k3s cluster from Ansible.
3. **Flux GitOps + secrets encryption** — bootstrap Flux, restore ESO, optionally rotate k3s secrets-encryption keys.

The three tracks correspond to the deployment order in
`docs/19-k3s-deployment.md` and `docs/29-flux-operations.md`; DR is those
guides applied to empty hardware.

## Storage Bootstrap

### When to Use This

Use the storage bootstrap playbook for these scenarios:

1. **Initial NAS Setup** - Brand new hardware, no existing ZFS pools
2. **Complete Hardware Failure** - Replacing NAS server entirely
3. **Disaster Recovery** - Rebuilding from backup after catastrophic failure
4. **Adding New Storage Pools** - Expanding storage with new ZFS pools

**DO NOT use this for:**
- Normal deployments (use `task infra:deploy` instead)
- Adding new datasets to existing pools (use ZFS commands directly)
- Configuration changes only (use `task storage:deploy`)

## Storage Safety Guarantees

The storage management in this infrastructure is designed to be non-destructive:

**What storage tasks will NOT do:**
- Create or destroy ZFS pools
- Create or destroy ZFS datasets
- Format any disks
- Delete any files
- Overwrite existing data

**What storage tasks WILL do:**
- Set ZFS dataset properties (metadata only - compression, mountpoint, etc.)
- Mount existing filesystems (MergerFS, NFS bind mounts)
- Configure services (NFS exports, Samba shares)
- Install and configure monitoring (SMART, backup jobs)

### Safe vs Destructive Operations

**Safe Operations** (automated via Ansible):
- Setting ZFS properties (compression, atime, recordsize)
- Creating mount points and bind mounts
- Configuring NFS exports and Samba shares
- Moving data between tiers (media mover)
- Creating backups

**Destructive Operations** (require manual intervention):
- Creating ZFS pools (must be done manually)
- Destroying ZFS datasets
- Formatting disks
- Deleting data permanently

The *regular* storage tasks (`task storage:deploy` / the `nas_storage`
role) are idempotent and fail safely if expected resources don't exist
rather than creating them. The *bootstrap* flow documented below is the
deliberate exception: it creates missing datasets/directories, but only
interactively and after an explicit confirmation step — pools are never
created by either path.

## Accepted Risk: NAS-Concentrated State

All zvol-backed application state (Authentik + Mealie PostgreSQL,
Prometheus, Loki, GitLab repos, Nextcloud + Immich Postgres/app data)
deliberately lives on pve-nas-01's `ssd` pool — a NAS or pool failure takes
those services down together. This is
an explicit single-box tradeoff, mitigated by raidz1 + ZFS snapshots +
the archive replication job (`archive-backupctl`) + GitLab's nightly
backups, not by replication-grade HA. Recovery is restore-from-backup
with the RTO that implies. Revisit if a second storage-capable node ever
joins.

The single-chassis concentration is now mitigated by an **offsite tier**: the
nightly restic → Backblaze B2 run ([docs/42-offsite-backup.md](42-offsite-backup.md))
pushes client-side-encrypted copies of the file-walkable estate, the relocated
logical dumps on `tank/backups/apps` (Authentik / Mealie / GitLab / Immich /
Nextcloud / Home Assistant), the Immich and Nextcloud data zvols (via snapshot
clones), and the `ssd/k3s-etcd` off-node etcd copies — so a chassis / site loss
no longer takes the only copies. **3-2-1 is met for everything except the
intentional exclusions**: `tank/media` (replaceable bulk media),
Prometheus/Loki history (config is in git; history accepted-loss), vzdump VM
images (IaC-rebuildable; local + archive copies only), and the Windows VM
(excluded entirely by design). GFS retention on B2: 3 daily / 2 weekly /
3 monthly / 1 yearly. Restore paths in docs/42 §Restore.

The archive's raw-encrypted streams and `plug`/`unplug` workflow remain
purpose-built for physical offsite rotation (the pool can be detached and
carried offsite) — an unused option now that B2 covers offsite, retained as a
documented fallback.

## Accepted Risk: Network Fabric SPOF

The network fabric is a single point of failure the rest of this analysis
otherwise omits. Both legs of every active-backup bond plug into **one unmanaged
switch** (see [docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md)), and there
is **one router/gateway** (Asus GT-AX11000 at 192.168.0.1, which is also DHCP and
the Cloudflare-origin port-forwarder). Everything rides one flat `vmbr0` /24 with
**no second corosync ring** (`cluster.fw.j2` reserves 5406/ring1 for a future
knet link, but only 5405/ring0 is configured). A switch or router failure does not
merely drop internet — it collapses Proxmox corosync quorum and k3s etcd traffic
simultaneously, since both traverse that single L2; the documented bond-MAC-flap
history already shows fabric instability black-holing HA guests. This is inherent
to a homelab budget. **A second switch + a second corosync ring on a separate
NIC/VLAN is a tracked roadmap item that needs a user decision** — see
[docs/16-next-steps.md](16-next-steps.md) ("Network-fabric SPOF: second switch +
corosync ring").

## Backup Dedup: vzdump Exclusion of App-Data Zvols

The app-data zvols (Authentik/Mealie PostgreSQL, Prometheus, Loki, GitLab
repos, and Nextcloud/Immich Postgres + app) plus the Plex `/config` LXC directory
bind mount (not a zvol — `mp0` →
`/mnt/ssd/appdata/plex`) are backed up by `archive-backupctl` (`ssd/appdata` →
`archive`, raw ZFS). They are attached to their host VM/CT as passthrough
disks (or, for Plex, a bind mount), so they were *also* captured by the nightly
vzdump into `tank/proxmox`
— double-storing ~2 TB. They now carry `backup=0` (`vzdump_backup: false` in
`hosts.yml`; `backup=0` in the Plex LXC mount options), so vzdump skips them
and **`archive-backupctl` is their sole backup path.** The Nextcloud/Immich bulk
media zvols (`tank/{nextcloud,immich}-data`) likewise carry `backup=0` and are
backed up via their own `tank/…-data → archive` replication. OS disks stay in
vzdump — only the app-data passthroughs are excluded.

**Cutover prerequisite — order matters.** Apply `backup=0` only *after* the
`ssd/appdata` raw re-seed is deployed and verified green; setting it while that
re-seed is broken would leave these zvols with no working backup path. The
`proxmox_vm` attach task re-applies on the next `task k3s:deploy` / GitLab
deploy. The existing Plex CT needs a one-time manual step (the LXC role sets
mounts only at container creation):

```bash
pct set 152 --mp0 /mnt/ssd/appdata/plex,mp=/config,backup=0
```

Reclaiming the already-accumulated oversized vzdump tarballs is a separate
one-time manual prune (or let `tank-proxmox` retention age them out).

## Restore Procedures

Two paths feed the `archive` pool (see "Backup Dedup" above): the nightly
`vzdump` of every VM/CT into `tank/proxmox`, and `archive-backupctl`'s direct
raw/encrypted replication of `tank/{share,backups,nextcloud-data,proxmox,
immich-data}` + `ssd/{appdata,databases}`. **Everything in `archive` is raw
(`zfs send -w`) and encrypted under its source's key** — `archive` never holds a
loaded key, so any restored dataset needs `zfs load-key` before it can be read
(passphrase in 1Password; see `docs/32-zfs-encryption.md` and
`docs/15-credential-rotation.md`).

### From the archive (`archive-backupctl`)

`archive-backupctl` is the restore tool for every directly-replicated dataset.
Import the pool first if it is detached (`archive-backupctl plug`), then:

```bash
# SAFE (default): restore the latest snapshot into a NEW dataset
#   <source>-restore-<timestamp>, mounted under /mnt/restore/<target>/<ts>/.
# Non-destructive — the live dataset is untouched. The restore is received onto
# the SOURCE pool (tank/ssd), so the space cost lands there, not on the archive;
# check `zfs list -o avail <pool>` first for large targets. The tool resets the
# received lockdown props (mountpoint, readonly) and attempts the mount itself;
# encrypted (key-less) trees still need the load-key + mount steps below.
sudo archive-backupctl restore <target>   # share|backups|nextcloud-data|proxmox|
                                          # immich-data|appdata|databases|all

# The restored clone is raw + encrypted (key-less). These pools use multiple
# SIBLING encryption roots that share one passphrase (Model B, docs/32) — NOT one
# nested root: a bare `zfs load-key -r` reads the passphrase once, unlocks the
# first root, and fails the rest. `-L prompt` re-prompts per root (this is exactly
# what the tool prints; the per-root loop in docs/32 is the scriptable form):
sudo zfs load-key -r -L prompt <pool>/<target>-restore-<timestamp>   # e.g.
                                                          # ssd/appdata-restore-<ts>
sudo zfs mount -r <pool>/<target>-restore-<timestamp>     # scoped to the restore
                     # tree (under /mnt/restore/<target>/<ts>/). Mounts the restored
                     # *filesystem* children only — the app-DB ZVOLS (authentik/
                     # postgres, prometheus/data, loki/data, gitlab/repos) are block
                     # devices: after load-key they appear under /dev/zvol/<pool>/
                     # ...-restore-<ts>/..., restore them per "App-data zvols" below.

# VERIFY before trusting the restore — a key-less `zfs mount` silently mounts
# nothing. No row may read `unavailable` (snapshots/unencrypted read `-`):
sudo zfs get -H -o value -r keystatus <pool>/<target>-restore-<timestamp> \
  | grep -qx unavailable && echo "STILL LOCKED — load-key per root before reading"

# DESTRUCTIVE in-place restore (`zfs receive -u -F` over the LIVE dataset). The
# tool does NOT stop consumers, and `receive -F` cannot roll back a dataset whose
# zvol children are held open by a running guest — it fails "dataset is busy", or
# corrupts the live volume if forced through. QUIESCE EVERY WRITER FIRST:
#   1. Stop the guest(s) holding the dataset. For ssd/appdata that is the
#      k3s-agt-nas-01 VM (vmid 202: authentik/mealie postgres, prometheus, loki),
#      the GitLab VM (vmid 153: gitlab/repos), the Nextcloud VM (vmid 156:
#      nextcloud app/postgres), AND the Immich VM (vmid 157: immich app/postgres)
#      — a recursive restore over ssd/appdata touches ALL of them. `qm stop <vmid>`
#      releases the passthrough zvol; scaling the k8s pod to 0 does NOT (the VM,
#      not the pod, holds it).
#      For tank/{share,proxmox}: stop/unexport the NFS consumers.
#   2. Confirm nothing holds the zvol nodes (zfs holds lists snapshot holds, not
#      open devices): sudo fuser -v /dev/zvol/ssd/appdata/*/*   # expect no holders
#   3. sudo archive-backupctl restore-force <target>
#   4. Load the key + confirm the data reads (DB starts, files present) BEFORE
#      restarting consumers (`qm start`/`pct start`, re-enable exports).
sudo archive-backupctl restore-force <target>
```

After a force restore the tool automatically resets the lockdown props the
stream carries from the archive (mountpoint back to `/mnt/<pool>/<dataset>`,
`readonly` cleared) and remounts the tree — restored data no longer sits
unmounted/read-only waiting for a manual `zfs inherit`. Re-run the storage
deploy afterwards to re-assert the full `host_vars` property set.

Never `zfs load-key` + mount an `archive/<dataset>` in place — it dirties the raw
incremental chain and forces a full re-seed (docs/32). Always restore to a clone.

### App-data zvols (Postgres, Prometheus, Loki, GitLab repos, Nextcloud/Immich)

These live on `ssd/appdata` and — since the vzdump dedup (`backup=0`) — are **no
longer in the VM/CT vzdumps**; `archive/appdata` is now their sole backup. Restore
them from the archive, not from a VM image:

```bash
sudo archive-backupctl restore appdata                  # -> ssd/appdata-restore-<ts>
sudo zfs load-key -r -L prompt ssd/appdata-restore-<ts> # per-root prompt (Model B)
# The zvol you need is e.g. ssd/appdata-restore-<ts>/prometheus/data. These zvols
# are PASSTHROUGH block devices on the k3s-agt-nas-01 VM (vmid 202) — the in-guest
# pod is NOT the holder, the VM is. Stop the VM to release the device (this also
# takes authentik/mealie postgres + loki offline); then send the snapshot back:
sudo qm stop 202                                        # on pve-nas-01
sudo fuser -v /dev/zvol/ssd/appdata/prometheus/data     # confirm free (host side)
sudo zfs send ssd/appdata-restore-<ts>/prometheus/data@<snap> \
  | sudo zfs receive -F ssd/appdata/prometheus/data
sudo qm start 202                                       # bring the node back, verify
# (`restore-force appdata` does the same `receive -F` recursively over the WHOLE
#  ssd/appdata tree — every zvol held by vmids 202, 153, 156, AND 157 — so stop
#  ALL of them first, not just 202, per the destructive-restore quiesce steps above.)
```

The zvol re-attaches to its VM via the `proxmox_vm` role's `vm_additional_disks`
on the next deploy (it carries `vzdump_backup: false`, so it stays out of vzdump).

### VM/CT images (from vzdump)

Every VM/CT OS disk is in the nightly `vzdump` on `tank/proxmox` (live, decrypted
on pve-nas-01) and replicated to `archive/proxmox` (raw/encrypted).

```bash
# Normal case — from the live tank/proxmox copy (Proxmox UI, or):
# NOTE: if <vmid>/<ctid> still exists, qmrestore/pct restore REFUSE unless you add
#   --force (or pick a fresh, unused id). --force first DESTROYS the existing guest
#   config + its OS/root volume, then restores. The app-data passthrough zvols (raw
#   /dev/zvol paths, backup=0) and Plex's bind mount are NOT Proxmox-managed, so
#   --force only drops their config reference (re-attached on next deploy) — the
#   underlying data is untouched. Prefer a fresh id to avoid touching the live guest.
sudo qmrestore /mnt/tank/proxmox/dump/vzdump-qemu-<vmid>-<ts>.vma.zst <vmid> --storage <pool>
sudo pct restore <ctid> /mnt/tank/proxmox/dump/vzdump-lxc-<ctid>-<ts>.tar.zst --storage <pool>

# If only the archive copy survives, restore it, load the key + mount, then
# restore the guest from the dump dir on the restored dataset:
sudo archive-backupctl restore proxmox           # -> tank/proxmox-restore-<ts>,
                                                 #    mounted at /mnt/restore/proxmox/<ts>/
sudo zfs load-key -r -L prompt tank/proxmox-restore-<ts>
sudo zfs mount -r tank/proxmox-restore-<ts>
sudo qmrestore  /mnt/restore/proxmox/<ts>/dump/vzdump-qemu-<vmid>-<bts>.vma.zst <vmid> --storage <pool>
sudo pct restore <ctid> /mnt/restore/proxmox/<ts>/dump/vzdump-lxc-<ctid>-<bts>.tar.zst --storage <pool>
```

App-data zvols recovered this way are the **stale pre-dedup copies** — prefer the
`archive/appdata` path above for current app data.

### From B2 (restic offsite)

The **offsite** copy of the file-walkable, high-value estate lives in Backblaze
B2 as restic client-side ciphertext (`restic_offsite` role, docs/42). Use it when
BOTH the live NAS and the archive pool are lost, or to pull a single file/dataset
back from offsite. Repo password = the `restic_repo_password` field of the
`B2 Archive Backup` 1Password item — **without it the repo is unrecoverable**
(keep an offline copy). On a rebuilt pve-nas-01 the role renders
`/etc/restic-offsite/{env,rclone.conf}`; to restore from another host, install
`restic` + `rclone`, drop those two files, then:

```bash
# Source the repo env (RESTIC_REPOSITORY/RESTIC_PASSWORD/RCLONE_CONFIG/...)
set -a; . /etc/restic-offsite/env; set +a

restic snapshots                      # find the snapshot to restore
restic-offsitectl verify              # (optional) restic check integrity first

# Whole-source restore (default target /mnt/restore/restic/<name>/<ts>):
#   names: backups | share | appdata | databases | k3s-etcd | immich-data | nextcloud-data
sudo restic-offsitectl restore appdata

# Ad-hoc single-path restore:
restic restore latest --target /tmp/rtest --include /mnt/restic-src/appdata/sonarr/config.xml

# Immich photos / Nextcloud user files (zvol-clone sources — restored as plain
# files, then copied back into the live app's data mount):
restic restore latest --target /mnt/restore/immich --include /run/restic-offsite/immich-data
```

restic restores are **already decrypted** (restic holds the repo key) — unlike
the archive `zfs send -w` path, no `zfs load-key` is needed. Logical DB dumps
land under `.../backups/apps/<app>` in the restored `backups` tree; replay them
with the app's normal restore (`pg_restore`, `gitlab-backup restore`, etc.).

### Other backup types

- **GitLab** — app-consistent nightly tarball + `gitlab-secrets.json` + `gitlab.rb`.
  The landing was **relocated** off the VM OS disk onto the NFS mount
  `/mnt/backups-offsite` (= `tank/backups/apps/gitlab`), so it now rides the
  archsync file walk into B2 (docs/42) instead of only the whole-VM vzdump image.
  Restore with `gitlab-backup restore` (`docs/27-gitlab-deployment.md`); the
  whole-VM vzdump image remains the bare-metal DR path.
- **Grafana** — the SQLite DB (`grafana.db`) lives on the NFS export
  `/appdata/grafana`, a bind of `ssd/appdata`, so it rides the `ssd/appdata →
  archive/appdata` replication (it is a *file*, not a zvol — the send-back above
  doesn't apply). Restore file-wise: `archive-backupctl restore appdata`, then
  `zfs load-key -r -L prompt ssd/appdata-restore-<ts>` + `zfs mount -r
  ssd/appdata-restore-<ts>`, scale Grafana to 0, copy `grafana.db` from
  `/mnt/restore/appdata/<ts>/grafana/` into `/mnt/ssd/appdata/grafana/` (owner
  `1000:2000`), scale back up. Most Grafana state is reproducible from git
  (dashboards via ConfigMap, datasources via config); only service accounts and
  user prefs are unique (`docs/31-observability.md`).
- **k3s etcd** — see "etcd Snapshots" below + "k3s cluster recovery".
- **Authentik / Mealie (in-cluster Postgres)** — nightly `pg_dump` CronJobs
  (`kubernetes/apps/{authentik,recipes}/pg-dump.yaml`, 7-dump rotation) write to
  the pg-dump PVs mounted at `…:/backups-apps/{authentik,mealie}` (the
  `/export/backups-apps` NFS export = `tank/backups/apps/<app>`), so each dump
  rides `tank/backups → archive` **and** the restic B2 offsite walk (docs/42);
  staleness alerts `AuthentikBackupStale` / `MealieBackupStale`. The Postgres
  zvols themselves ride `ssd/appdata → archive` (crash-consistent); prefer the
  logical dump for restores.
- **Nextcloud** — the nightly `pg_dump` lands under `tank/backups/apps/nextcloud`
  (rides `tank/backups → archive` + restic B2); the app/postgres zvols ride
  `ssd/appdata → archive`, and the bulk library on `tank/nextcloud-data` has its
  own `archive` replication. Restore per `docs/35-nextcloud.md`.
- **Immich** — the nightly `pg_dump` lands under `tank/backups/apps/immich`
  (rides `tank/backups → archive` + restic B2); the app/postgres zvols ride
  `ssd/appdata → archive`, and the photo library on `tank/immich-data` has its
  own `archive` replication. Restore per `docs/36-immich.md`.
- **wg-easy / Hermes / Hindsight** — NFS-backed state on `ssd/appdata` rides
  `ssd/appdata → archive`; restore file-wise like Grafana above (see
  `docs/38-wireguard-vpn.md`, `docs/37-hermes.md`).
- **Home Assistant** — full-VM recovery rides the monitored nightly vzdump of the
  HAOS VM (.154) via `VzdumpBackupStale`; HAOS built-in backups
  (`docs/24-home-assistant-deployment.md`) are an unmonitored best-effort
  convenience for granular config restore.

## etcd Snapshots

k3s's built-in scheduled snapshots are active on every server node
(12-hour cadence, retention 5, `/var/lib/rancher/k3s/server/db/snapshots/`),
plus `task k3s:backup` for on-demand ones. Restore procedure: `k3s server
--cluster-reset --cluster-reset-restore-path=<snapshot>` on one server, then
rejoin the others.

**Off-node copy — enabled.** Snapshots live on the server-node disks; losing all
three servers (or the Proxmox hosts under them) would otherwise lose etcd — the
only backup class without an off-node copy. Each server node runs a systemd timer
that mounts an NFS export on `pve-nas-01` (by hostname over TLS, `xprtsec=tls`) and
copies the newest snapshot there, writing an
`etcd_snapshot_last_copy_timestamp_seconds` textfile metric consumed by the
`EtcdSnapshotStale` PrometheusRule (fires when the newest off-node copy is >26h old
— two 12h snapshot cycles — or the metric is absent), mirroring the
`ArchiveBackupStale`/`GitLabBackupStale` pattern.

Enabled with `k3s_etcd_snapshot_offnode_enabled: true` (`group_vars/k3s.yml`); the
companion pieces are all in-repo: the `/export/k3s-etcd` NFS export + `ssd/k3s-etcd`
dataset on `pve-nas-01` (`host_vars/pve-nas-01.yml` — mounted **pseudo-root-relative
as `/k3s-etcd`**, since the fsid=0 root is at `/export`), `nfs_tls` widened to the
whole `k3s` group and ordered **before** the server play so tlshd is up first
(`playbooks/k3s.yml`), `node_exporter_host` on `k3s_servers` (`playbooks/site.yml`)
plus its scrape Endpoints, and the three servers listed as `/32`s in both the
export and the fsid=0 pseudo-root. Defaults (paths, mount options, retention) are
in `ansible/roles/k3s/defaults/main.yml`.

**Activation (operator, in order):** (1) **create the dataset first, encrypted** —
`zfs create -o encryption=aes-256-gcm -o keyformat=passphrase -o keylocation=prompt
-o mountpoint=/mnt/ssd/k3s-etcd ssd/k3s-etcd` on pve-nas-01 (passphrase from
1Password "ZFS Pool ssd Passphrase"; the `ssd` pool root is plaintext, so the
dataset needs its own encryption root — see docs/32 — and its name equals its
encryptionroot so the `zfs-load-key@ssd` boot loop unlocks it automatically),
because `nas_storage` does not create datasets and hard-fails `DATASET_MISSING`
on the next NAS deploy otherwise; (2) deploy `nas_storage` (export + bind), `task k3s:deploy`
(tlshd + the copy timer), and `node_exporter_host` on the servers
(`site.yml --tags node_exporter_host --limit k3s_servers`); Flux reconciles the
scrape Endpoints on merge. Verify with `systemctl status k3s-etcd-snapshot-copy.timer`
on a server and `etcd_snapshot_last_copy_timestamp_seconds` in Prometheus.

## Observability plane is a single-NAS SPOF

Prometheus (150Gi zvol), Loki (75Gi zvol, RF=1, single binary), and Grafana
(NFS-backed PVC) are all pinned to `pve-nas-01` via `esweiss.com/nas`
nodeSelectors — a deliberate storage-locality tradeoff (local ZFS vs replicated
storage). The consequence for DR: **a NAS-node outage takes the entire
observability plane down**, and because Prometheus itself is then gone, the
NAS-dependent alerts (ZFS/temperature, Loki, blackbox) cannot fire — the
monitoring blind spot coincides exactly with the most likely incident.

Mitigation (wired): the kube-prometheus-stack chart's always-firing `Watchdog`
alert is routed through Alertmanager to an **external dead-man's-switch**
(healthchecks.io, `watchdog-heartbeat` receiver in
`kubernetes/infrastructure/observability/kube-prometheus-stack/alertmanager-config.yaml`);
the ping URL is injected via the alertmanager-config `ExternalSecret`. The
external service alarms when the heartbeat *stops* — i.e. when Prometheus,
Alertmanager, the NAS node, or notification egress is down. This is the only
signal that survives a total observability-plane outage, including both DNS
resolvers (on which the SMTP and Discord receivers depend). Remaining manual
step: create the healthchecks.io check and the `Healthchecks Watchdog`
1Password item (field `ping url`, see docs/15) so ESO can render the ping URL.
Optionally `remote_write` a thin critical-alerts shard to an off-NAS target.

---

## Overview

The storage bootstrap process consists of 8 phases:

1. **Pre-flight Checks** - Verify target host and display warnings
2. **Infrastructure Detection** - Scan for existing ZFS pools, datasets, directories
3. **Action Planning** - Determine what needs to be created
4. **Interactive Confirmation** - User approval required before proceeding
5. **Dataset Creation** - Create missing ZFS datasets
6. **Directory Setup** - Create bind mounts, export structure
7. **Service Configuration** - Configure NFS, Samba, MergerFS
8. **Verification** - Validate everything is working

---

## Prerequisites

Before running the bootstrap playbook:

### 1. Hardware Requirements
- Physical disks installed and detected by Linux
- Sufficient RAM for ZFS (minimum 8GB recommended)
- Network connectivity

### 2. ZFS Pools Must Be Created First

**CRITICAL**: The playbook does NOT create ZFS pools. You must create them manually first.

#### Why Manual Pool Creation?

ZFS pool creation requires:
- Choosing RAID level (raidz, raidz2, raidz3, mirror, stripe)
- Selecting specific disks by ID
- Setting pool-level properties (ashift, etc.)
- Making decisions about redundancy vs capacity

These decisions are too critical and hardware-specific to automate safely.

#### Creating ZFS Pools

**Use the canonical pool-creation commands in
[docs/06-zfs.md](06-zfs.md#pool-details) — do not hand-transcribe them here.**
The real `tank` pool is `raidz2` **plus** a `special mirror` (2x NVMe metadata)
**and** an L2ARC `cache` vdev; a `zpool create ... tank raidz2 <disks>` without
those two vdevs would rebuild a materially different, slower pool. docs/06 is the
single source of truth for every pool's exact geometry and by-id device list.

**Required Pools for pve-nas-01:**
- `tank` - Main storage pool (HDDs in raidz2)
- Any other pools defined in `host_vars/pve-nas-01.yml`

### 3. Ansible Environment

Ensure your Ansible environment is set up:

```bash
# Install Ansible collections
cd ansible
ansible-galaxy collection install -r requirements.yml

# Verify 1Password authentication
op account get

# Export required secrets
export SSH_PUBLIC_KEY=$(op read "op://Homelab/SSH Key/public key")
export SAMBA_NAS_PASSWORD=$(op read "op://Homelab/Samba NAS User/password")
```

---

## Running Storage Bootstrap

### Step 1: Verify Current State

Before bootstrapping, understand what exists:

```bash
# Check for existing ZFS pools
ssh pve-nas-01 'zpool list'

# Check for existing datasets
ssh pve-nas-01 'zfs list'

# Check directory structure
ssh pve-nas-01 'ls -la /mnt/ /export/'
```

### Step 2: Run Bootstrap Playbook

```bash
cd ansible

# Run bootstrap (interactive mode - will prompt for confirmation)
ansible-playbook playbooks/bootstrap/storage-bootstrap.yml --limit pve-nas-01
```

### Step 3: Review Detection Results

The playbook will display:

```
INFRASTRUCTURE DETECTION RESULTS

ZFS Kernel Module: LOADED

Existing ZFS Pools:
  - tank

Existing ZFS Datasets:
  tank:
    - tank
    - tank/media
    - tank/share
    (or none found)

Critical Directories:
  /mnt/tank: EXISTS
  /mnt/ssd: MISSING
  /export: MISSING
```

### Step 4: Review Required Actions

```
REQUIRED ACTIONS

ZFS Pools to Create:
  WARNING: tank
  (or: All pools exist)

ZFS Datasets to Create:
  WARNING: tank/media
  WARNING: tank/share
  WARNING: tank/downloads
  (or: All datasets exist)

Directories to Create:
  - /export (if missing)
  - Bind mount targets under /export/
  - Bind source directories (if missing)

Services to Configure:
  - NFS exports (/etc/exports)
  - Samba shares (smb.conf)
  - MergerFS union mounts
```

### Step 5: Confirm or Abort

If pools are missing:
```
CRITICAL WARNING

The following ZFS pools need to be created:
  tank

ZFS POOL CREATION IS NOT AUTOMATED

You must manually create ZFS pools first.

TASK [Abort if pools need creation]
fatal: [pve-nas-01]: FAILED! => {
    "msg": "ZFS pools must be created manually first. See warning above."
}
```

**Action**: Create pools manually, then re-run playbook.

If datasets need creation:
```
CONFIRMATION REQUIRED

This playbook will create the following ZFS datasets:
  - tank/media
  - tank/share
  - tank/downloads
  - tank/proxmox
  - tank/pve

WARNING: This operation will:
  - Create ZFS datasets with configured properties
  - Set mountpoints, compression, recordsize, etc.
  - NOT destroy any existing data
  - NOT format any existing filesystems

Existing datasets will be SKIPPED (safe).

Do you want to proceed? [yes/NO]:
```

**Type exactly:** `yes` (lowercase, then press Enter)

### Step 6: Monitor Execution

The playbook will:

1. **Create ZFS Datasets**
   ```
   TASK [Create missing ZFS datasets]
   changed: [pve-nas-01] => (item=tank/media)
   changed: [pve-nas-01] => (item=tank/share)
   changed: [pve-nas-01] => (item=tank/downloads)
   ```

2. **Create Directory Structure**
   - `/export` (NFS root)
   - Bind mount targets (`/export/media`, `/export/share`, etc.)
   - Bind sources (`/mnt/tank/media`, `/mnt/ssd/appdata`, etc.)

3. **Configure Services**
   - NFS exports → `/etc/exports`
   - Samba shares → `/etc/samba/smb.conf`
   - MergerFS mounts → `/etc/fstab`
   - Media mover → systemd timer

4. **Verify Configuration**
   - ZFS datasets mounted
   - NFS exports active
   - MergerFS unions created

### Step 7: Review Completion Status

```
STORAGE BOOTSTRAP COMPLETE

ZFS Datasets Mounted:
  tank                     yes
  tank/media              yes
  tank/share              yes
  tank/downloads          yes
  tank/proxmox            yes
  tank/pve                yes

NFS Exports:
  Export list for pve-nas-01:
  /export           192.168.0.102,192.168.0.200/29
  /export/media     192.168.0.200/29,192.168.0.154
  /export/share     192.168.0.200/29
  /export/appdata   192.168.0.200/29

MergerFS Mounts:
  /mnt/media  /mnt/nvme/media:/mnt/tank/media

Next steps:
  1. Run postflight verification: task infra:verify
  2. Review ZFS pool health: zpool status
  3. Check SMART disk health: smartctl -H /dev/disk/by-id/...
```

---

## Post-Bootstrap Verification

### 1. Run Verification

```bash
task infra:verify
```

This will check:
- ZFS pool health (tank, ssd, nvme, archive - no DEGRADED/FAULTED)
- ZFS datasets mounted correctly
- MergerFS unions active
- NFS exports responding
- Samba shares configured
- SMART disk health (17 disks: 6 HDD tank + 3 SSD + 4 NVMe + 4 HDD archive)
- Backup jobs configured
- Media mover timer active

### 2. Verify ZFS Pool Health

```bash
ssh pve-nas-01 'zpool status -v'
```

Expected output:
```
  pool: tank
 state: ONLINE
config:

        NAME                                      STATE     READ WRITE CKSUM
        tank                                      ONLINE       0     0     0
          raidz2-0                                ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA18WD1     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0RDDB     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0EJDZ     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0ZZ1R     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0HR34     ONLINE       0     0     0
            ata-ST22000NM000C-3WC103_ZXA0FEGJ     ONLINE       0     0     0

errors: No known data errors
```

### 3. Verify Dataset Properties

```bash
ssh pve-nas-01 'zfs get compression,recordsize,atime,mountpoint tank/media'
```

Should match configured values in `host_vars/pve-nas-01.yml`.

### 4. Test NFS Mounts

After `nfs_tls` hardening the `/export/{media,share,appdata}` exports are scoped
to the k3s CIDRs (.200/29, .220/29) with `xprtsec=tls` **required** (plaintext is
rejected); `/export/media` also has a plaintext read-only entry for HAOS (.154).
General LAN clients are intentionally not exported to, so a plaintext mount by IP
from a laptop fails by design (and a TLS mount by IP fails too — the
`*.esweiss.com` cert has no IP SAN). Verify from an authorized k3s agent, mounting
**by hostname over TLS**:

```bash
# From a k3s agent (an authorized CIDR with tlshd running)
sudo mount -t nfs4 -o xprtsec=tls pve-nas-01.esweiss.com:/media /mnt/test
ls -la /mnt/test
sudo umount /mnt/test

# Or simply confirm the existing PV-backed pods are Running (they mount the same
# exports over TLS): kubectl get pods -A | grep -Ev 'Running|Completed'
```

### 5. Test Samba Access

From a client machine:

```bash
# List shares
smbclient -L //pve-nas-01 -U nas

# Connect to share
smbclient //pve-nas-01/share -U nas
```

---

## Common Scenarios

### Scenario 1: Brand New NAS (Fresh Install)

**Starting Point**: Proxmox installed, disks connected, no ZFS pools

**Procedure**:
1. Create ZFS pools manually (see above)
2. Run storage bootstrap playbook
3. Confirm dataset creation
4. Verify with `task infra:verify`

### Scenario 2: Disaster Recovery (Pool Intact, Datasets Lost)

**Starting Point**: ZFS pool exists but datasets were accidentally destroyed

**Procedure**:
1. Run storage bootstrap playbook (will detect existing pool)
2. Confirm creation of missing datasets
3. Restore data from backups (if needed)
4. Verify with `task infra:verify`

### Scenario 3: Hardware Replacement (Complete Rebuild)

**Starting Point**: New hardware, need to restore from backup

**Procedure**:
1. Install Proxmox on new hardware
2. Install physical disks
3. Create ZFS pools (same names as before)
4. Run storage bootstrap playbook
5. Restore ZFS datasets from backup:
   ```bash
   # Receive ZFS snapshot from backup
   zfs receive tank/media < /path/to/backup/tank-media-snapshot.zfs
   ```
6. Run storage bootstrap to configure services
7. Verify with `task infra:verify`

### Scenario 4: Adding New Storage Pool

**Starting Point**: Existing NAS operational, adding new pool

**Procedure**:
1. Install new physical disks
2. Create new ZFS pool manually
3. Add pool configuration to `host_vars/pve-nas-01.yml`
4. Run storage bootstrap playbook (will only create new datasets)
5. Verify with `task infra:verify`

---

## Safety Features

The storage bootstrap playbook includes multiple safety features:

### 1. Detection Before Action
- Scans for existing pools and datasets
- Shows exactly what will be created
- Never modifies existing infrastructure

### 2. Interactive Confirmation
- Requires user to type "yes" to proceed
- Displays clear warnings about what will happen
- Aborts if confirmation not given

### 3. Idempotent Operations
- Safe to run multiple times
- Skips existing infrastructure
- Only creates what's missing

### 4. No Destructive Operations
- Never creates ZFS pools (manual only)
- Never destroys datasets
- Never formats disks
- Never deletes data
- Only creates missing datasets
- Only creates directories
- Only configures services

### 5. Clear Error Messages
- Fails fast if pools are missing
- Provides actionable guidance
- Links to relevant documentation

---

## Troubleshooting

### Problem: Playbook aborts with "ZFS pools must be created manually"

**Cause**: Required ZFS pools don't exist

**Solution**:
1. Create pools manually (see `docs/06-zfs.md`)
2. Verify pools exist: `zpool list`
3. Re-run bootstrap playbook

### Problem: Dataset creation fails with "dataset already exists"

**Cause**: Dataset exists but wasn't detected

**Solution**:
1. Check dataset list: `zfs list`
2. Verify dataset name matches configuration
3. If dataset exists, playbook should skip it (report as bug if not)

### Problem: Permission denied errors during directory creation

**Cause**: Parent directory doesn't exist or insufficient permissions

**Solution**:
1. Verify ZFS datasets are mounted: `zfs get mounted`
2. Check mount points exist: `ls -la /mnt/`
3. Verify playbook running with `become: true`

### Problem: NFS exports not visible to clients

**Cause**: Firewall rules, NFS server not running, or exports misconfigured

**Solution**:
1. Check NFS server: `systemctl status nfs-kernel-server`
2. Verify exports: `exportfs -v`
3. Check firewall rules
4. Test from NAS: `showmount -e localhost`

---

## Rollback Procedures

### If Dataset Creation Fails Mid-Way

**Situation**: Some datasets created, others failed

**Rollback**:
```bash
# List datasets that were created
zfs list

# Destroy newly created datasets (if needed)
zfs destroy tank/problematic-dataset

# Re-run bootstrap playbook
```

**Note**: Only destroy datasets if they were just created and contain no data.

### If Service Configuration Fails

**Situation**: Datasets created successfully, but NFS/Samba config failed

**Rollback**:
1. ZFS datasets are safe (no rollback needed)
2. Fix configuration issue
3. Re-run bootstrap playbook (will skip dataset creation)

### If You Need to Start Over

**Nuclear Option**: Remove all created infrastructure

**WARNING**: This destroys all data!

```bash
# Export and destroy all NFS/Samba configs
sudo systemctl stop nfs-kernel-server smbd nmbd
sudo mv /etc/exports /etc/exports.old
sudo mv /etc/samba/smb.conf /etc/samba/smb.conf.old

# Unmount MergerFS
sudo umount /mnt/media
sudo umount /export/media

# Destroy datasets (DATA LOSS!)
sudo zfs destroy -r tank/media
sudo zfs destroy -r tank/share
# ... repeat for all datasets

# Destroy pools (DATA LOSS!)
sudo zpool destroy tank

# Now you can start completely fresh
```

---

## Checklist

Use this checklist when performing disaster recovery:

### Pre-Bootstrap
- [ ] Physical disks installed and detected (`ls /dev/disk/by-id/`)
- [ ] Sufficient RAM available (8GB+ for ZFS)
- [ ] Network connectivity working
- [ ] SSH access to NAS established
- [ ] 1Password authenticated (`op account get`)
- [ ] Required secrets exported
- [ ] ZFS kernel module loaded (`lsmod | grep zfs`)
- [ ] ZFS pools created manually (`zpool list`)

### During Bootstrap
- [ ] Reviewed infrastructure detection results
- [ ] Confirmed required actions make sense
- [ ] Provided explicit confirmation ("yes")
- [ ] Monitored dataset creation
- [ ] Reviewed service configuration output
- [ ] Checked for errors in playbook output

### Post-Bootstrap
- [ ] Ran `task infra:verify` successfully
- [ ] Verified ZFS pool health (`zpool status`)
- [ ] Checked dataset properties (`zfs get all`)
- [ ] Tested NFS exports (`showmount -e`)
- [ ] Tested Samba shares (`smbclient -L`)
- [ ] Verified MergerFS mounts (`findmnt -t fuse.mergerfs`)
- [ ] Checked SMART disk health
- [ ] Reviewed backup job configuration
- [ ] Verified media mover timer active

---

## k3s cluster recovery (after hardware loss)

If the k3s cluster itself is gone (all three server VMs lost, corrupt etcd
that can't be restored from snapshot, etc.):

```bash
# 1. Bring up VMs + k3s from Ansible (idempotent; respects existing state).
task k3s:provision-vms
task k3s:deploy

# 2. Fetch the fresh kubeconfig.
task k3s:kubeconfig

# 3. Verify the cluster is Ready.
task k3s:status
```

After step 3 you have a bare k3s cluster with kube-vip API VIP ready, but no
workloads. MetalLB, Traefik, and all other platform components are deployed by
Flux in the next step.

The previous etcd snapshot (if available) contained a snapshot of every
Secret value at snapshot time — but restoring from etcd is uncommon in our
topology. The canonical "restore" path is: reinstall k3s fresh → bootstrap
Flux → let ESO re-sync every Secret from 1Password. The only state NOT in
git is the ZFS zvols that back Postgres (Authentik, Mealie) and the GitLab
repo storage — those are independently backed up via ZFS snapshots /
replication and must be restored before the consuming workload comes up
(see `docs/06-zfs.md`).

## Flux + ESO recovery

After the k3s cluster is healthy:

```bash
# 1. Install flux CLI locally if needed.
task flux:install-cli

# 2. Create the 1Password Connect bootstrap secrets (two hand-managed Secrets).
#    `flux:bootstrap-onepassword` prints the procedure; after `op connect server
#    create` has produced ./1password-credentials.json, the executing sibling
#    `flux:bootstrap-onepassword-apply` mints the Connect token (via `op connect
#    token create` — no vault item exists for it) and creates both Secrets.
#    See docs/29-flux-operations.md for details.
task flux:bootstrap-onepassword
task flux:bootstrap-onepassword-apply

# 3. Optionally pre-delete pre-existing Secrets on a partial recovery.
# If the cluster was rebuilt from scratch (fresh k3s install), nothing
# to delete — skip this step. If you kept the cluster but are re-pointing
# Flux at a clean repo, see docs/29-flux-operations.md §
# "First-time Flux bootstrap: delete pre-existing manually-created Secrets".

# 4. Bootstrap Flux itself. This pushes a commit to the configured branch
# (default main) with the Flux components manifests. The bootstrap token
# is read from 1Password item "Flux GitLab PAT".
task flux:bootstrap

# 5. Watch reconciliation. Expect 5-15 min for first convergence
# (HelmRepository pulls → controllers install → CRDs registered →
# configs reconcile → apps reconcile).
task flux:status
task flux:verify
```

## k3s secrets-encryption status

Encryption is enabled cluster-wide and the initial re-encrypt has completed
(`reencrypt_finished`). For future key rotation on this multi-server cluster
(k3s >= v1.28, so `rotate-keys` does prepare/rotate/reencrypt in one pass),
run the rotation on **one** server only, then restart k3s on the others —
do NOT run the rotation commands independently on each server:

```bash
# On ONE server only:
ssh k3s-srv-nas-01
sudo k3s secrets-encrypt status        # show current state and active key
sudo k3s secrets-encrypt rotate-keys   # full rotation (prepare+rotate+reencrypt)
sudo k3s secrets-encrypt status        # wait for "reencrypt_finished"

# Then restart k3s on the OTHER servers, one at a time:
ssh k3s-srv-laptop-01 sudo systemctl restart k3s
ssh k3s-srv-prec-01 sudo systemctl restart k3s
```

See https://docs.k3s.io/security/secrets-encryption for the upstream guide.

## Related Documentation

- `docs/06-zfs.md` - ZFS administration, pool creation, maintenance
- `docs/07-fileservices.md` - NFS, Samba, MergerFS detailed configuration
- `docs/12-runbooks.md` - Operational procedures
- `docs/19-k3s-deployment.md` - k3s cluster deployment (full workflow)
- `docs/29-flux-operations.md` - Flux day-2 ops (suspend/resume, rotation, rollback)
- `ansible/roles/nas_storage/` - Storage role implementation

---

## Emergency Contacts / Resources

If you need help during disaster recovery:

1. **ZFS Documentation**: https://openzfs.github.io/openzfs-docs/
2. **Proxmox Forums**: https://forum.proxmox.com/
3. **This Repository**: Check issues for similar scenarios
4. **Your Backups**: Always verify you have recent backups before major changes

---

**Last Updated**: 2026-07-21
