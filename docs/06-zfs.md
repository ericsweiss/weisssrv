# ZFS Storage Configuration

This document covers ZFS pool and dataset configuration on the NAS (pve-nas-01).

## ZFS Pools

The NAS has four ZFS pools with different performance characteristics:

| Pool | Type | Capacity | Use Case |
|------|------|----------|----------|
| `tank` | raidz2 (6x 22TB) + special device + cache | 122TB | Bulk media storage |
| `ssd` | raidz1 (3x 4TB SSD) | 10.9TB | App data, databases |
| `nvme` | Single NVMe 4TB | 2.27TB | Hot downloads, fast scratch |
| `archive` | raidz1 (4x 6TB) | 21.8TB | Cold backups |

## Pool Details

### tank (Primary Media Pool)

**Configuration**:
- **Data VDEVs**: raidz2 with 6x ST22000NM000C (22TB enterprise drives)
- **Special Device**: 2x 2TB NVMe mirror (metadata + small blocks)
- **L2ARC Cache**: 1x 2TB NVMe

**Properties**:
```bash
atime: off
compression: zstd
recordsize: 128K (default)
```

**Datasets**:
- `tank/media` - Media library (mounted `/mnt/tank/media`)
- `tank/share` - General shared storage (mounted `/mnt/tank/share`)
- `tank/downloads` - Downloads directory (mounted `/mnt/tank/downloads`)
- `tank/proxmox` - Proxmox backup target (mounted `/mnt/tank/proxmox`)
- `tank/pve` - Proxmox storage (mounted `/mnt/tank/pve`)

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
             /dev/disk/by-id/ata-ST22000NM000C-3WC103_ZXA0FEGJ

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
- `recordsize`: Override to 16K for databases (set per dataset)
- Same base properties as tank pool

**Capacity**: With raidz1, usable capacity is approximately 8TB (4TB × 2 data drives)

### NVMe Pool Creation

The nvme pool is a single-device pool using a Samsung 990 PRO 4TB for maximum performance.

```bash
# Create nvme pool (single device, no redundancy)
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/nvme \
             nvme \
             /dev/disk/by-id/nvme-Samsung_SSD_990_PRO_4TB_S7KGNU0YA04137V

# Verify pool creation
zpool status nvme
zpool list -v nvme
```

**Pool Properties**:
- Single device (no redundancy)
- Optimized for hot data and fast scratch space
- Same base properties as tank pool

**Capacity**: Full 4TB available (no parity overhead)

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
             /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1NC3Z \
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

### Future Pool Configurations

#### Optiplex/Laptop 1TB SSD Pool (Planned)

For future Optiplex and laptop nodes with 1TB Samsung 870 EVO SSDs:

```bash
# Create local-ssd pool on compute nodes (when installed)
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/local-ssd \
             local-ssd \
             /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB-SERIALNUMBER

# For mirror configuration (if node has 2 SSDs)
zpool create -o ashift=12 \
             -O acltype=posixacl \
             -O compression=zstd \
             -O normalization=formD \
             -O relatime=on \
             -O xattr=sa \
             -m /mnt/local-ssd \
             local-ssd mirror \
             /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB-SERIAL1 \
             /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB-SERIAL2
```

**Use Case**: Local storage for VM/LXC workloads on compute nodes
**Status**: Not yet implemented - awaiting hardware installation

### ssd (App Data Pool)

**Configuration**:
- **Data VDEVs**: raidz1 with 3x Samsung 870 EVO 4TB

**Properties**:
```bash
atime: off
compression: zstd
recordsize: 16K (for databases)
```

**Datasets**:
- `ssd/appdata` - Application persistent data
- `ssd/databases` - Database storage
- `ssd/pve` - Proxmox LXC root filesystems

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
- `nvme/fast` - Fast scratch space (Plex transcode, etc.)
- `nvme/pve` - Fast Proxmox workloads

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
- `archive/appdata` - Archived app data
- `archive/share` - Archived shared files

## ZFS Scrubs

Regular scrubs verify data integrity and repair any errors.

### Current Schedule

ZFS scrubs are scheduled via cron or systemd timers:

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

**TODO**: Document exact scrub schedule once retrieved from system.

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
sudo zfs set reservation=100G ssd/databases

# View reservation
sudo zfs get reservation ssd/databases
```

## Performance Tuning

### Record Size

Optimized per workload:

- **Large files (media)**: 128K (default)
- **Databases**: 8K-16K
- **Small files**: 16K-32K

```bash
# Set recordsize for new dataset
sudo zfs create -o recordsize=16K ssd/postgres

# Change existing (only affects new writes)
sudo zfs set recordsize=16K ssd/databases
```

### Compression

All pools use `zstd` compression:

```bash
# Check compression ratio
sudo zfs get compressratio tank/media

# View compression stats for all datasets
sudo zfs get compression,compressratio
```

### ARC (Adaptive Replacement Cache)

Monitor ARC usage:

```bash
# ARC stats
sudo arc_summary

# Current ARC size
grep "c_max\|c\|size" /proc/spl/kstat/zfs/arcstats
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

### ZFS Send/Receive

Replicate datasets to `archive` pool:

```bash
# Initial send
sudo zfs snapshot ssd/appdata@backup-$(date +%Y%m%d)
sudo zfs send ssd/appdata@backup-$(date +%Y%m%d) | \
  sudo zfs receive archive/appdata

# Incremental send
sudo zfs snapshot ssd/appdata@backup-$(date +%Y%m%d)
sudo zfs send -i ssd/appdata@backup-$(date +%Y%m%d-%H%M) \
  ssd/appdata@backup-$(date +%Y%m%d) | \
  sudo zfs receive archive/appdata
```

### Proxmox Backup

Proxmox VMs/LXCs backup to:
- `tank/proxmox` (primary)
- `archive/proxmox` (long-term retention)

## Ansible Management

ZFS configuration is managed by the `nas_storage` role.

### Deploying ZFS Configuration

```bash
# Deploy ZFS settings
ansible-playbook ansible/playbooks/storage.yml --tags zfs

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

### High Memory Usage (ARC)

ZFS ARC can use significant RAM:

```bash
# View ARC size
grep "c_max\|c\|size" /proc/spl/kstat/zfs/arcstats

# Limit ARC (temporary)
echo 34359738368 | sudo tee /sys/module/zfs/parameters/zfs_arc_max  # 32GB

# Permanent: add to /etc/modprobe.d/zfs.conf
options zfs zfs_arc_max=34359738368
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

## References

- [OpenZFS Documentation](https://openzfs.github.io/openzfs-docs/)
- [ZFS Best Practices](https://pthree.org/2012/12/13/zfs-administration-part-ix-copy-on-write/)
- [Proxmox ZFS Guide](https://pve.proxmox.com/wiki/ZFS_on_Linux)
