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
- Unified media directory (/tank/media/unified)
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
# ZFS pools (read-only, never created by Ansible)
zfs_pools:
  - tank  # 6x 22TB raidz2
  - ssd   # 3x 4TB raidz1
  - nvme  # 1x 4TB single
  - archive  # 4x 6TB raidz1

# NFS exports
nfs_exports:
  - path: /tank/media/unified
    clients: "{{ groups['nfs_clients'] }}"
    options: "ro,sync,no_subtree_check"

# Samba shares
samba_shares:
  media:
    path: /tank/media/unified
    readonly: true
  downloads:
    path: /nvme/media
    readonly: false
```

## Deployment

```bash
# Deploy NAS configuration
task deploy:storage

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
├─ Mergerfs: /tank/media/unified
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
# Test NFS from k3s node
showmount -e pve-nas-01
mount -t nfs pve-nas-01:/tank/media/unified /mnt/test

# Test Samba from Windows/Mac
smb://pve-nas-01/media

# Check mergerfs
df -h /tank/media/unified
ls /tank/media/unified

# Check media-mover
systemctl status media-mover.timer
journalctl -u media-mover.service

# Check SMART
smartctl -a /dev/sda
```
