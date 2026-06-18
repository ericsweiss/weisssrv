# ZFS Storage Configuration

This document covers ZFS pool and dataset configuration on the NAS (pve-nas-01).

## ZFS Pools

The NAS has four ZFS pools with different performance characteristics:

| Pool | Type | Capacity | Use Case |
|------|------|----------|----------|
| `tank` | raidz2 (6x 22TB) + special device + cache | ~88TB usable (122TB raw) | Bulk media storage |
| `ssd` | raidz1 (3x 4TB SSD) | 10.9TB | App data, databases |
| `nvme` | Single NVMe 4TB | 2.27TB | Hot downloads, fast scratch |
| `archive` | raidz1 (4x 6TB) | 21.8TB | Cold backups |

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
`archive-N` LUKS container in `host_vars/pve-nas-01.yml`; after the LUKS
rolling conversion the pool members are `/dev/mapper/archive-{1..4}`):

| LUKS container | Physical drive (by-id) | Notes |
|----------------|------------------------|-------|
| archive-1 | ata-SEAGATE_ST6000NM0024_Z4D2BDD2 | Replaced Z4D1NC3Z 2026-06-06 |
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
  authentik, gitlab, loki, mealie, prometheus)

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

Scrub scheduling is handled by Proxmox's built-in ZFS scrub systemd timers (typically monthly).
Check current schedule with: `systemctl list-timers '*scrub*'`

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

- **Large files (media)**: 128K (default)
- **Databases**: 8K-16K
- **Small files**: 16K-32K

```bash
# Set recordsize for new dataset
sudo zfs create -o recordsize=16K ssd/appdata/postgres

# Change existing (only affects new writes)
sudo zfs set recordsize=16K ssd/appdata/mealie
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
loads a key. Replicated datasets: `tank/{share,backups,nextcloud-data,proxmox,
immich-data}` and `ssd/{appdata,databases}`. Retention: the newest few `archsync`
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

### High Memory Usage (ARC)

ZFS ARC can use significant RAM:

```bash
# View ARC size
awk '$1 ~ /^(c|c_max|size)$/' /proc/spl/kstat/zfs/arcstats

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

## Encryption Posture

The homelab's threat model treats the LAN as a trust boundary. TLS terminates
at the perimeter (Cloudflare → Traefik wildcard certs) and at outbound paths
(SMTP STARTTLS, DoT to upstream resolvers, WireGuard for Tailscale). Inside
the LAN, application traffic and on-disk data are intentionally cleartext to
keep operations simple. This section documents what is and isn't encrypted so
the posture is explicit rather than assumed.

### At Rest

> **Current state (post-rollout, 2026-06).** `tank` and `ssd` use ZFS-native
> dataset-level encryption with boot-time passphrase unlock via 1Password Connect
> (`zfs_encryption` role; model and runbooks in
> [`docs/32-zfs-encryption.md`](32-zfs-encryption.md)). Pool roots stay plaintext;
> the encryption roots are the sensitive child datasets. `nvme` is plaintext by
> design — it is the media domain (hot-tier media, transcode scratch, ephemeral
> images), non-sensitive like `tank/media`. `archive` moves to LUKS
> via the rolling conversion in docs/32 §LUKS (role bootstrapped; conversion
> follows the 2026-06-06 drive swap). Compute-node `local-ssd` pools stay
> plaintext on purpose (avoids a chicken-and-egg dependency on the Connect VIP
> at boot).

