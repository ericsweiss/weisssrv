# ZFS Storage Configuration

This document covers ZFS pool and dataset configuration on the NAS (pve-nas-01).

## ZFS Pools

The NAS has four ZFS pools with different performance characteristics:

| Pool | Type | Capacity | Use Case |
|------|------|----------|----------|
| `tank` | raidz2 (6x 22TB) + special device + cache | ~88TB usable (132TB raw) | Bulk media storage |
| `ssd` | raidz1 (3x 4TB SSD) | ~8TB usable / 10.9TB raw | App data, databases |
| `nvme` | Partition 4 of the Proxmox boot NVMe (Samsung 990 PRO 4TB — shared device) | 2.27TB pool (per `zpool list nvme`) | Hot downloads, fast scratch |
| `archive` | raidz1 (4x 6TB) | ~18TB usable (3x 6TB data) | Cold backups |

## Pool Details

### tank (Primary Media Pool)

**Configuration**:
- **Data VDEVs**: raidz2 with 6x ST22000NM000C (22TB enterprise drives)
- **Special Device**: 2x 2TB NVMe mirror (CT2000P5PSSD8, metadata + small blocks)
- **L2ARC Cache**: 1x 4TB NVMe (Samsung 990 PRO with Heatsink)

**Properties**:
```bash
atime: off
compression: zstd
recordsize: 1M (tank/media), 128K (tank/share, default)
```

**Datasets**:
- `tank/media` - Media library (mounted `/mnt/tank/media`)
- `tank/share` - General shared storage (mounted `/mnt/tank/share`)
- `tank/proxmox` - Proxmox VM backup target (mounted `/mnt/tank/proxmox`)
- `tank/pve` - Ephemeral Proxmox VM/LXC images (mounted `/mnt/tank/pve`)
- `tank/backups` - General backup target (encryption root; replicated to `archive`)
- `tank/backups/apps` - Per-app logical-dump landing zone, one subdirectory per
  app, each NFS-exported to just that app's client. A child of `tank/backups`, so
  it **inherits** that encryption root rather than being one itself, and rides
  the same recursive raw `zfs send -w`. Created by hand (`nas_storage` never
  creates datasets) — the sequence is in docs/44 § Adding a dataset to a live NAS.
- `tank/nextcloud-data` - Nextcloud data (encryption root, replicated)
- `tank/immich-data` - Immich data (encryption root, replicated). Holds the
  `tank/immich-data/disk` zvol — the 2 TB **sparse** photo library for the Immich
  VM (.157), ext4, mounted `/mnt/immich-data` on the guest. Inherits the parent's
  aes-256-gcm encryption and rides the existing archive SRC_LIST entry (docs/36).

### ssd (App Data Pool)

**Configuration**:
- **Data VDEVs**: raidz1 with 3x Samsung 870 EVO 4TB

**Properties**:
```bash
atime: off
compression: lz4 (ssd/appdata; pool default zstd)
# Databases live on zvols — volblocksize is set at zvol creation, not recordsize
```

**Datasets**:
- `ssd/appdata` - Application persistent data (per-app children:
  authentik, gitlab, loki, mealie, nextcloud, prometheus). The `prometheus` and
  `loki` children are dropped from the recursive archive send
  (`nas_storage_archive_backup_exclude`): both TSDBs are re-derivable and huge,
  and they are already out of restic. An excluded child keeps no source-side
  `archsync-*` snapshot, and the stream leaves whatever archive-side copy it
  already had in place: the run warns, names it and counts it in
  `archive_backup_excluded_orphans`, and reclaiming that space is an explicit
  `zfs destroy -r` (docs/12 § ArchiveBackupFailed / ArchiveBackupStale). The
  Nextcloud VM
  (156) adds `ssd/appdata/nextcloud/app` (20G, /mnt/nextcloud-app: compose +
  html/config + backups) and `ssd/appdata/nextcloud/postgres` (16G, PGDATA).
  Its bulk user data is a 2T **sparse** zvol `tank/nextcloud-data/disk` under the
  encrypted `tank/nextcloud-data` root (already in the archive SRC_LIST).
- `ssd/databases` - Empty; an encryption root kept for shape, deliberately out
  of the archive and restic chains
