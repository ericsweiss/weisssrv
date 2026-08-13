# Disaster Recovery

This document covers disaster recovery scenarios for rebuilding the homelab
from backups / git. It is split into three tracks you may need to combine:

1. **Storage** — rebuild ZFS pools + datasets on the NAS (this doc's original scope).
2. **k3s cluster** — bring up the 9-node k3s cluster from Ansible.
3. **Flux GitOps + secrets encryption** — bootstrap Flux, restore ESO, optionally rotate k3s secrets-encryption keys.

The three tracks correspond to the deployment order in
`docs/19-k3s-deployment.md` and `docs/29-flux-operations.md`; DR is those
guides applied to empty hardware.

## Storage bootstrap

Rebuilding the NAS's datasets, export tree and file services from bare pools is
its own procedure: see **[docs/44-storage-bootstrap.md](44-storage-bootstrap.md)**.
Run it after the ZFS pools exist (docs/06) and before the k3s and Flux tracks
below.

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
rather than creating them. The *bootstrap* flow
([docs/44](44-storage-bootstrap.md)) is the deliberate exception: it creates
missing datasets and directories, but only interactively and after an explicit
confirmation step — pools are never created by either path.

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
intentional exclusions**: `tank/media` and `nvme/media` (replaceable bulk media —
**no backup tier at all beyond same-pool snapshots**, an explicitly accepted
risk), Prometheus/Loki history (config is in git; history accepted-loss), vzdump
VM images (IaC-rebuildable; local + archive copies only), and the Windows VM
(offsite-excluded — it *is* vzdumped nightly, so it has local + archive copies;
only the B2 tier skips it). GFS retention on B2: 7 daily / 2 weekly / 3 monthly
/ 1 yearly, plus a `--keep-last 5` floor so the daily buckets alone cannot walk
every restore point out of a bucket that has no Object Lock. Restore paths in
docs/42 §Restore.

> **Audit artefact NAMES, not just mtimes.** A freshness check that matches
> *any* file in `tank/backups/apps/<app>` passes while the artefact you actually
> need is missing — sidecar files (config copies, secrets dumps) keep the mtime
> current on their own. The collector now counts only files matching a per-app
> artefact glob and `BackupArtifactEmpty` fires on a zero-byte newest artefact,
> but the same rule applies to any manual audit. docs/42 carries the worked
> example.

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

## What vzdump does and does not cover

Two separate exclusions shape what the nightly `vzdump` job into `tank/proxmox`
actually holds. Both matter when you are deciding whether `qmrestore` is a
legitimate recovery path.

**1. App-data zvols are excluded (`vzdump_backup: false` in `hosts.yml`).** The
Authentik/Mealie PostgreSQL, Prometheus, Loki, GitLab-repo and Nextcloud/Immich
zvols, plus the Plex `/config` LXC bind mount (`mp0` → `/mnt/ssd/appdata/plex`,
`backup=0`), are backed up by `archive-backupctl` (`ssd/appdata` → `archive`,
raw ZFS). Without the exclusion they would be captured twice. **`archive-backupctl`
is their sole backup path.** The bulk media zvols
(`tank/{nextcloud,immich}-data`) likewise carry `backup=0` and ride their own
`tank/…-data → archive` replication.

**2. All nine k3s guests are excluded wholesale** (`exclude: [202-207, 222, 223,
227]` on the vzdump job). They are IaC-rebuildable cattle: every byte of
persistent state already lives on zvols covered by archive replication, the
pg-dumps, and restic/B2.

> **Never `qmrestore` a k3s node.** Rolling a weeks-old k3s **server** image back
> rejoins etcd with a stale member and can corrupt quorum. The supported recovery
> is *reprovision* (`task k3s:deploy`) plus an etcd-snapshot restore — see
> [k3s cluster recovery](#k3s-cluster-recovery-after-hardware-loss) and
> [etcd Snapshots](#etcd-snapshots) below.

**Retention gotcha when excluding a guest.** Proxmox runs `prune-backups`
per guest, immediately after that guest's backup — so a guest that is no longer
backed up is never revisited and its existing dumps are never pruned. Excluding a
guest strands its whole dump history on `tank/proxmox` (and, via archsync, on
`archive/proxmox`). Sweep those dumps by hand in the same change:

```bash
sudo ls -lt /mnt/tank/proxmox/dump/vzdump-qemu-<vmid>-*
sudo rm -f  /mnt/tank/proxmox/dump/vzdump-{qemu,lxc}-<vmid>-*
```

then let archsync retention age the archive copy out. With the current exclusion
set swept, `tank/proxmox` holds one dump generation per remaining guest under the
job's normal retention.

**No guest image has an offsite copy.** vzdump is local plus archive only — a
site loss takes both. That is acceptable for the IaC-managed guests, whose
recovery path is reprovision-then-restore-data. The Windows VM (155) is the one
guest whose desktop state is not reproducible from IaC; if anything on it matters,
export it onto an NFS path under `tank/backups/apps/` so it rides the existing
file walk into B2.

## Total site loss — the ordered critical path

The sections below cover each recovery track in isolation. This is the sequence
that ties them together when there is **nothing left on site** (fire / theft /
flood).

**The two things that must survive outside the house:**

1. **The IaC itself.** GitLab (`git.esweiss.com`) hosts the canonical repos AND
   the Terraform HTTP state backends for `terraform/{cloudflare,tailscale,authentik}`.
   Its nightly tarball reaches B2 — but you cannot read B2 without the repo's
   tooling, so the bootstrap copy is the **read-only GitHub mirror**
   (`github.com/ericsweiss/weisssrv`). The mirror carries this repo only: **not**
   `weisssrv-lib` / `weisssrv-app-template` history, not issues/MRs, not the
   container registry, and not Terraform state. Those come back from the GitLab
   tarball once GitLab itself is running.
2. **The credentials.** The 1Password vault (survives independently) plus an
   **offline** copy of `restic_repo_password` — without it the entire B2 repo is
   an inert blob (`docs/15-credential-rotation.md`).

**Order (rough RTO per track, assuming replacement hardware is on hand):**

| # | Step | Depends on | Rough RTO |
|---|---|---|---|
| 1 | Install Proxmox on the replacement hosts, restore `/etc/pve` from the `pve-cluster` archive once B2 is readable (or rebuild the cluster and re-add nodes) | hardware | 2–4 h |
| 2 | Clone the IaC from the **GitHub mirror**; sign in to 1Password; restore the offline `restic_repo_password` | GitHub + 1Password | 15 min |
| 3 | Create the ZFS pools by hand (never automated — docs/06), then run the [storage bootstrap](44-storage-bootstrap.md). **`nvme` lives on partition 4 of the Proxmox boot disk you installed in step 1** — create it against `...-part4`, never the whole device | 1, 2 | 1–2 h |
| 4 | Restore from B2: `restic-offsitectl restore <source>` for `backups`, `share`, `appdata`, `k3s-etcd`, and the two data zvol trees | 3 | hours–days (data-volume bound; 621 GB raw at review time) |
| 5 | Rebuild the k3s VMs + cluster (`task k3s:deploy`), restoring etcd from the off-node snapshot if a same-identity cluster is wanted. **Never `qmrestore` a k3s guest** — no image exists, and a stale server image corrupts etcd quorum | 3, 4 | 1–2 h |
| 6 | Bootstrap Flux + ESO (the two manual secrets — `docs/29-flux-operations.md`), let Flux reconcile everything in `kubernetes/` | 5 | 30–60 min |
| 7 | Rebuild the VM/LXC apps via their playbooks, then replay logical dumps from the restored `backups/apps/<app>` (`gitlab-backup restore`, `pg_restore`, HAOS tar import — the HA tars need `backup_encryption_key`) | 4, 6 | 2–4 h |
| 8 | Re-point DNS: `terraform/cloudflare` state is inside the restored GitLab, so restore GitLab (step 7) **before** any terraform apply | 7 | 30 min |

Bare-metal guest images are deliberately **not** in B2 (vzdump is local +
archive only), so step 7 is "reprovision via Ansible, then restore data" — not
"restore images". That is a defensible trade given the IaC coverage, but it is
the reason the GitHub mirror is a hard dependency rather than a convenience.

**Nothing above is proven until it is drilled** — see docs/42 § "Restore drills".

## Restore Procedures

Two paths feed the `archive` pool (see "What vzdump does and does not cover"
above): the nightly
`vzdump` of every VM/CT into `tank/proxmox`, and `archive-backupctl`'s direct
raw/encrypted replication of `tank/{share,backups,nextcloud-data,proxmox,
immich-data}` + `ssd/{appdata,databases,k3s-etcd}`. **Everything in `archive` is raw
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
                                          # immich-data|appdata|databases|
                                          # k3s-etcd|all

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
- **Homarr** — the SQLite DB on the NFS export `/appdata/homarr` rides
  `ssd/appdata → archive`. Restore file-wise exactly like Grafana:
  `archive-backupctl restore appdata`, `zfs load-key` + `zfs mount` the restore
  dataset, scale the Deployment to 0, copy `db.sqlite` back as `1000:2000`, scale
  up. Dashboard layout is reproducible by hand; the SQLite file is what carries
  the encrypted integration credentials, which are unreadable without
  `secret-encryption-key` from the **Homarr SSO** 1Password item
  (`docs/41-homarr.md`).
- **Bar Assistant** — same file-wise recipe against `/appdata/bar-assistant`. Only
  the SQLite file is load-bearing: the Meilisearch index is regenerable from it
  (`docs/22-recipes-deployment.md`).
- **Home Assistant** — full-VM recovery rides the monitored nightly vzdump of the
  HAOS VM (.154) via `VzdumpBackupStale`. The HAOS built-in backups
  (`docs/24-home-assistant-deployment.md`) are **no longer unmonitored**: they
  land on `tank/backups/apps/home-assistant` over the HAOS network-storage mount,
  ride archsync into B2, and are watched by
  `BackupArtifactStale{app="home-assistant"}` (docs/42 superseded the old
  best-effort framing). They are **encrypted** — restoring them needs the
  emergency-kit key stored as `backup_encryption_key` on the "Home Assistant API
  Token" 1Password item (`docs/15-credential-rotation.md`); without it the tars
  are unusable, so verify that field is populated as part of the restore drill.
- **PVE cluster identity (`/etc/pve`)** — pmxcfs is NOT captured by vzdump
  (which backs up guests, not the cluster filesystem). `pve-cluster-backup.timer`
  on pve-nas-01 tars it nightly to `tank/backups/apps/pve-cluster`, so
  `user.cfg` (users/ACLs/API tokens), `corosync.conf` and `priv/` (cluster CA +
  node certs + `authkey.pub`) ride archsync into B2. Restore by unpacking the
  tar and copying files back into `/etc/pve` on a rebuilt cluster — note the
  archive holds private key material, so it is root-only 0600 at every hop.

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
in weisssrv-lib `ansible_collections/weisssrv/infra/roles/k3s/defaults/main.yml`.

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

## Related documentation

- [docs/44-storage-bootstrap.md](44-storage-bootstrap.md) - NAS storage bootstrap manual
- [docs/06-zfs.md](06-zfs.md) - ZFS administration, pool creation, maintenance
- [docs/07-fileservices.md](07-fileservices.md) - NFS, Samba, MergerFS configuration
- [docs/12-runbooks.md](12-runbooks.md) - Operational procedures
- [docs/19-k3s-deployment.md](19-k3s-deployment.md) - k3s cluster deployment (full workflow)
- [docs/29-flux-operations.md](29-flux-operations.md) - Flux day-2 ops (suspend/resume, rotation, rollback)
- [docs/42-offsite-backup.md](42-offsite-backup.md) - offsite restic/B2 tier and restore drills
- `weisssrv.infra.nas_storage` (weisssrv-lib) - Storage role implementation
- External: [OpenZFS docs](https://openzfs.github.io/openzfs-docs/), [Proxmox forums](https://forum.proxmox.com/)
