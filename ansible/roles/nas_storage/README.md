# NAS Storage Role

Manages ZFS pool properties, NFS exports, Samba shares, mergerfs media directory, media-mover automation, and SMART monitoring on pve-nas-01. Does **not** create or destroy ZFS pools.

## What This Role Manages

### ZFS
- Pool property configuration (compression, atime, xattr)
- Dataset creation and management
- Automated periodic snapshots via zfs-auto-snapshot

### NFS
- NFS server installation and configuration
- Exports configuration for k3s nodes and services
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
- Systemd timer (runs at 03:30 daily; overridable via media_mover_schedule)
- Moves completed downloads from nvme to tank
- Preserves directory structure and permissions

### SMART Monitoring
- Smartmontools configuration
- Email alerts via SMTP relay
- Daily short tests, monthly long tests (staggered per pool)

## Configuration

```yaml
# ZFS pools (never created/destroyed by Ansible — manually built).
# The role only sets properties + creates datasets under existing pools.
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
  - path: /export                     # NFSv4 pseudo-root (left plaintext)
    clients:
      - spec: "192.168.0.200/29"
        options: "rw,sync,hide,crossmnt,no_subtree_check,fsid=0,sec=sys,root_squash"

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
│  ├─ tank (122TB usable, raidz2)
│  ├─ ssd (10.9TB, raidz1)
│  ├─ nvme (2.27TB, single)
│  └─ archive (21.8TB, raidz1)
├─ Mergerfs: /mnt/media
│  └─ Combines: tank/media + nvme/media
├─ NFS: Exports to k3s nodes
├─ Samba: Shares to LAN
├─ Media Mover: nvme → tank (03:30 daily)
└─ SMART: Monitoring + alerts
```

## Files

- `tasks/main.yml` - Main orchestration
- `tasks/zfs.yml` - ZFS configuration
- `tasks/nfs.yml` - NFS exports
- `tasks/samba.yml` - Samba shares
- `tasks/mergerfs.yml` - Unified media directory
- `tasks/media_mover.yml` - Automated file mover
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

Ansible only sets properties and creates datasets.

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