- `ssd/pve` - GitLab VM disks (encryption root; intentionally NOT replicated by
  `archive-backupctl` — the GitLab repos zvol under `ssd/appdata` is the backed-up copy)
- `ssd/k3s-etcd` - Off-node k3s etcd snapshot copies (encryption root; exported as
  `/export/k3s-etcd` to the k3s servers over TLS — see docs/07 and docs/17)

### nvme (Fast Scratch Pool)

**Configuration**:
- **Data VDEVs**: Single Samsung 990 PRO 4TB NVMe

**Properties**:
```bash
atime: off
compression: zstd
```

**Datasets**:
- `nvme/media` - MergerFS hot tier for downloads and new media (mounted `/mnt/nvme/media`)
- `nvme/fast` - Transcode scratch (mounted `/mnt/nvme/fast`)
- `nvme/pve` - Ephemeral VM/LXC images (mounted `/mnt/nvme/pve`; Proxmox-managed)

### archive (Cold Storage Pool)

**Configuration**:
- **Data VDEVs**: raidz1 with 4x ST6000NM0024 (6TB enterprise drives)

**Properties**:
```bash
atime: off
compression: zstd
canmount: off
com.sun:auto-snapshot: false  # Disable automatic snapshots
```

**Datasets**:
- `archive/backups` - Long-term backup retention
- `archive/proxmox` - Long-term Proxmox VM backup retention

> The canonical dataset inventory is the encryption table in
> [docs/47](47-security-posture.md) § At Rest and the backup section below —
> these per-pool lists are a quick reference.

## Current Cluster Configuration

This section documents the exact commands to recreate the ZFS pools on pve-nas-01.

### Tank Pool Creation

The tank pool uses raidz2 (dual parity) for reliability with 6x 22TB Seagate enterprise drives.

```bash
# Create tank pool with raidz2
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/tank \
             tank raidz2 \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA18WD1 \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0RDDB \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0EJDZ \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0ZZ1R \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0HR34 \
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0FEGJ \
             special mirror \
             /dev/disk/by-id/nvme-CT2000P5PSSD8_2334429F310B \
             /dev/disk/by-id/nvme-CT2000P5PSSD8_2334429F32EE \
             cache \
             /dev/disk/by-id/nvme-Samsung_SSD_990_PRO_with_Heatsink_4TB_S7DSNJ0Y608388N

# Verify pool creation
zpool status tank
zpool list -v tank
```

**Pool Properties**:
- `ashift=12`: 4K sector size (optimal for modern drives)
- `acltype=posixacl`: POSIX ACL support
- `compression=zstd`: Default compression (can be overridden per dataset)
- `normalization=formD`: Unicode normalization for consistent filenames
- `relatime=on`: Reduces atime writes while maintaining some tracking
- `xattr=sa`: Store extended attributes in system attribute table (better performance)
- `mountpoint=/mnt/tank`: Pool mounts at /mnt/tank

**Capacity**: With raidz2, usable capacity is approximately 88TB (22TB × 4 data drives)

### SSD Pool Creation

The ssd pool uses raidz1 (single parity) for performance with 3x 4TB Samsung SSDs.

```bash
# Create ssd pool with raidz1
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/ssd \
             ssd raidz1 \
             /dev/disk/by-id/ata-Samsung_SSD_870_EVO_4TB_S757NL0Y902062Z \
             /dev/disk/by-id/ata-Samsung_SSD_870_EVO_4TB_S757NL0Y902052X \
             /dev/disk/by-id/ata-Samsung_SSD_870_EVO_4TB_S757NL0Y901976N

# Verify pool creation
zpool status ssd
zpool list -v ssd
```

**Pool Properties**:
- `ashift=12`: 4K sector size
- Same base properties as tank pool
- Databases live on zvols (volblocksize set at zvol creation, not recordsize)

**Capacity**: With raidz1, usable capacity is approximately 8TB (4TB × 2 data drives)

### NVMe Pool Creation

The nvme pool is a single-partition pool on the Samsung 990 PRO 4TB.