| Subsystem | Encrypted? | Notes |
|---|---|---|
| ZFS pools `tank`, `ssd` | Yes (dataset-level) | Encryption roots `tank/share`, `tank/backups`, `tank/proxmox`, `tank/nextcloud-data`, `tank/immich-data`, `ssd/appdata` (+ children), `ssd/databases`, `ssd/pve`; boot unlock via Connect. `tank/media` and `tank/pve` stay plaintext by design. See `docs/32-zfs-encryption.md`. |
| ZFS pool `nvme` | No | Media domain (hot-tier media, transcode scratch, ephemeral images) — non-sensitive, plaintext by design. See `docs/32-zfs-encryption.md`. |
| ZFS pool `archive` | Pending | LUKS rolling conversion per `docs/32-zfs-encryption.md` §LUKS; `luks_archive` role deployed in bootstrap mode until all four drives are converted. |
| Compute-node `local-ssd` ZFS pools (5 hosts) | No | Intentionally plaintext — see `docs/32-zfs-encryption.md` for the cold-boot rationale. |
| App PVCs (Authentik PG, Mealie PG, GitLab repos, Prometheus, Loki, Grafana) | Yes | All zvol-backed under `ssd/appdata/*` → inherit the encrypted parent. |
| Proxmox VM disks | Mixed | App-data zvols under `ssd/appdata/*` are encrypted; VM root disks on `local-ssd`, `tank/pve`, and the `ssd` pool root are not. |
| K3s Secrets in etcd | Yes | `secrets-encryption: true` (`reencrypt_finished` confirmed). |
| 1Password Connect on-disk cache | Yes | Connect server's encrypted SQLite (AES via bootstrap creds). |
| 1Password vault (cloud + Connect sync source) | Yes | Vendor end-to-end (SRP + secret key). |
| Backups (NAS archive pool, ZFS snapshots, GitLab backups) | Partial | `tank/proxmox` (VM backup target) is encrypted; `archive` is pending the LUKS conversion. |
| Proxmox host root filesystems | No | Standard install, no LUKS. |

### In Transit

