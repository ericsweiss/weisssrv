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
compression: zstd
recordsize: 16K (for databases)
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
  sudo zfs receive archive/backups/appdata

# Incremental send
sudo zfs snapshot ssd/appdata@backup-$(date +%Y%m%d)
sudo zfs send -i ssd/appdata@backup-$(date +%Y%m%d-%H%M) \
  ssd/appdata@backup-$(date +%Y%m%d) | \
  sudo zfs receive archive/backups/appdata
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

## Encryption Posture

The homelab's threat model treats the LAN as a trust boundary. TLS terminates
at the perimeter (Cloudflare → Traefik wildcard certs) and at outbound paths
(SMTP STARTTLS, DoT to upstream resolvers, WireGuard for Tailscale). Inside
the LAN, application traffic and on-disk data are intentionally cleartext to
keep operations simple. This section documents what is and isn't encrypted so
the posture is explicit rather than assumed.

### At Rest

> **Current state (pre-rollout).** The table below documents the **actual production
> posture today** — ZFS pools are not yet encrypted. The `zfs_encryption` role and
> the rollout plan in [`docs/32-zfs-encryption.md`](32-zfs-encryption.md) describe
> the **target state**: `tank`/`ssd`/`nvme` move to ZFS-native encryption with
> boot-time passphrase unlock via 1Password Connect, `archive` moves to LUKS after
> the next drive swap, and compute-node `local-ssd` pools stay plaintext on purpose
> (avoids a chicken-and-egg dependency on the Connect VIP at boot). Treat this
> section as a snapshot of where the cluster is **before** that work lands.

| Subsystem | Encrypted? | Notes |
|---|---|---|
| ZFS pools `tank`, `ssd`, `nvme`, `archive` | No | `encryption=off` on every dataset. Acceptable on a LAN-trusted homelab; risk class is disk theft / RMA / disposal. Target state in `docs/32-zfs-encryption.md`. |
| Compute-node `local-ssd` ZFS pools (5 hosts) | No | Same defaults. Intentionally remains plaintext post-rollout — see `docs/32-zfs-encryption.md` for rationale. |
| App PVCs (Authentik PG, Mealie PG, GitLab repos, Prometheus, Loki, Grafana) | No | All zvol-backed → inherit parent dataset state. |
| Proxmox VM disks | No | Same as parent ZFS. |
| K3s Secrets in etcd | Yes | `secrets-encryption: true` (`reencrypt_finished` confirmed). |
| 1Password Connect on-disk cache | Yes | Connect server's encrypted SQLite (AES via bootstrap creds). |
| 1Password vault (cloud + Connect sync source) | Yes | Vendor end-to-end (SRP + secret key). |
| Backups (NAS archive pool, ZFS snapshots, GitLab backups) | No | Live on `archive`/`tank`. |
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
| Traefik → GitLab Container Registry :5050 | Accepted exception | `gitlab.rb.j2` sets `registry_nginx['listen_https'] = false`. Omnibus's outer nginx terminates TLS at :443 for `registry.git.{ericsweiss,esweiss}.com`; :5050 is the registry backend on the GitLab VM and is never exposed to clients — only Traefik's HTTPS frontend reaches it. Public/Tailscale path is encrypted; the in-VM/cross-LAN-Traefik hop is not. Same LAN-trust class as AdGuard. |
| Traefik → GitLab Pages :8090 | Accepted exception | `gitlab.rb.j2` sets `pages_nginx['enable'] = false` so Traefik connects directly to the gitlab_pages daemon on :8090. Traefik terminates TLS at :443 for `*.pages.git.{ericsweiss,esweiss}.com`; the :8090 backend is only consumable from Traefik. User-facing path is HTTPS; the backend hop is not. |
| Traefik → router | No | Hardware-specific; configure router's TLS endpoint manually if/when it's worth the effort. |
| Pod-to-pod (CNI) | Yes (cross-node) | flannel-wireguard-native, UDP/51820, WireGuard (Curve25519 + ChaCha20-Poly1305). Same-node pod-to-pod stays on the local bridge unencrypted. |
| ESO → 1Password Connect | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. Same-node hop on `cni0` is unencrypted. |
| Alloy → Loki ingestion | Yes (cross-node) | Plain HTTP at L7; cross-node hops ride flannel-wireguard. Same-node hop on `cni0` is unencrypted. |
| Host Alloy → Loki | Yes | `alloy_host_loki_url` defaults to `https://loki.esweiss.com/loki/api/v1/push` (Traefik IngressRoute, lan-tailscale-only). The plain NodePort `:31100` is kept as an emergency fallback. |
| AdGuard sync (dns-01 → dns-02) | Yes | adguardhome-sync URLs target the Traefik-fronted `dns-{01,02}.esweiss.com` hostnames; TLS terminated by Traefik with the wildcard cert. |
| Local clients → smtp-relay | Yes | `smtp_tls_security_level: encrypt`. |
| smtp-relay → Gmail | Yes | `smtp_tls_security_level: encrypt`. |
| Unbound → upstream resolvers | Yes | DoT with `tls-cert-bundle`. |
| LAN clients → AdGuard (port 53) | Partial | Plain UDP/TCP 53; DoT exposed but stub resolvers rarely use it. |
| NFS exports | Opt-in | New `nfs_tls` role + per-export `xprtsec=tls` (NFSv4 over kernel-TLS via `tlshd`). Off by default; activating requires `nfs_tls_enabled: true` on server + every client AND `xprtsec: tls` set on the export. See `ansible/roles/nfs_tls/README.md` for the coordinated-rollout sequence; `docs/16-next-steps.md` tracks the follow-up. |
| Samba | Yes | `smb encrypt = required` + `server min protocol = SMB3_00` in smb.conf — every SMB session is encrypted. |
| Tailscale (admin remote access) | Yes | WireGuard. |
| K3s API server | Yes | TLS, kube-vip + standard k3s API certs. |
| GitLab SSH | Yes | Port 22 internal, 2222 external. |