> **DANGER — this device is pve-nas-01's Proxmox boot disk. The pool lives on
> partition 4 only.** `p1` is the BIOS boot partition, `p2` is the 1 GB ESP
> (`/boot/efi`) and `p3` is the LVM PV carrying the `pve` volume group
> (`pve-root`, `pve-swap`/cryptswap, and the `pve-data` thin pool with the
> local-lvm guest disks). Only `p4` (PARTLABEL `nvme-zfs`) is the `zfs_member`.
> Running `zpool create` against the whole device destroys the hypervisor
> install — a live hazard during a rebuild, because docs/17 § Total Site Loss
> installs Proxmox onto this same disk before sending the operator here.

```bash
# Create nvme pool (single partition, no redundancy)
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/nvme \
             nvme \
             /dev/disk/by-id/nvme-Samsung_SSD_990_PRO_4TB_S7KGNU0YA04137V-part4

# Verify pool creation — the vdev must read ...-part4, never the bare device
zpool status nvme
zpool list -v nvme
```

**Pool Properties**:
- Single partition on a shared device (no redundancy)
- Optimized for hot data and fast scratch space
- Same base properties as tank pool

**Capacity**: ~2.27TB pool (single-disk, no parity overhead) — the live figure
per `zpool list nvme`, which is the source of truth. It is below the 4TB
device's nominal size because partitions 1-3 hold the Proxmox install (above).

**WARNING**: Single device pool has no redundancy. Suitable for temporary/cache data only.

### Archive Pool Creation

The archive pool uses raidz1 with 4x 6TB Seagate enterprise drives for long-term cold storage.

```bash
# Create archive pool with raidz1
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -O canmount=off \
             -O com.sun:auto-snapshot=false \
             -m /mnt/archive \
             archive raidz1 \
             /dev/disk/by-id/ata-SEAGATE_ST6000NM0024_Z4D2BDD2 \
             /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JCL6 \
             /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1RQSM \
             /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JQBA

# Verify pool creation
zpool status archive
zpool list -v archive
```

**Pool Properties**:
- `canmount=off`: Pool itself not mounted (only datasets)
- `com.sun:auto-snapshot=false`: Disable automatic snapshots for cold storage

**Capacity**: With raidz1, usable capacity is approximately 18TB (6TB × 3 data drives)

**Drive mapping** (source of truth for which physical drive backs each
`archive-N` raidz1 member; the pool is built directly on the by-id devices,
feeds `nas_storage_smartd_archive_disks`, and guides resilver ops):

| Archive member | Physical drive (by-id) | Notes |
|----------------|------------------------|-------|
| archive-1 | ata-SEAGATE_ST6000NM0024_Z4D2BDD2 | |
| archive-2 | ata-ST6000NM0024-1HT17Z_Z4D1JCL6 | |
| archive-3 | ata-ST6000NM0024-1HT17Z_Z4D1RQSM | |
| archive-4 | ata-ST6000NM0024-1HT17Z_Z4D1JQBA | |

### local-ssd (Compute Node Storage)

**Status**: Active on all compute nodes (pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01)

The `local-ssd` pool provides local VM/container storage on compute nodes (all Proxmox hosts except the NAS). Each compute node has a 1TB Samsung 870 EVO SSD configured as local-ssd.

**Configuration** (verified on pve-opt-03):
- **Device**: 1x Samsung 870 EVO 1TB (ata-Samsung_SSD_870_EVO_1TB_S6PTNS0Y900757T)
- **Capacity**: ~900GB usable
- **Redundancy**: None (single device)
- **Use Case**: Local storage for VMs/containers on compute nodes

**Why local-ssd for compute nodes?**

1. **Proxmox HA Requirements**: ZFS pools required on all nodes for replication and failover
2. **Performance**: Better than LVM thin (compression, checksumming, snapshots)
3. **Stateless Workloads**: Compute nodes run stateless/replicated workloads:
   - K3s agents: State in etcd (replicated across servers)
   - Pods: Automatically rescheduled on node failure
   - DNS/SMTP: Multiple instances or retry mechanisms
4. **Cost-Effective**: 1TB SSD per node is sufficient for local workloads

**Pool Properties** (optimized for VM workloads):

