# File Services

The NAS server (pve-nas-01) provides file sharing via NFS and Samba.

## Storage Layout

### ZFS Datasets

| Dataset | Mount Point | Purpose |
|---------|-------------|---------|
| tank/media | /mnt/tank/media | Media library (cold tier) |
| tank/share | /mnt/tank/share | General file share |
| tank/downloads | /mnt/tank/downloads | Download staging |
| tank/proxmox | /mnt/tank/proxmox | VM backups |
| tank/pve | /mnt/tank/pve | VM disk images |

### Tiered Storage with MergerFS

Fast NVMe storage is merged with slower HDD storage:

```
/mnt/nvme/downloads/media-hot  (NVMe, hot)
           +
/mnt/tank/media                (HDD, cold)
           |
           v
/mnt/nvme/downloads/media      (MergerFS union)
           |
           v
/export/media                  (NFS bind mount)
```

**fstab entry:**
```
/mnt/nvme/downloads/media-hot:/mnt/tank/media  /mnt/nvme/downloads/media  fuse.mergerfs  \
  defaults,allow_other,default_permissions,use_ino,cache.files=off,dropcacheonclose=true,\
  category.create=ff,category.action=epall,moveonenospc=true,minfreespace=100G,fsname=media  0  0
```

### Media Mover

A daily cron job moves aged files from NVMe to HDD:

```bash
# /usr/local/sbin/media-mover.sh
# Runs daily at 2 AM
# Moves files older than 24 hours from hot to cold tier
```

## Permission Model

### Users and Groups

The NAS uses a consistent UID/GID model for shared access:

| Name | Type | ID | Purpose |
|------|------|----|----|
| eric | User | UID 1000 | Primary owner of files |
| media | Group | GID 2000 | Shared access group |
| nas | User | - | Samba access user (member of media) |

### Setgid Directories

All shared directories use the **setgid bit (2xxx)** to ensure group inheritance:

```bash
# Directory with setgid: mode 02775
drwxrwsr-x  eric  media  /mnt/tank/share

# Files created in this directory automatically get group "media"
-rw-rw-r--  alice  media  /mnt/tank/share/newfile.txt
```

This ensures:
- All files created inherit the `media` group (GID 2000)
- Users/services in the `media` group can read and write files
- No manual `chgrp` needed after file creation

### NFS Export Permissions

All NFS exports are configured with proper ownership:

```yaml
owner: 1000  # eric
group: 2000  # media
mode: "02775"  # rwxrwsr-x (setgid for group inheritance)
```

**Read-only exports** use mode `02755` (read-only for non-owners).

### Application Setup

When configuring applications (Kubernetes pods, Docker containers, etc.):

1. **Set user/group in container spec**:
   ```yaml
   securityContext:
     runAsUser: 1000
     runAsGroup: 2000
     fsGroup: 2000  # Ensures new files get GID 2000
   ```

2. **Or use supplemental groups**:
   ```yaml
   securityContext:
     supplementalGroups:
       - 2000  # media group
   ```

3. **Verify permissions** after deployment:
   ```bash
   # Files should show:
   -rw-rw-r-- 1000 2000 filename
   # Directories should show:
   drwxrwsr-x 1000 2000 dirname
   ```

### Why This Matters

Without proper group setup:
- Containers running as different UIDs can't access each other's files
- Manual permission fixes are needed constantly
- Shared storage becomes fragile and error-prone

With setgid + GID 2000:
- All applications can read/write shared data
- Permission inheritance happens automatically
- Consistent access model across NFS and Samba

## NFS Exports

### Export Structure

```
/export                    (NFSv4 root, fsid=0)
  +-- appdata              (bind: /mnt/ssd/appdata)
  +-- share                (bind: /mnt/tank/share)
  +-- downloads            (bind: /mnt/nvme/downloads)
  +-- media                (bind: /mnt/nvme/downloads/media)
  +-- tank-proxmox         (bind: /mnt/tank/proxmox)
```

### Access Control

| Export | Clients | Access |
|--------|---------|--------|
| /export | Proxmox hosts, k3s VMs | RW, crossmnt |
| /export/appdata | k3s VMs (192.168.0.200/29) | RW |
| /export/share | k3s VMs | RW |
| /export/downloads | k3s VMs | RW |
| /export/media | k3s VMs, home.esweiss.com | RO |
| /export/tank-proxmox | Proxmox hosts only | RW, no_root_squash |

### Mounting from Clients

```bash
# NFSv4 mount (recommended)
mount -t nfs4 192.168.0.102:/media /mnt/media

# In fstab
192.168.0.102:/media  /mnt/media  nfs4  defaults,_netdev  0  0
```

## Samba Shares

### Shares

| Share | Path | Access | Group |
|-------|------|--------|-------|
| share | /mnt/tank/share | nas user RW | media (forced) |
| media | /mnt/tank/media | nas user RW | media (forced) |

All files created via Samba are automatically assigned to the `media` group and get mode 0664 (files) or 2775 (directories).

### User Management

The `nas` user is automatically created by Ansible with:
- Password stored in 1Password: `op://Homelab/Samba NAS User/password`
- Member of `media` group (GID 2000)
- No login shell (security)

To manage Samba users manually:

```bash
# List Samba users
pdbedit -L

# Change Samba password
smbpasswd nas

# Disable Samba user
smbpasswd -d nas
```

### Connecting

```
smb://192.168.0.102/share
\\192.168.0.102\share
```

## Ansible Role

Deploy file services with:

```bash
task deploy:storage
# Or directly:
ansible-playbook -i inventories/prod playbooks/storage.yml
```

### Role: `nas_storage`

Manages:
- ZFS dataset properties
- MergerFS mounts
- NFS exports
- Samba shares
- Media mover script and timer

## Troubleshooting

### NFS

```bash
# Check exports
exportfs -v

# Re-export after changes
exportfs -ra

# Check NFS server status
systemctl status nfs-server

# Debug client mount issues
showmount -e 192.168.0.102
mount -v -t nfs4 192.168.0.102:/share /mnt/test
```

### Samba

```bash
# Test config
testparm

# Check status
smbstatus

# Restart services
systemctl restart smbd nmbd
```

### MergerFS

```bash
# Check mounts
findmnt -t fuse.mergerfs

# Remount
mount -a -t fuse.mergerfs

# Check pool distribution
du -sh /mnt/nvme/downloads/media-hot /mnt/tank/media
```
