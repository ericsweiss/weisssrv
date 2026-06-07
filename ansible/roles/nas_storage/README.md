# NAS Storage Role

Manages ZFS pool properties, NFS exports, Samba shares, mergerfs media directory, media-mover automation, and SMART monitoring on pve-nas-01. Does **not** create or destroy ZFS pools.

## What This Role Manages

### ZFS
- Pool property configuration (compression, atime, xattr)
- Dataset creation and management
- Snapshots (manual, not automated)

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
- Systemd timer (runs at 3 AM daily)
- Moves completed downloads from nvme to tank
- Preserves directory structure and permissions

### SMART Monitoring
- Smartmontools configuration
- Email alerts via SMTP relay
- Daily short tests, weekly long tests

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
# entry per CIDR/host with `spec:` + free-form `options:` string. Optional
# top-level `xprtsec:` (e.g. "tls") adds RPC-with-TLS to every client line.
nfs_exports:
  - path: /export                     # NFSv4 pseudo-root
    clients:
      - spec: "192.168.0.200/29"
        options: "rw,sync,hide,crossmnt,no_subtree_check,fsid=0,sec=sys,root_squash"

  - path: /export/media               # bind-mounted from /mnt/media (mergerfs)
    bind_source: /mnt/media
    owner: 1000
    group: 2000
    mode: "02775"
    clients:
      - spec: "192.168.0.200/29"
        options: "rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=2000,fsid=20"
      - spec: "192.168.0.154/32"
        options: "ro,sync,no_subtree_check,root_squash,fsid=20"
    # xprtsec: tls    # opt-in NFSv4 over TLS (requires nfs_tls on both sides)

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
├─ Media Mover: nvme → tank (3 AM daily)
└─ SMART: Monitoring + alerts
```

## Files

- `tasks/main.yml` - Main orchestration
- `tasks/zfs.yml` - ZFS configuration
- `tasks/nfs.yml` - NFS exports
- `tasks/samba.yml` - Samba shares
- `tasks/mergerfs.yml` - Unified media directory
- `tasks/media-mover.yml` - Automated file mover
- `tasks/smart.yml` - SMART monitoring
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
mount -t nfs4 pve-nas-01:/media /mnt/test

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