```bash
ashift=12           # 4K sector alignment
compression=lz4     # Low-latency compression (better than zstd for VMs)
atime=off           # Reduce write amplification
xattr=sa            # System attribute storage (3x faster, shows as "on" in ZFS 2.3+)
autotrim=on         # SSD longevity
normalization=formD # Unicode normalization
acltype=posixacl    # POSIX ACL support
```

**Why lz4 instead of zstd?**
- VM workloads are latency-sensitive (random I/O patterns)
- lz4 has ~10x faster decompression than zstd
- Near-zero CPU overhead vs zstd's higher cost
- Industry standard for VM storage (Proxmox defaults to lz4)

**Creation Commands**:

```bash
# Step 1: Identify the disk
ls -l /dev/disk/by-id/ | grep -i samsung

# Step 2: Wipe any existing filesystem signatures
sudo wipefs -a /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER
sudo sgdisk --zap-all /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER

# Step 3: Create local-ssd pool
sudo zpool create -f -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  -O xattr=sa \
  -O normalization=formD \
  -O acltype=posixacl \
  local-ssd \
  /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER

# Step 4: Enable autotrim for SSD longevity
sudo zpool set autotrim=on local-ssd

# Step 5: Verify pool creation
sudo zpool status local-ssd
sudo zpool list local-ssd
sudo zfs list local-ssd

# Step 6: Register as Proxmox storage (via UI or CLI)
# Via Proxmox UI: Datacenter → Storage → Add → ZFS
# Or via CLI:
sudo pvesm add zfspool local-ssd --pool local-ssd --content images,rootdir

# Step 7: Verify Proxmox recognizes the storage
sudo pvesm status
```

**Current Deployment**:

| Host | Status | Device | Pool Size | VMs/CTs |
|------|--------|--------|-----------|---------|
| pve-laptop-01 | Active | Samsung 870 EVO 1TB | ~900GB | k3s-srv-laptop-01 (VM 223), k3s-agt-laptop-01 (VM 203) |
| pve-opt-01 | Active | Samsung 870 EVO 1TB | ~900GB | k3s-agt-opt-01 (VM 204) |
| pve-opt-02 | Active | Samsung 870 EVO 1TB | ~900GB | k3s-agt-opt-02 (VM 205) |
| pve-opt-03 | Active | Samsung 870 EVO 1TB | ~900GB | k3s-agt-opt-03 (VM 206) |
| pve-prec-01 | Active | Samsung 870 EVO 1TB | ~900GB | k3s-srv-prec-01 (VM 227), k3s-agt-prec-01 (VM 207) |

## ZFS Scrubs

Regular scrubs verify data integrity and repair any errors.

### Current Schedule

- **NAS pools (tank/ssd/nvme/archive)**: the `nas_storage` role enables the
  zfsutils-linux `zfs-scrub-<schedule>@<pool>.timer` template units per pool
  (`nas_storage_zfs_scrub_schedule`, default `monthly`; gated on `nas_storage_zfs_scrub_enabled`).
  The `archive` pool's timer is toggled by `archive-backupctl plug/unplug`
  so an exported pool is never scrub-targeted.
- **Compute `local-ssd` pools (5 hosts)**: deliberately rely on the
  zfsutils-linux default second-Sunday scrub cron
  (`/etc/cron.d/zfsutils-linux`) — no role manages scrub timers on compute
  hosts.

```bash
# Check scrub status (sudo on Proxmox hosts)
sudo zpool status -v tank

# Manually start scrub
sudo zpool scrub tank

# Check all pools
for pool in tank ssd nvme archive; do
  echo "=== $pool ==="
  sudo zpool status $pool | grep -E "state:|scan:"
done
```

Check the active schedule with: `systemctl list-timers '*scrub*'`

## Snapshots

The `com.sun:auto-snapshot` property controls which datasets are
auto-snapshotted: enabled by default, and set to `false` on the `archive` pool,
which holds replicated snapshots already.

## Performance Tuning

### Record Size

`tank/media` is set to 1M for large sequential media. Everything else, including
`tank/share`, uses the 128K pool default.

### Compression

The four NAS pool roots default to `zstd`; `ssd/appdata` and the compute-node
`local-ssd` pools use `lz4` (see the per-pool sections above for why):

