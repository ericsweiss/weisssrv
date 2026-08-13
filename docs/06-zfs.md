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
  authentik, gitlab, loki, mealie, nextcloud, prometheus). The Nextcloud VM
  (156) adds `ssd/appdata/nextcloud/app` (20G, /mnt/nextcloud-app: compose +
  html/config + backups) and `ssd/appdata/nextcloud/postgres` (16G, PGDATA).
  Its bulk user data is a 2T **sparse** zvol `tank/nextcloud-data/disk` under the
  encrypted `tank/nextcloud-data` root (already in the archive SRC_LIST).
- `ssd/databases` - Database storage (encryption root; replicated to `archive`)
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

> The canonical dataset inventory is the encryption table (§At Rest) and the
> backup section below — these per-pool lists are a quick reference.

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

**Future: Mirror Configuration** (if node has 2 SSDs):

```bash
# For future nodes with 2x 1TB SSDs (redundancy)
zpool create -f -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  -O xattr=sa \
  -O normalization=formD \
  -O acltype=posixacl \
  local-ssd mirror \
  /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIAL1 \
  /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIAL2
```

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

### Automatic Snapshots

The `com.sun:auto-snapshot` property controls which datasets are auto-snapshotted:

- **Enabled** (default): Most datasets
- **Disabled**: `archive` pool (set to `false`)

### Manual Snapshots

Create manual snapshots for important changes:

```bash
# Create snapshot
sudo zfs snapshot tank/media@pre-upgrade-2026-01-02

# List snapshots
sudo zfs list -t snapshot -r tank/media

# Rollback to snapshot
sudo zfs rollback tank/media@pre-upgrade-2026-01-02

# Delete snapshot
sudo zfs destroy tank/media@pre-upgrade-2026-01-02
```

## Dataset Quotas and Reservations

### Setting Quotas

Limit dataset size:

```bash
# Set quota
sudo zfs set quota=500G tank/share

# View quota
sudo zfs get quota tank/share

# Remove quota
sudo zfs set quota=none tank/share
```

### Reservations

Guarantee minimum space:

```bash
# Reserve space
sudo zfs set reservation=100G ssd/appdata

# View reservation
sudo zfs get reservation ssd/appdata
```

## Performance Tuning

### Record Size

Optimized per workload:

- **Large files (media)**: `tank/media` is set to 1M (large sequential media);
  128K is the pool default used by `tank/share`
- **Databases**: 8K-16K
- **Small files**: 16K-32K

```bash
# Set recordsize for new dataset
sudo zfs create -o recordsize=16K ssd/appdata/postgres

# Change existing (only affects new writes)
sudo zfs set recordsize=16K ssd/appdata/mealie
```

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

The four 14-15 GiB opt/laptop hosts each **override it to 2 GiB** in their
`host_vars`: on a host that small an 8 GiB ceiling is not self-limiting (one was
measured at 4.3 GiB ARC with 2 GiB available and 1.7 GiB swapped), so the cap has
to be set below what the host can actually spare.

The NAS carries its own, larger cap — see *NAS memory management* below.

Monitor ARC usage:

```bash
# ARC stats
sudo arc_summary

# Current ARC size
awk '$1 ~ /^(c|c_max|size)$/' /proc/spl/kstat/zfs/arcstats
```

## Monitoring and Maintenance

### Pool Health

```bash
# Check all pools
sudo zpool status -v

# Check specific pool
sudo zpool status tank

# List pools with capacity
sudo zpool list
```

### Dataset Usage

```bash
# List all datasets with usage
sudo zfs list -o name,mountpoint,used,avail -r

# Show dataset tree
sudo zfs list -t all -r tank

# Show space accounting
sudo zfs list -o space -r tank
```

### I/O Statistics

```bash
# Pool I/O stats (live)
sudo zpool iostat -v 5

# Detailed stats
sudo zpool iostat -v tank 5
```

## Backup Strategy

### Archive Replication