| Path | Encrypted? | Notes |
|---|---|---|
| Internet → Cloudflare edge | Yes | TLS 1.2+/1.3, `always_use_https=on` (Cloudflare side). |
| Cloudflare → origin (Traefik) | Yes | `ssl=strict`, valid origin cert required. Traefik enforces TLS 1.3 minimum via cluster-default `TLSOption` (`infrastructure/configs/traefik-tls-options.yaml`). |
| LAN/Tailscale client → Traefik | Yes | Wildcard certs + HSTS middleware + TLS 1.3 minimum (same default `TLSOption`). |
| Traefik → in-cluster app pods | Yes (cross-node) | Plain HTTP at the L7 layer; flannel-wireguard encrypts every cross-node packet (`--flannel-backend=wireguard-native`). Same-node hops stay on the local bridge unencrypted at L3 — acceptable per LAN-trust threat model since the attacker would already need root on the node. |
| Traefik → VM backends (GitLab web, HAOS, Plex) | Yes | Each backend terminates TLS using the *.esweiss.com wildcard distributed by acme_certs (gitlab nginx :443, HAOS http.ssl_certificate :8123, Plex custom-cert PFX :32400); Traefik connects with `scheme: https` + the shared `vm-tls-wildcard` ServersTransport. Auto-renewal is part of the standard acme.sh post-renewal hook. |
| Traefik → AdGuard admin (dns-01/dns-02) :3000 | Accepted exception | LAN-only (lan-tailscale-only middleware) plain HTTP. AdGuard's admin UI doesn't natively support TLS on its own port; flipping to AdGuard's :443 (DoH+admin) requires UI/sync config outside Ansible's reach. User-facing hop (browser/Tailscale → Traefik) is HTTPS; only the LAN-internal Traefik → AdGuard hop is plain. Bounded by LAN-trust threat model. |
| Traefik → GitLab Container Registry :5050 | Yes | `registry_nginx['listen_https'] = true` with the distributed wildcard cert; Traefik connects `scheme: https` + `vm-tls-wildcard` ServersTransport. |
| Traefik → GitLab Pages :8443 | Yes | `pages_nginx` terminates TLS on :8443 (wildcard cert) and proxies to the pages daemon, which binds localhost only; Traefik connects `scheme: https` + `vm-tls-wildcard`. |
| Traefik → router | No | Hardware-specific; configure router's TLS endpoint manually if/when it's worth the effort. |
| Pod-to-pod (CNI) | Yes (cross-node) | flannel-wireguard-native, UDP/51820, WireGuard (Curve25519 + ChaCha20-Poly1305). Same-node pod-to-pod stays on the local bridge unencrypted. |
| ESO → 1Password Connect | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. Same-node hop on `cni0` is unencrypted. |
| Alloy → Loki ingestion | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. Same-node hop on `cni0` is unencrypted. |
| Host Alloy → Loki | Yes | `alloy_host_loki_url` defaults to `https://loki.esweiss.com/loki/api/v1/push` (Traefik IngressRoute, lan-tailscale-only). The plain NodePort `:31100` is kept as an emergency fallback. |
| AdGuard sync (dns-01 → dns-02) | Yes | adguardhome-sync URLs target the Traefik-fronted `dns-{01,02}.esweiss.com` hostnames; TLS terminated by Traefik with the wildcard cert. |
| Local clients → smtp-relay | Yes | `smtp_tls_security_level: encrypt`. |
| smtp-relay → Gmail | Yes | `smtp_tls_security_level: secure` (encrypts AND verifies Gmail's certificate against the system CA bundle). |
| Unbound → upstream resolvers | Yes | DoT with `tls-cert-bundle`. |
| LAN clients → AdGuard (port 53) | Partial | Plain UDP/TCP 53; DoT exposed but stub resolvers rarely use it. |
| NFS exports | TLS for k3s; plaintext exceptions | NFSv4 over kernel-TLS via `tlshd` (`nfs_tls` role, `nfs_tls_enabled: true` on `pve-nas-01` + every k3s agent). The k3s client lines of `/export/{appdata,share,media}` **require TLS** (`xprtsec=tls` — plaintext rejected); the k3s PVs *mount* with `xprtsec=tls`, **by hostname** (`pve-nas-01.esweiss.com`, so the `*.esweiss.com` cert verifies — an IP mount fails the handshake). `xprtsec` is per-client, so the require-TLS k3s lines coexist with two documented plaintext exceptions: HAOS (.154) on `/export/media` — its Supervisor hardcodes NFS mount options and the appliance has no `tlshd`, so it can never request TLS (see docs/24); and the Proxmox `tank-proxmox` backup target (Proxmox-managed NFS storage, deferred). See `ansible/roles/nfs_tls/README.md` + `docs/16-next-steps.md`. |
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
  `tank/pve` (ephemeral VM/LXC images), `archive` pool
  (separate LUKS effort, see #1 below), every compute node's
  `local-ssd` pool (encrypting it deadlocks cold-boot — k3s VMs live
  on `local-ssd`, Connect runs in k3s, Connect would need to be up
  before pve-nas-01 could fetch its own passphrase, which would need
  k3s up, which would need `local-ssd` up; threat coverage moves to
  the drive-wipe SOP in `docs/15-credential-rotation.md` plus k3s
  `secrets-encryption` for etcd at rest).

**Open follow-up:**

1. **LUKS on the `archive` pool.** It's cold-tier, no perf concern, and
   decommissioned drives are most likely to leave the building. LUKS rather
   than ZFS-native because the encryption layer sits below ZFS, keeping
   per-dataset `zfs send -w` semantics clean. Implemented by the
   `luks_archive` role: the rolling-conversion runbook (one drive at a
   time, no pool destroy) and the operator unlock procedure live in
   [`docs/32-zfs-encryption.md`](32-zfs-encryption.md) §"LUKS rolling
   conversion (archive pool)". 1Password item: **LUKS Archive Passphrase**;
   the mapper names `archive-1..4` and their physical-drive mapping are
   defined in `host_vars/pve-nas-01.yml` (`luks_archive_devices`) and in
   the drive-mapping table under [Archive Pool Creation](#archive-pool-creation)
   above.

**Shipped (active in current configuration):**

- **Drive decommission / RMA SOP** — `docs/15-credential-rotation.md`
  covers ATA Secure Erase / NVMe sanitize / dd-overwrite per device class
  plus the inventory-update checklist.
- **flannel `wireguard-native` backend** — `k3s_flannel_backend:
  wireguard-native` in `group_vars/k3s.yml`, rendered as
  `flannel-backend:` in `/etc/rancher/k3s/config.yaml`; encrypts every
  cross-node pod hop. Cost ~5% throughput; matrix updated above.
- **AdGuard sync over HTTPS** — `adguardhome_sync_origin/replica`
  target the Traefik-fronted `dns-{01,02}.esweiss.com` hostnames.

Earlier guidance was to skip ZFS-native encryption on `tank`/`ssd`. That
was reversed once 1Password Connect HA + internal exposure removed the
manual-passphrase boot dependency — passphrase is fetched at boot from
Connect, with a manual SSH-and-paste fallback for cold-cluster
recovery. For a single-tenant homelab on a LAN-trusted network, the
~5% throughput cost of a WireGuard CNI is small relative to the
encryption-at-rest gain on disk theft / RMA exposure.

## References

- [OpenZFS Documentation](https://openzfs.github.io/openzfs-docs/)
- [ZFS Best Practices](https://pthree.org/2012/12/13/zfs-administration-part-ix-copy-on-write/)
- [Proxmox ZFS Guide](https://pve.proxmox.com/wiki/ZFS_on_Linux)