```bash
# Check compression ratio
sudo zfs get compressratio tank/media

# View compression stats for all datasets
sudo zfs get compression,compressratio
```

### ARC (Adaptive Replacement Cache)

The compute Proxmox hosts carry a group-wide **8 GiB ARC cap**
(`zfs_arc_cap_max_bytes` in `group_vars/proxmox.yml`, applied by the
`zfs_arc_cap` role on every non-NAS host). That default is sized for the
**62 GiB `pve-prec-01`**, where it keeps the ARC from colliding with VM 207's
VFIO-pinned, non-swappable GPU RAM — see [docs/43](43-gpu-passthrough.md).

The four 14-15 GiB opt/laptop hosts **override it to 2 GiB** once, in
`group_vars/small_ram_hosts.yml` (pve-opt-01/02/03 + pve-laptop-01): on a host
that small an 8 GiB ceiling is not self-limiting (one was measured at 4.3 GiB
ARC with 2 GiB available and 1.7 GiB swapped), so the cap has to be set below
what the host can actually spare.

The NAS carries its own, larger cap — see *NAS memory management* below.

Monitor ARC usage:

```bash
# ARC stats
sudo arc_summary

# Current ARC size
awk '$1 ~ /^(c|c_max|size)$/' /proc/spl/kstat/zfs/arcstats
```

## Backup Strategy

### Archive Replication