`archive-backupctl` (on pve-nas-01, nightly timer) replicates the source datasets
to the `archive` pool as **raw, encrypted** `zfs send -w` streams, so the archive
copies are encrypted at rest under each source's own key — the archive never
loads a key. Replicated datasets (`nas_storage_archive_backup_sources` in
`host_vars/pve-nas-01.yml` is the source of truth; the role renders it as the
script's `SRC_LIST`): `tank/{share,backups,nextcloud-data,proxmox,immich-data}` and
`ssd/{appdata,databases,k3s-etcd}`. Retention: the newest few `archsync`
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
with `vers=4.2,xprtsec=tls`; migrating the legacy IP-based entry is a
one-time supervised step (outside a backup window:
`pvesh delete /storage/tank-proxmox` — config only, data untouched — then
re-run the role). See weisssrv-lib `ansible_collections/weisssrv/infra/roles/proxmox_backup/README.md`.

## Ansible Management

ZFS configuration is managed by the `nas_storage` role.

### Deploying ZFS Configuration

```bash
# Deploy ZFS settings (storage.yml defines no tags — run untagged, or use task storage:deploy)
ansible-playbook ansible/playbooks/storage.yml

# Verify configuration
ansible pve-nas-01 -m shell -a "zfs get all tank/media"
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

### NAS memory management (ARC cap, swappiness, swap reset)

pve-nas-01 has ~124 GiB usable. Guest maximums total ~84 GiB of VMs plus the two
LXC ceilings (plex and immich-ml at `proxmox_lxc_memory: 8192` each), and the ARC ceiling
adds 16 GiB — so worst case, with every guest pinned at its cap, the commit is
~120 GiB of ~124. Both LXCs peak well under 1 GiB in practice, so the realistic
spare is ~20 GiB; the ceilings are cgroup caps, which cost nothing until
something genuinely asks for the memory.
It is not memory-tight, and the five levers below are what keep it that way —
most of them are **guards** rather than fixes: they cost nothing while there is
headroom, and they are what stops a new workload silently re-creating the swap
ratchet this host is prone to.

The first three live in `host_vars/pve-nas-01.yml`, the fourth in `hosts.yml`,
the fifth in the GitLab runner config:

1. **ARC cap `nas_storage_zfs_arc_max_bytes` = `17179869184` (16 GiB).** The `nas_storage`
   role renders `/etc/modprobe.d/zfs.conf` and notifies an `update-initramfs`
   handler (the pools import from the initramfs at early boot, so a bare
   modprobe.d write would only apply on the next module reload). The cap is a
   **guard, not a target**: uncapped, ARC takes ~half of RAM (~62 GiB), and a
   rebuild must never silently revert to that. The measured working set sits far
   below the ceiling — ARC runs a ~98 % hit ratio at ~2.5 GiB, almost all
   metadata, because the pools are large-but-cold — so 16 GiB is room for the
   metadata cache to grow as the pools fill, not a response to a shortage.
2. **`vm.swappiness = 1`** (`nic_tuning_vm_swappiness`, via `nic_tuning`'s
   sysctl.d drop-in). This is the only host in the fleet that sets it; the others
   keep the kernel default of 60, because they run neither ZFS ARC nor
   memory-pinned guests. At higher values the kernel parks cold anon pages to
   swap opportunistically *even with free RAM*, and since Linux never pages them
   back in on its own, swap ratchets upward rather than thrashing — measured at
   10 on this host, climbing 0 → 7 GB+ with `si≈0`, and once reaching ~17 GB. At
   1 it reclaims from ARC — instantly reclaimable — and only swaps under genuine
   near-OOM pressure. Free RAM slows that ratchet but does not remove it, and
   swapping buys this host nothing it does not already have.
3. **Daily swap reset** (`nas_storage_swap_clean_enabled`). swappiness=1 stops the
   *opportunistic* parking, but a full host still swaps under real peak events
   (deploys/backups/ML) and that swap never self-clears. A `swap-clean.timer`
   (nightly) runs `/usr/local/sbin/swap-clean.sh`, which shrinks ARC for headroom,
   `swapoff -a`/`swapon -a`, and restores ARC — **only** when the freed RAM
   comfortably covers the swap in use. It is device-agnostic, so it works
   unchanged now that swap is `/dev/mapper/cryptswap` (`encrypted_swap`, see the
   At Rest table below and docs/42); its pre-flight skips the cycle while the
   mapper is still pending on a host that has not yet rebooted into it.
   **Recovery escalation** (`nas_storage_swap_clean_stop_guests`): if the ARC-shrink
   headroom alone can't cover the swap, rather than just aborting the reset
   *gracefully shuts down* heavy guests from an ordered candidate list
   (`vmid:name:timeout` — immich → GitLab → nextcloud), **only as many as needed**
   to reach the headroom, does the swapoff, then restarts every guest it stopped.
   A single cleanup trap fires on **any** exit (success, error, or signal) and
   brings back every guest it touched — a crash mid-reclaim can never strand one.
   Guests are only ever stopped **gracefully** (`qm shutdown`, never a hard kill);
   a guest that won't stop within its timeout aborts the reclaim rather than being
   forced, so a Postgres guest is never at risk. With lever 4 the no-downtime
   ARC-shrink path covers the common case, so a guest stop is the rare-overflow
   exception, not a nightly event. The service `TimeoutStartSec` (1200s) sits well
   above the sum of the stop-guest timeouts so a legit reclaim is never cut short.
4. **NAS k3s VMs sized for headroom** (`hosts.yml`: `k3s-agt-nas-01`
   `vm_memory: 24576`; `k3s-srv-nas-01` `vm_memory: 8192`). The agent carries the
   heavy in-cluster state (Prometheus, Loki, the Authentik/Mealie Postgres zvols)
   *and* is CI-eligible, so it is sized for both: 24 GB puts standing pod requests
   at ~32 % of allocatable and leaves ~16 GB of slack for CI job pods bursting on
   top. Anything much tighter starts OOM-killing the observability stack when a
   pipeline lands there. The etcd server gets 8 GB against ~2.4 GB of use —
   deliberate margin, not need: etcd is latency-sensitive and the one guest here
   whose degradation takes the whole cluster with it, so do not size it to
   measured usage. Cores are deliberately **not** raised to match: the host is
   32 vCPU over 20 physical, and CI must not crowd out host-side CPU work.
   Applying a `vm_memory` change needs a guest reboot (drain → `qm shutdown`/`qm
   start` → uncordon; one k3s server at a time to preserve etcd quorum).
5. **CI job pods are eligible on the NAS agent, but last**
   (`kubernetes/apps/gitlab-runner-privileged/release.yaml`). There is no
   `node_affinity` exclusion; the node's `esweiss.com/nas=true:PreferNoSchedule`
   taint is what makes it least-preferred, so the scheduler exhausts every other
   agent before spilling here. That matters because this host also runs on-demand
   CPU work, and CI should be its last claim rather than a standing one — while on
   pure capacity it is the least-loaded machine in the fleet (20 cores at ~9 %
   load, against 4-core opt nodes at 58–68 %).
   **The taint is the lever**: to tighten this, raise it to `NoSchedule` rather
   than adding an affinity exclusion, so the decision lives in one place. Spilled
   molecule/DinD jobs are memory-heavy (~1.5–2.5 GB each, up to 7 concurrent) and
   were the main driver of swap pressure back when this host was over-committed,
   so keep an eye on the mix if the taint is ever relaxed. Including the agent's
   6 cores makes the CI-eligible pool 25 cores, which is what `concurrent: 12`
   and the matching ResourceQuota are sized against.

```bash
# View ARC size + hit ratio
awk '$1 ~ /^(c|c_max|size)$/' /proc/spl/kstat/zfs/arcstats
sudo arc_summary | grep "Hit Rate"

# Limit ARC (temporary, until reboot)
echo 17179869184 | sudo tee /sys/module/zfs/parameters/zfs_arc_max  # 16 GiB

# One-off swap reset (what the timer does)
sudo /usr/local/sbin/swap-clean.sh
# Timer status
systemctl status swap-clean.timer; journalctl -t swap-clean --since today

# Permanent changes: edit host_vars (nas_storage_zfs_arc_max_bytes / nic_tuning_vm_swappiness
# / nas_storage_swap_clean_*) and redeploy
task storage:deploy
```

### Slow Performance

1. **Check pool status**:
   ```bash
   sudo zpool status -v
   ```

2. **Check I/O wait**:
   ```bash
   iostat -x 5
   ```

3. **Monitor ARC hit rate**:
   ```bash
   sudo arc_summary | grep "Hit Rate"
   ```

4. **Check fragmentation**:
   ```bash
   sudo zpool list -o name,frag
   ```

## Encryption Posture

The homelab's threat model treats the LAN as a trust boundary. TLS terminates
at the perimeter (Cloudflare → Traefik wildcard certs) and at outbound paths
(SMTP STARTTLS, DoT to upstream resolvers, WireGuard for Tailscale). Inside
the LAN, application traffic and on-disk data are intentionally cleartext to
keep operations simple. This section documents what is and isn't encrypted so
the posture is explicit rather than assumed.

### At Rest

> **Encryption.** `tank` and `ssd` use ZFS-native
> dataset-level encryption with boot-time passphrase unlock via 1Password Connect
> (`zfs_encryption` role; model and runbooks in
> [`docs/32-zfs-encryption.md`](32-zfs-encryption.md)). Pool roots stay plaintext;
> the encryption roots are the sensitive child datasets. `nvme` is plaintext by
> design — it is the media domain (hot-tier media, transcode scratch, ephemeral
> images), non-sensitive like `tank/media`. `archive` is a plaintext raidz1
> pool; its sensitive replicated datasets are encrypted at rest via raw
> `zfs send -w` streams from the encrypted tank/ssd sources (see docs/32).
> Compute-node `local-ssd` pools stay
> plaintext on purpose (avoids a chicken-and-egg dependency on the Connect VIP
> at boot), and so does pve-nas-01's `local-lvm` thin pool, which is not ZFS at
> all — it is carved out of the Proxmox boot device and carries four guests.

| Subsystem | Encrypted? | Notes |
|---|---|---|
| ZFS pools `tank`, `ssd` | Yes (dataset-level) | Encryption roots `tank/share`, `tank/backups`, `tank/proxmox`, `tank/nextcloud-data`, `tank/immich-data`, `ssd/appdata` (+ children), `ssd/databases`, `ssd/pve`, `ssd/k3s-etcd`; boot unlock via Connect. `tank/media` and `tank/pve` stay plaintext by design. See `docs/32-zfs-encryption.md`. |
| ZFS pool `nvme` | No | Media domain (hot-tier media, transcode scratch, ephemeral images) — non-sensitive, plaintext by design. See `docs/32-zfs-encryption.md`. |
| ZFS pool `archive` | Dataset-level (raw) | Plaintext pool; the eight replicated backup datasets arrive as raw `zfs send -w` streams encrypted under their source keys (`archive-backupctl`). See `docs/32-zfs-encryption.md`. |
| Compute-node `local-ssd` ZFS pools (5 hosts) | No | Intentionally plaintext — see `docs/32-zfs-encryption.md` for the cold-boot rationale and for what the pools actually hold. |
| pve-nas-01 `local-lvm` (LVM-thin in VG `pve`) | No | Not ZFS and not dm-crypt: a thin pool on partition 3 of the Proxmox boot NVMe. Carries CT 152 (plex) and CT 158 (immich-ml) rootfs plus VM 202 (k3s-agt-nas-01) and VM 222 (k3s-srv-nas-01, an **etcd quorum member** — its root holds the etcd datastore, the k3s secrets-encryption key, the server token and the cluster CA). Same cold-boot rationale as compute `local-ssd`: VM 222 must never wait on unlock, or quorum would depend on the NAS. VM 202 appears in `zfs_encryption_guest_vmids` because its passthrough data zvols live on the encrypted `ssd/appdata` — that list is start ordering, not a claim about the root disk. See `docs/32-zfs-encryption.md`. |
| App PVCs — zvol-backed (Authentik PG, Mealie PG, GitLab repos, Prometheus, Loki) | Yes | Each is a zvol under `ssd/appdata/*` → inherits the encrypted parent. |
| App PVCs — NFS-backed (all of them) | Yes, except the media share | Every app NFS PV resolves under `/appdata` or `/backups-apps` (i.e. `ssd/appdata` / `tank/backups`), both encryption roots — the export, not the PV, carries the encryption. The one exception is the `/media` PV used by the download/media stack, which is the plaintext media domain by design. The PV definitions under `kubernetes/apps/` (plus the Grafana PV in `infrastructure/observability/`) are the inventory of record. |
| Proxmox VM disks | Mixed | App-data zvols under `ssd/appdata/*` are encrypted, and the NAS app-VM root disks land on `ssd/pve`, which **is** an encryption root (GitLab, Nextcloud, Immich — docs/32, docs/35, docs/36). Guest disks on `local-ssd` (every compute-host k3s VM), pve-nas-01's `local-lvm` (row above) and `tank/pve` (ephemeral images) are plaintext by design. The `ssd` storage id is codified in `proxmox_backup_storage` (host_vars/pve-nas-01.yml) with `pool: ssd/pve`; `pool` is create-fixed and asserted, so an id recreated against the pool root `ssd` (encryption=off) fails the deploy instead of silently landing new guest disks in plaintext. `postflight.yml` additionally asserts `ssd/pve` is still `aes-256-gcm`. |
| K3s Secrets in etcd | Yes | `secrets-encryption: true` (`reencrypt_finished` confirmed). |
| 1Password Connect on-disk cache | Yes | Connect server's encrypted SQLite (AES via bootstrap creds). |
| 1Password vault (cloud + Connect sync source) | Yes | Vendor end-to-end (SRP + secret key). |
| Backups (NAS archive pool, ZFS snapshots, GitLab backups) | Yes (dataset-level) | `tank/proxmox` (VM backup target) is encrypted; `archive`'s backup datasets arrive as raw-encrypted `zfs send -w` streams under their source keys. |
| Proxmox host root filesystems | No | Standard install, no LUKS. |
| Proxmox host swap (all 6 hosts) | Yes | `encrypted_swap` role — dm-crypt plain-mode AES-256-XTS over `/dev/pve/swap` with a random ephemeral key regenerated every boot (`/dev/mapper/cryptswap`), so paged-out memory is unrecoverable after a reboot. See `docs/42-offsite-backup.md` § Encrypted swap. |

### In Transit

| Path | Encrypted? | Notes |
|---|---|---|
| Internet → Cloudflare edge | Yes | TLS 1.2+/1.3, `always_use_https=on` (Cloudflare side). |
| Cloudflare → origin (Traefik) | Yes | `ssl=strict`, valid origin cert required. Traefik enforces TLS 1.3 minimum via cluster-default `TLSOption` (`infrastructure/configs/traefik-tls-options.yaml`). |
| LAN/Tailscale client → Traefik | Yes | Wildcard certs + HSTS middleware + TLS 1.3 minimum (same default `TLSOption`). |
| Traefik → in-cluster app pods | Yes (cross-node) | Plain HTTP at the L7 layer; flannel-wireguard encrypts every cross-node packet (`--flannel-backend=wireguard-native`). Same-node hops stay on the local bridge unencrypted at L3 — acceptable per LAN-trust threat model since the attacker would already need root on the node. |
| Traefik → VM backends (GitLab web, HAOS, Plex) | Yes | Each backend terminates TLS using the *.esweiss.com wildcard distributed by acme_certs (gitlab nginx :443, HAOS http.ssl_certificate :8123, Plex custom-cert PFX :32400); Traefik connects with `scheme: https` + the shared `vm-tls-wildcard` ServersTransport. Auto-renewal is part of the standard acme.sh post-renewal hook. |
| Traefik → AdGuard admin (dns-01/dns-02) :443 | Yes | Both the resolver-hostname routes (`dns-0X`) and the SSO routes (`adguard`/`adguard-02`) target the `adguard-home-0X-backend` Services with `scheme: https`, port 443, `vm-tls-wildcard` ServersTransport — AdGuard terminates TLS itself with the distributed wildcard cert. |
| adguard-exporter → AdGuard admin API (dns-01/dns-02) :443 | Yes | `ADGUARD_SERVERS: https://dns-0X.esweiss.com` in `infrastructure/observability/exporters/adguard-exporter.yaml`, with a pod-level `hostAliases` pinning each name to the host IP. The name (not a bare IP) is required — the wildcard cert has no IP SAN — and the alias is required because the AdGuard rewrite sends that name to the Traefik internal VIP, whose route drops the pod CIDR. AdGuard's admin credential no longer crosses the LAN in the clear. |
| Traefik → GitLab Container Registry :5050 | Yes | `registry_nginx['listen_https'] = true` with the distributed wildcard cert; Traefik connects `scheme: https` + `vm-tls-wildcard` ServersTransport. |
| Traefik → GitLab Pages :8443 | Yes | `pages_nginx` terminates TLS on :8443 (wildcard cert) and proxies to the pages daemon, which binds localhost only; Traefik connects `scheme: https` + `vm-tls-wildcard`. |
| Traefik → router | No | Hardware-specific; configure router's TLS endpoint manually if/when it's worth the effort. |
| Pod-to-pod (CNI) | Yes (cross-node) | flannel-wireguard-native, UDP/51820, WireGuard (Curve25519 + ChaCha20-Poly1305). Same-node pod-to-pod stays on the local bridge unencrypted. |
| ESO → 1Password Connect | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. Same-node hop on `cni0` is unencrypted. |
| Alloy → Loki ingestion | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. Same-node hop on `cni0` is unencrypted. |
| Host Alloy → Loki | Yes | `alloy_host_loki_url` is set to `https://loki.esweiss.com/loki/api/v1/push` (Traefik IngressRoute, lan-tailscale-only). The plaintext `:31100` NodePort is not in git — it is applied by hand only for the duration of an ingress outage and deleted afterwards (docs/12 § Loki break-glass NodePort). |
| AdGuard sync (dns-01 → dns-02) | Yes | adguardhome-sync URLs target the Traefik-fronted `dns-{01,02}.esweiss.com` hostnames; TLS terminated by Traefik with the wildcard cert. |
| Local clients → smtp-relay | Yes in practice | The hosts' null-client sets `smtp_tls_security_level = secure` (encrypts **and** verifies the relay's cert), and submission :587 requires TLS (`smtpd_tls_security_level = encrypt` in `master.cf`). The relay's :25 smtpd is opportunistic only (`smtpd_tls_security_level = may`), so plaintext would still be accepted there. No client uses it, and since `mynetworks`/`smtpd_relay_restrictions` dropped back to the role default (loopback-only + SASL-only, `group_vars/mail.yml`) an unauthenticated LAN sender on :25 is refused rather than relayed. The `sg-smtp-relay` :25 firewall rule is still rendered by the library role. |
| Immich VM (.157) → Immich ML LXC (.158) :3003 | No | Every photo byte crosses the LAN as plain HTTP. Scoped to the one source by the `sg-immich-ml` security group (`IN ACCEPT -source 192.168.0.157 -p tcp -dport 3003`). See docs/36. |
| smtp-relay → Gmail | Yes | `smtp_tls_security_level: secure` (encrypts AND verifies Gmail's certificate against the system CA bundle). |
| Unbound → upstream resolvers | Yes | DoT with `tls-cert-bundle`. |
| LAN clients → AdGuard (port 53) | Partial | Plain UDP/TCP 53; DoT exposed but stub resolvers rarely use it. |
| NFS exports | TLS for k3s + Proxmox backup target; one plaintext exception | NFSv4 over kernel-TLS via `tlshd` (`nfs_tls` role, `nfs_tls_enabled: true` on every k3s agent and all six Proxmox hosts). The k3s client lines of `/export/{appdata,share,media}` **require TLS** (`xprtsec=tls` — plaintext rejected); the k3s PVs *mount* with `xprtsec=tls`, **by hostname** (`pve-nas-01.esweiss.com`, so the `*.esweiss.com` cert verifies — an IP mount fails the handshake). `xprtsec` is per-client, so the require-TLS k3s lines coexist with the one documented plaintext exception: HAOS (.154) on `/export/media` — its Supervisor hardcodes NFS mount options and the appliance has no `tlshd`, so it can never request TLS (see docs/24). The Proxmox `tank-proxmox` backup target mounts with hostname + `xprtsec=tls` via the `proxmox_backup` role, though its export does not yet *require* TLS (docs/16). See weisssrv-lib `ansible_collections/weisssrv/infra/roles/nfs_tls/README.md`. |
| Samba | Yes | `smb encrypt = required` + `server min protocol = SMB3_00` in smb.conf — every SMB session is encrypted. |
| Tailscale (admin remote access) | Yes | WireGuard. |
| K3s API server | Yes | TLS, kube-vip + standard k3s API certs. |
| GitLab SSH | Yes | Port 22 internal, 2222 external. |

### Pragmatic improvements

The biggest concrete gain for the LAN-trust threat model is encrypting
*offsite* / *retired* media: a stolen disk is the most realistic exposure.
ZFS native encryption with passphrase-from-Connect unlock is documented
in `docs/32-zfs-encryption.md`. Scope (post-rollout):

- Encrypted: `tank/share`, `tank/backups`, `tank/proxmox` (Proxmox VM backup
  target — holds VM tarballs with persistent app state), `tank/nextcloud-data`,
  `tank/immich-data`, `ssd/appdata` (+ children, per-app via send/recv),
  `ssd/databases`, `ssd/pve` (GitLab VM disks).
- Not encrypted (by design): `tank/media` plus the whole `nvme` pool
  (`nvme/media` hot tier, `nvme/fast` transcode scratch, `nvme/pve` ephemeral
  images) — one logical media domain, LAN-trust acceptable and encryption
  complicates `zfs send` to off-pool replicas,
  `tank/pve` (ephemeral VM/LXC images), the `archive` pool root
  (plaintext; its eight replicated backup datasets are raw-encrypted at rest
  under their source keys — see `docs/32-zfs-encryption.md`), every compute node's
  `local-ssd` pool (encrypting it deadlocks cold-boot — k3s VMs live
  on `local-ssd`, Connect runs in k3s, Connect would need to be up
  before pve-nas-01 could fetch its own passphrase, which would need
  k3s up, which would need `local-ssd` up; threat coverage moves to
  the drive-wipe SOP in `docs/15-credential-rotation.md` plus k3s
  `secrets-encryption` for etcd at rest).

**Shipped (active in current configuration):**

- **Drive decommission / RMA SOP** — `docs/15-credential-rotation.md`
  covers ATA Secure Erase / NVMe sanitize / dd-overwrite per device class
  plus the inventory-update checklist.
- **flannel `wireguard-native` backend** — `k3s_flannel_backend:
  wireguard-native` in `group_vars/k3s.yml`, rendered as
  `flannel-backend:` in `/etc/rancher/k3s/config.yaml`; encrypts every
  cross-node pod hop. Cost ~5% throughput; matrix updated above.
- **AdGuard sync over HTTPS** — `adguard_sync_origin/replica`
  target the Traefik-fronted `dns-{01,02}.esweiss.com` hostnames.

## Related documentation

- [docs/32 — ZFS encryption](32-zfs-encryption.md) (encryption roots, boot unlock, key handling)
- [docs/44 — Storage bootstrap](44-storage-bootstrap.md) (pool/dataset creation order)
- [docs/07 — File services](07-fileservices.md) (NFS/Samba exports on these pools)
- [docs/17 — Disaster recovery](17-disaster-recovery.md) and [docs/42 — Offsite backup](42-offsite-backup.md)
- [docs/34 — Bond MAC flapping](34-bond-mac-flapping.md) and [docs/43 — GPU passthrough](43-gpu-passthrough.md) (the ARC-cap constraint)

## External references

- [OpenZFS Documentation](https://openzfs.github.io/openzfs-docs/)
- [ZFS Best Practices](https://pthree.org/2012/12/13/zfs-administration-part-ix-copy-on-write/)
- [Proxmox ZFS Guide](https://pve.proxmox.com/wiki/ZFS_on_Linux)