### Pragmatic improvements

The biggest concrete gain for the LAN-trust threat model is encrypting
*offsite* / *retired* media: a stolen disk is the most realistic exposure.
ZFS native encryption with passphrase-from-Connect unlock is documented
in `docs/32-zfs-encryption.md`. Scope (post-rollout):

- Encrypted: `tank/share`, `tank/proxmox` (Proxmox VM backup target —
  holds VM tarballs with persistent app state), `tank/nextcloud-data`,
  `tank/immich-data`, `ssd/appdata/*` (per-app via send/recv), `nvme/*`.
- Not encrypted (by design): `tank/media` (LAN-trust acceptable;
  encryption complicates `zfs send` to off-pool replicas),
  `tank/pve` (ephemeral VM/LXC images), `archive` pool
  (separate LUKS effort, see #1 below), every compute node's
  `local-ssd` pool (encrypting it deadlocks cold-boot — k3s VMs live
  on `local-ssd`, Connect runs in k3s, Connect would need to be up
  before pve-nas-01 could fetch its own passphrase, which would need
  k3s up, which would need `local-ssd` up; threat coverage moves to
  the drive-wipe SOP in `docs/15-credential-rotation.md` plus k3s
  `secrets-encryption` for etcd at rest).

**Open follow-up:**

1. **LUKS on the `archive` pool.** It's cold-tier offline, no perf concern,
   and decommissioned drives are most likely to leave the building. Key
   stored in 1Password + a USB recovery copy. Bundled with the failing
   archive-pool drive replacement. LUKS rather than ZFS-native because
   (a) the pool currently has unrecoverable errors and we're re-creating
   it anyway during the drive swap, (b) LUKS keeps the encryption layer
   below ZFS so per-dataset `zfs send -w` semantics stay clean, and
   (c) `archive` isn't mounted at boot — operator unlock at use-time is
   acceptable.

   Procedure (executed at drive-swap time on `pve-nas-01`).
   `set -euo pipefail` is at the top of each block so a `cryptsetup` failure
   on disk 3 of 4 does not silently proceed to `zpool create` with a
   non-LUKS member.

   The disk list is enumerated explicitly because the failing
   `Z4D1NC3Z` is being replaced and the replacement is unlikely to be the
   same `ST6000NM0024` model — a glob like `ata-ST6000NM0024-*` would
   quietly skip it. Update `ARCHIVE_DISKS` once at swap time with the four
   actual `/dev/disk/by-id/ata-*` paths visible from
   `ls -l /dev/disk/by-id/ | grep -i 6T` after the new drive is seated.

   ```bash
   # 0. Define the four archive members up front. Update at swap time.
   set -euo pipefail
   ARCHIVE_DISKS=(
     /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JCL6
     /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1RQSM
     /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JQBA
     /dev/disk/by-id/ata-<NEW-DRIVE-ID>          # replaces Z4D1NC3Z
   )

   # Derive a stable, bay-traceable mapper name from each disk's serial.
   # `archive-Z4D1JCL6` is greppable back to the smartd config, the
   # physical drive label, and `zpool status archive` output — unlike a
   # UUID prefix which obscures which bay to pull on failure.
   mapper_name() {
     local disk_id; disk_id=$(basename "$1")
     # Strip the by-id model prefix, keep the trailing serial.
     printf 'archive-%s' "${disk_id##*_}"
   }

   # Pre-flight: every entry must be a real device. Catches the
   # <NEW-DRIVE-ID> placeholder + any other placeholder string an
   # operator might paste in, before luksFormat starts destroying data.
   for DISK in "${ARCHIVE_DISKS[@]}"; do
     if [ ! -e "$DISK" ]; then
       echo "ARCHIVE_DISKS entry '$DISK' does not exist — edit array before running." >&2
       exit 1
     fi
   done
   ```

   ```bash
   # 1. Generate the shared LUKS passphrase and store it in 1Password.
   set -euo pipefail
   op item create --category=password --vault=Homelab \
     --title='ZFS Pool archive LUKS Passphrase' \
     password="$(op item generate-password --length=64)"

   # 2. Drain + destroy the existing degraded pool.
   sudo zpool export archive
   sudo zpool destroy archive

   # 3. Format each member as LUKS2 (argon2id KDF). printf (not echo -n)
   #    so passphrases that happen to contain backslash escapes or look
   #    like `-e`/`-n` are passed verbatim to cryptsetup stdin.
   PASS=$(op read 'op://Homelab/ZFS Pool archive LUKS Passphrase/password')
   for DISK in "${ARCHIVE_DISKS[@]}"; do
     printf '%s' "$PASS" | sudo cryptsetup luksFormat --type luks2 \
       --pbkdf argon2id --batch-mode "$DISK" -
     NAME=$(mapper_name "$DISK")
     printf '%s' "$PASS" | sudo cryptsetup open "$DISK" "$NAME" -
   done

   # 4. Create the new ZFS pool on top of the LUKS containers.
   sudo zpool create -o ashift=12 -O atime=off -O compression=zstd \
     archive raidz1 \
     $(for D in "${ARCHIVE_DISKS[@]}"; do echo "/dev/mapper/$(mapper_name "$D")"; done)

   # 5. Persist via /etc/crypttab (noauto = manual unlock-at-use, not
   #    mounted at boot). The crypttab UUID column is the LUKS-header UUID
   #    blkid returns from the underlying disk after luksFormat.
   for DISK in "${ARCHIVE_DISKS[@]}"; do
     UUID=$(sudo blkid -s UUID -o value "$DISK")
     NAME=$(mapper_name "$DISK")
     printf '%s\n' "$NAME UUID=$UUID none luks,noauto" \
       | sudo tee -a /etc/crypttab
   done
   ```

   Operator unlock procedure (run when needing archive access). The
   block is self-contained — own `set -euo pipefail`, own
   `ARCHIVE_DISKS`, own `mapper_name()`. If you change `mapper_name()`
   in one place, change it in the other; the format block above and
   this block must agree on the per-disk mapper names.

   The two pre-flight guards (`[ -e "$DISK" ]` and the placeholder
   pattern check) together catch: an array still containing the
   `<NEW-DRIVE-ID>` placeholder, an operator who used a different
   placeholder string (`PLACEHOLDER`, `TODO`, etc.), and a stale entry
   for a drive that was physically removed. Without them the loop
   would silently call `cryptsetup open` on a non-existent path and
   the failure would not surface until the later `zpool import`.

   ```bash
   set -euo pipefail
   # EDIT BEFORE RUNNING — same list as the format block above. Keep the
   # canonical copy in /usr/local/sbin/archive-unlock.sh.
   ARCHIVE_DISKS=(
     /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JCL6
     /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1RQSM
     /dev/disk/by-id/ata-ST6000NM0024-1HT17Z_Z4D1JQBA
     /dev/disk/by-id/ata-<NEW-DRIVE-ID>
   )
   mapper_name() {
     local disk_id; disk_id=$(basename "$1")
     printf 'archive-%s' "${disk_id##*_}"
   }

   # Pre-flight: every entry must be a real device. This catches the
   # <NEW-DRIVE-ID> placeholder, any other placeholder token an operator
   # might have left in, and stale entries pointing to removed drives.
   for DISK in "${ARCHIVE_DISKS[@]}"; do
     if [ ! -e "$DISK" ]; then
       echo "ARCHIVE_DISKS entry '$DISK' does not exist — edit array before running." >&2
       exit 1
     fi
   done

   PASS=$(op read 'op://Homelab/ZFS Pool archive LUKS Passphrase/password')
   for DISK in "${ARCHIVE_DISKS[@]}"; do
     NAME=$(mapper_name "$DISK")
     printf '%s' "$PASS" | sudo cryptsetup open "$DISK" "$NAME" -
   done
   sudo zpool import archive
   ```

   Once stable, codify the unlock as `/usr/local/sbin/archive-unlock.sh`
   + add a USB-stick recovery copy of the passphrase per
   `docs/17-disaster-recovery.md`.

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

Earlier guidance was to skip ZFS-native encryption on `tank`/`ssd`/`nvme`. That
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
