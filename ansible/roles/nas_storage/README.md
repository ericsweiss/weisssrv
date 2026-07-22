# NAS Storage Role

Manages ZFS pool properties, NFS exports, Samba shares, mergerfs media directory, media-mover and archive-backup automation, a nightly swap reset, and SMART monitoring on pve-nas-01. Does **not** create or destroy ZFS pools.

## What This Role Manages

### ZFS
- Pool property configuration (compression, atime, xattr)
- Dataset verification and property enforcement (datasets are created
  manually; a missing dataset fails the deploy — see `tasks/zfs.yml`)
- Automated periodic snapshots via zfs-auto-snapshot
- Optional ARC cap: when `zfs_arc_max_bytes` is set (host_vars; 6711934976
  ≈ 6.25 GiB on pve-nas-01), renders `/etc/modprobe.d/zfs.conf` with
  `options zfs zfs_arc_max=<bytes>` and notifies an `Update initramfs`
  handler so the cap applies at early boot, before the pools import. It also
  applies the value to the running kernel via
  `/sys/module/zfs/parameters/zfs_arc_max` (compare-then-set) so a changed cap
  takes effect immediately, not only at next reboot. Empty (the default)
  leaves ARC at the ZFS default and the file unmanaged.

### NFS
- NFS server installation and configuration
- Exports configuration for k3s nodes and services
- Per-app appdata subdirectories under the `/export/appdata` bind source
  (`nas_appdata_dirs`, owned `nas_appdata_owner`:`nas_appdata_group` =
  1000:2000 to match the export's `all_squash,anonuid=1000,anongid=2000`);
  created after the mounted-dataset guard so a locked/unmounted `ssd` pool
  can never get them mkdir'd onto its bare mountpoint
- Security (restricted to nfs_clients IPSet)

### Samba
- Samba server installation
- Share configuration (media, downloads, backups)
- User management (nas user with password)
- Guest access where appropriate

### Mergerfs
- Unified media directory (/mnt/media)
- Combines tank/media, nvme/media with policies
- Automatic remount on boot

### Media Mover
- Systemd timer (production runs at 06:00 daily via `media_mover_schedule`
  in host_vars; the timer template falls back to 06:00 when unset)
- Moves aged library files (older than media_mover_min_age) off the NVMe hot
  tier to tank
- Preserves directory structure and permissions
- Load-shaped: it shares the nightly window with vzdump/scrub/smartd, so the
  unit runs deferentially — `media_mover_nice` (10) + ionice
  (`media_mover_io_class`/`media_mover_io_priority`, best-effort/7) set
  absolute priority, and the cgroup-v2 `media_mover_cpu_weight` /
  `media_mover_io_weight` (both 20, below the 100 default) deprioritize it
  only under contention. `media_mover_bwlimit` is an optional hard rsync
  throughput cap (empty = unlimited).

### Swap Reset (swap-clean)
- Nightly systemd timer (`nas_swap_clean_enabled`; production runs at 07:00 via
  `nas_swap_clean_schedule` in host_vars, AFTER the overnight backup window so it
  reclaims the swap those jobs caused). The memory-tight NAS swaps under peak
  events and that swap never self-clears, so `swap-clean.sh` resets it: shrink
  ARC for headroom, `swapoff -a`/`swapon -a`, restore ARC.
- If ARC-shrink headroom alone can't safely cover the swap, it **escalates**:
  gracefully stops heavy guests from an ordered candidate list
  (`nas_swap_clean_stop_guests`, only as many as needed), does the swapoff, and
  **always** restarts every guest it stopped (a single EXIT trap). Guests are
  only ever stopped gracefully (`qm shutdown`, never a hard kill); a guest that
  won't stop within its timeout **aborts** the reclaim rather than being forced.
- Emits `/var/lib/node_exporter/swap_clean.prom` on every exit path
  (`swap_clean_last_run_success`, `..._last_success_timestamp_seconds`,
  `..._swap_cleared_bytes`, `..._guests_stopped_count`), so an unsafe-abort night
  or a guest-stop escalation is directly alertable. See docs/06.

### SMART Monitoring
- Smartmontools configuration
- Email alerts via SMTP relay
- Daily short tests, monthly long tests (staggered per pool)

## Configuration

```yaml
# ZFS pools (never created/destroyed by Ansible — manually built).
# The role only verifies datasets exist and enforces their properties;
# dataset creation is manual too (docs/06-zfs.md, docs/32 §2a).
zfs_pools:
  - name: tank        # 6x 22TB raidz2
    datasets:
      - name: tank/media
        properties:
          mountpoint: /mnt/tank/media
          atime: "off"
          compression: zstd
          recordsize: 1M
  - name: ssd         # 3x 4TB raidz1
  - name: nvme        # 1x 4TB
  - name: archive     # 4x 6TB raidz1

# NFS exports — exports.j2 consumes this exact shape.
# Each export uses `path:` (the actual exported directory under /export, an
# NFSv4 root), with `bind_source:` mounted to it; `clients[]` carries one
# entry per CIDR/host with `spec:` + free-form `options:` string.
#
# RPC-with-TLS (NFSv4 over kernel-TLS via the nfs_tls role) has two scopes:
#   - export-level `xprtsec:` — applies to every client line.
#   - per-client `xprtsec:` — overrides the export-level value for that one
#     client, INCLUDING a falsy value to opt a single client OUT.
# The production k3s exports use "tls" (REQUIRE: reject plaintext mounts).
# The wire is encrypted because the k3s PVs MOUNT with xprtsec=tls — by
# HOSTNAME (pve-nas-01.esweiss.com, so the *.esweiss.com cert verifies; an IP
# mount fails the TLS handshake). Because xprtsec is per-client, a require-TLS
# k3s line and a plaintext-only client can share one export (e.g.
# /export/media: k3s clients require TLS, HAOS .154 has no xprtsec because its
# Supervisor can't request it — see docs/24). A client line with no xprtsec is
# left at the server default (none:tls:mtls), which accepts plaintext.
# Requires nfs_tls active on the server AND on every client that mounts TLS.
nfs_exports:
  # NFSv4 pseudo-root (fsid=0): traversal-only, so read-only — clients cross
  # from here into whichever child exports are explicitly listed for them.
  # crossmnt is deliberately ABSENT: it would implicitly export every child
  # filesystem bound under /export with THIS line's options (plaintext, no
  # xprtsec), bypassing the per-child client lists and TLS requirements.
  - path: /export                     # NFSv4 pseudo-root (left plaintext)
    clients:
      - spec: "192.168.0.200/29"
        options: "ro,sync,hide,no_subtree_check,fsid=0,sec=sys,root_squash"

  - path: /export/appdata             # k3s-only -> export-level require TLS
    bind_source: /mnt/ssd/appdata
    owner: 1000
    group: 2000
    mode: "02775"
    xprtsec: "tls"                    # every client line gets xprtsec=tls (require)
    clients:
      - spec: "192.168.0.200/29"
        options: "rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=2000,fsid=11"

  - path: /export/media               # mixed: per-client require TLS
    bind_source: /mnt/media
    owner: 1000
    group: 2000
    mode: "02775"
    clients:
      - spec: "192.168.0.200/29"
        options: "rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=2000,fsid=20"
        xprtsec: "tls"                # k3s client: require TLS, reject plaintext
      - spec: "192.168.0.154/32"      # HAOS: no xprtsec -> plaintext accepted
        options: "ro,sync,no_subtree_check,root_squash,fsid=20"

# Samba shares (list of dicts, not a map)
samba_shares:
  - name: media
    path: /mnt/tank/media
    comment: Media library
    browseable: true
    read_only: false
    guest_ok: false
    valid_users: "nas"
    force_group: "media"
    create_mask: "0664"
    directory_mask: "2775"

# ZFS ARC cap (defaults/main.yml; pinned per host in host_vars — the
# memory-tight pve-nas-01 caps it, compute hosts leave it empty)
zfs_arc_max_bytes: ""

# Media-mover load-shaping (defaults/main.yml; see Media Mover above)
media_mover_nice: 10
media_mover_io_class: best-effort
media_mover_io_priority: 7
media_mover_cpu_weight: 20
media_mover_io_weight: 20
media_mover_bwlimit: ""               # rsync --bwlimit, e.g. "50m"; empty = unlimited

# Per-app appdata subdirectories on the /export/appdata bind source.
# Zvol-backed datasets (authentik/mealie postgres, prometheus, loki) are NOT
# here — those are separate ext4-on-zvol mounts, not NFS subdirectories.
nas_appdata_base: /mnt/ssd/appdata
nas_appdata_owner: 1000
nas_appdata_group: 2000
nas_appdata_mode: "0775"
# nas_appdata_dirs: the per-app list lives in defaults/main.yml (authoritative).
```

Mergerfs unifies `/mnt/nvme/media` (hot) + `/mnt/tank/media` (cold) at
`/mnt/media`; that path is bind-mounted into `/export/media` for NFS clients.
See `ansible/inventories/prod/host_vars/pve-nas-01.yml` for the full
production set of exports, shares, and mergerfs branches.

## Deployment

```bash
# Deploy NAS configuration
task storage:deploy

# Deploy to pve-nas-01
ansible-playbook ansible/playbooks/storage.yml
```

## Architecture

```
pve-nas-01
├─ ZFS Pools
│  ├─ tank (6x 22TB raidz2, ~88TB usable)
│  ├─ ssd (3x 4TB raidz1, ~8TB usable)
│  ├─ nvme (1x 4TB, ~2.27TB)
│  └─ archive (4x 6TB raidz1, ~18TB usable)
├─ Mergerfs: /mnt/media
│  └─ Combines: tank/media + nvme/media
├─ NFS: Exports to k3s nodes
├─ Samba: Shares to LAN
├─ Media Mover: nvme → tank (06:00 daily, load-shaped)
├─ Archive backup: archive-backupctl → archive pool (06:30 nightly)
├─ Swap reset: swap-clean → ARC-shrink + optional graceful guest-stop (07:00 daily)
└─ SMART: Monitoring + alerts
```

## Files

- `tasks/main.yml` - Main orchestration
- `tasks/zfs.yml` - ZFS configuration (incl. the ARC modprobe.d cap)
- `tasks/nfs.yml` - NFS exports + per-app appdata subdirectories
- `tasks/samba.yml` - Samba shares
- `tasks/mergerfs.yml` - Unified media directory
- `tasks/media_mover.yml` - Automated file mover
- `tasks/archive_backup.yml` - Nightly ZFS replication to the archive pool
- `tasks/swap_clean.yml` - Nightly swap reset timer (`swap-clean.{sh,service,timer}.j2`)
- `tasks/smartd.yml` - SMART monitoring
- `templates/*` - Configuration templates

## Dependencies

- ZFS pools must exist (created manually)
- `base` role (for mail relay configuration)

## CRITICAL: ZFS Pool Creation

**NEVER create/destroy pools via Ansible.** Pools are too critical and must be created manually:

```bash
# Example tank pool creation (DO NOT RUN VIA ANSIBLE)
zpool create -f tank raidz2 \
  /dev/disk/by-id/... \
  /dev/disk/by-id/... \
  # ... (6 disks total)
```

Ansible only sets pool/dataset properties and verifies the expected datasets
exist (they are created manually alongside the pool; a missing one fails the
deploy).

## Testing

```bash
# Test NFS from k3s node (clients mount /media off the NFSv4 root /export)
showmount -e pve-nas-01
# Plaintext mount (any name/IP works):
mount -t nfs4 pve-nas-01:/media /mnt/test
# TLS mount MUST use a name the *.esweiss.com cert covers (an IP fails the
# handshake — "Certificate owner unexpected"):
mount -t nfs4 -o xprtsec=tls pve-nas-01.esweiss.com:/media /mnt/test

# Test Samba from Windows/Mac
smb://pve-nas-01/media

# Check mergerfs
df -h /mnt/media
ls /mnt/media

# Check media-mover
systemctl status media-mover.timer
journalctl -u media-mover.service

# Check SMART
smartctl -a /dev/sda
```