`archive-backupctl` (on pve-nas-01, nightly timer) replicates the source datasets
to the `archive` pool as **raw, encrypted** `zfs send -w` streams, so the archive
copies are encrypted at rest under each source's own key — the archive never
loads a key. Replicated datasets (`nas_storage_archive_backup_sources` in
`host_vars/pve-nas-01.yml` is the source of truth; the role renders it as the
script's `SRC_LIST`): `tank/{share,backups,nextcloud-data,proxmox,immich-data}` and
`ssd/{appdata,k3s-etcd}`. Retention: the newest few `archsync`
snapshots plus a grandfather monthly.

Do **not** run manual `zfs send | zfs receive` into `archive/*` — it breaks the
raw incremental chain `archive-backupctl` maintains and forces a full re-seed.
Restore + key handling: `docs/17-disaster-recovery.md` (Restore Procedures) and
`docs/32-zfs-encryption.md`.

### Proxmox Backup

Proxmox VMs/LXCs back up nightly (`vzdump`, `all`) to:
- `tank/proxmox` (primary; itself replicated raw/encrypted to `archive/proxmox`)
- App-data passthrough zvols carry `backup=0`, so vzdump skips them — they are
  backed up via `ssd/appdata` → archive instead, not double-stored in the VM
  image (see `docs/17-disaster-recovery.md` "Backup Dedup").

The Proxmox `storage.cfg` entry (`tank-proxmox`) and the nightly vzdump job
are Ansible-managed by the `proxmox_backup` role (config in
`host_vars/pve-nas-01.yml`). The codified storage entry mounts by hostname
with `vers=4.2,xprtsec=tls`. See weisssrv-lib `ansible_collections/weisssrv/infra/roles/proxmox_backup/README.md`.

## Ansible Management

ZFS configuration is managed by the `nas_storage` role.

### Deploying ZFS Configuration

```bash
# Deploy ZFS settings (storage.yml defines no tags — run untagged, or use task storage:deploy)
ansible-playbook -i ansible/inventories/prod ansible/playbooks/storage.yml

# Verify configuration
ansible -i ansible/inventories/prod pve-nas-01 -m shell -a "zfs get all tank/media"
```

## Troubleshooting

### Pool Degraded

If a disk fails:

```bash
# Check pool status
sudo zpool status tank

# Replace failed disk
sudo zpool replace tank old-disk-id new-disk-id

# Monitor resilver progress
sudo zpool status tank
```

### NAS memory management

pve-nas-01 has ~124 GiB usable. Guest maximums total ~76 GiB (gitlab 16, immich
12, windows 8, nextcloud 8, k3s agent 24, k3s server 8, plus the plex and
immich-ml LXC ceilings at 8 each) and the ARC cap adds 12 GiB, so a worst-case
commit is ~104 GiB. Guest backups are staggered across the night window (immich
01:00, nextcloud 01:30, gitlab 02:00, vzdump 03:30) so their peaks never stack,
and the swap reset at 07:00 closes the window.

Five levers keep it that way. They are guards: they cost nothing while there is
headroom, and they stop a new workload re-creating the swap ratchet this host is
prone to.

| Lever | Set in | What it does |
|---|---|---|
| ARC cap `nas_storage_zfs_arc_max_bytes` = 12 GiB | `host_vars/pve-nas-01.yml` | Uncapped ARC takes about half of RAM. The `nas_storage` role renders `/etc/modprobe.d/zfs.conf` and notifies `update-initramfs`, because the pools import from the initramfs. About 2.5 GiB of the cap is L2ARC headers for the 3.6 TB cache device. |
| `vm.swappiness = 1` (`nic_tuning_vm_swappiness`) | `host_vars/pve-nas-01.yml` | Only host in the fleet that sets it. Higher values park cold anon pages in swap even with free RAM, and Linux never pages them back on its own, so swap ratchets upward. At 1 the kernel reclaims from ARC instead. |
| Daily swap reset (`nas_storage_swap_clean_enabled`, `nas_storage_swap_clean_schedule` = 07:00) | `host_vars/pve-nas-01.yml` | `swap-clean.timer` runs `/usr/local/sbin/swap-clean.sh`: shrink ARC for headroom, `swapoff -a` / `swapon -a`, restore ARC, and only when the freed RAM comfortably covers the swap in use. Device-agnostic, so it works against `/dev/mapper/cryptswap`. |
| NAS k3s VM sizes (`vm_memory` 24576 agent / 8192 server) | `hosts.yml` | The agent carries Prometheus, Loki and the app Postgres zvols and is CI-eligible, so it is sized for both. The etcd server has deliberate margin: etcd is latency-sensitive and its degradation takes the cluster with it. A change needs drain, `qm shutdown` / `qm start`, uncordon, one server at a time. |
| CI job pods land here last | `kubernetes/apps/gitlab-runner-privileged/release.yaml` | The node's `esweiss.com/nas=true:PreferNoSchedule` taint makes it least-preferred, so the scheduler exhausts every other agent first. To tighten, raise the taint to `NoSchedule` rather than adding an affinity exclusion. |

If the ARC-shrink headroom cannot cover the swap,
`nas_storage_swap_clean_stop_guests` gracefully shuts down heavy guests from an
ordered candidate list (immich, then GitLab, then nextcloud), only as many as
needed, does the swapoff, and restarts every guest it stopped. A cleanup trap
fires on any exit, so a crash mid-reclaim can never strand a guest. Stops are
always `qm shutdown`; a guest that will not stop within its timeout aborts the
reclaim instead of being forced. Each stop releases the guest's swap as well as
its RAM, so the headroom target is re-read after every one.

The escalation refuses a goal it cannot reach. If stopping every running
candidate still would not cover the swap, it stops no guest, records
`swap_clean_skip_reason_info{reason="escalation unreachable"}` with
`swap_clean_last_run_success` 0, and exits. `SwapCleanFailed` is the alert; the
reason label says which arm it was.

A pre-flight check skips the whole run when one of
`nas_storage_swap_clean_conflicting_units` is still active, so a nightly backup
that never finishes stops the reset without failing it. `SwapCleanSkipped`
watches for that: it fires when every run across three days skipped, and the
alert's `reason` label names the unit that blocked it.

#### Kernel 192-byte slab leak

The levers above manage reclaimable pressure. The merged 192-byte slab leaks
about 2.7-4 GiB/day on the NAS and 230-380 MB/day on every other Proxmox host.
The tenant is `skbuff_ext_cache`: about 0.5% of the skb extensions
`br_nf_pre_routing` allocates per bridged frame are never freed, which is ~4
GB/day at this host's NFS volume. Only a reboot reclaims it.

The mitigation is `proxmox_firewall_nftables: true`, which renders `nftables: 1`
into the node's `host.fw`. That option selects the `proxmox-firewall` package,
which programs nftables and takes the bridge-netfilter hook out of the node's
bridged path. The package must be installed on the node first, and the choice is
per node, so it goes in `host_vars`. Validate it on one opt host before the NAS
and keep the reboot pager until a week of flat slab proves it.

- `HostSlabLeakSuspected` (SUnreclaim minus ARC above 16 GiB for 24 h) is the
  reboot pager. It fires roughly weekly, and firing means schedule the NAS
  reboot window.
- `slub_nomerge` must be on pve-nas-01's kernel cmdline, so that `slabtop -s c`
  names the cache directly instead of the merged `:0000192` alias (which
  `/proc/slabinfo` displays under a `file_lock_cache` name that is not the
  culprit). The flag is applied by hand, not by Ansible. A host rebuild, or a
  `proxmox-boot-tool refresh` against a regenerated cmdline, drops it and blinds
  `HostSlabCacheGrowing`; `SlabinfoCollectorDegraded` is the witness.
- `nas_storage_nfs_disable_delegations` stays `false`: turning it on costs the
  *arr apps 15-30 s SQLite stalls and does not slow the leak.
- Retire the reboot cadence when the running kernel carries the
  br_netfilter/skb_ext fix and a week of flat
  `node_memory_SUnreclaim_bytes - node_zfs_arc_size` in Grafana confirms it. Per
  cache, that is `node_slab_object_bytes{cache="skbuff_ext_cache"}` from the
  `node_exporter_host` role's slabinfo textfile collector, enabled on this host
  by `node_exporter_host_slabinfo_collector`. `HostSlabCacheGrowing` is the
  leading arm that reads it. The series appears once the `weisssrv.infra` pin
  carries the collector and `task infra:deploy` has run.
- `SlabinfoCollectorDegraded` is the witness for that arm. A dead timer, an
  unreadable `/proc/slabinfo` or a renamed cache would otherwise silence
  `HostSlabCacheGrowing` with no other signal.
- Arming `slub_nomerge` on the compute hosts too, to settle the fleet-wide
  230-380 MB/host/day figure, is an owner decision
  ([docs/16](16-next-steps.md)).

Diagnosis history: [docs/34](34-bond-mac-flapping.md). Tracking:
[docs/16](16-next-steps.md).

```bash
# View ARC size + hit ratio
awk '$1 ~ /^(c|c_max|size)$/' /proc/spl/kstat/zfs/arcstats
sudo arc_summary | grep "Hit Rate"

# Limit ARC (temporary, until reboot)
echo 12884901888 | sudo tee /sys/module/zfs/parameters/zfs_arc_max  # 12 GiB (matches host_vars)

# One-off swap reset (what the timer does)
sudo /usr/local/sbin/swap-clean.sh
# Timer status
systemctl status swap-clean.timer; journalctl -t swap-clean --since today

# Permanent changes: edit host_vars (nas_storage_zfs_arc_max_bytes / nic_tuning_vm_swappiness
# / nas_storage_swap_clean_*) and redeploy
task storage:deploy
```

## Encryption Posture

Storage at-rest encryption is [docs/32](32-zfs-encryption.md). The estate-wide
at-rest and in-transit matrix, covering every subsystem and not just storage, is
[docs/47](47-security-posture.md).

## Related documentation

- [docs/32 — ZFS encryption](32-zfs-encryption.md) (encryption roots, boot unlock, key handling)
- [docs/47 — Security posture](47-security-posture.md) (the estate-wide at-rest/in-transit matrix)
- [docs/44 — Storage bootstrap](44-storage-bootstrap.md) (pool/dataset creation order)
- [docs/07 — File services](07-fileservices.md) (NFS/Samba exports on these pools)
- [docs/17 — Disaster recovery](17-disaster-recovery.md) and [docs/42 — Offsite backup](42-offsite-backup.md)
- [docs/34 — Bond MAC flapping](34-bond-mac-flapping.md) and [docs/43 — GPU passthrough](43-gpu-passthrough.md) (the ARC-cap constraint)

## External references

- [OpenZFS Documentation](https://openzfs.github.io/openzfs-docs/)
- [ZFS Best Practices](https://pthree.org/2012/12/13/zfs-administration-part-ix-copy-on-write/)
- [Proxmox ZFS Guide](https://pve.proxmox.com/wiki/ZFS_on_Linux)
