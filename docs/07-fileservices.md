# File Services

The NAS server (pve-nas-01) provides file sharing via NFS and Samba.

## Storage Layout

### ZFS Datasets

| Dataset | Mount Point | Purpose |
|---------|-------------|---------|
| tank/media | /mnt/tank/media | Media library (cold tier) |
| tank/share | /mnt/tank/share | General file share |
| tank/proxmox | /mnt/tank/proxmox | VM backups |
| tank/pve | /mnt/tank/pve | VM disk images |
| nvme/media | /mnt/nvme/media | Hot tier (downloads land here inside the mergerfs union) |

### Tiered Storage with MergerFS

Fast NVMe storage is merged with slower HDD storage:

```
/mnt/nvme/media                (NVMe, hot)
           +
/mnt/tank/media                (HDD, cold)
           |
           v
/mnt/media                     (MergerFS union)
           |
           v
/export/media                  (NFS bind mount)
```

**fstab entry:**
```
/mnt/nvme/media:/mnt/tank/media  /mnt/media  fuse.mergerfs  \
  defaults,allow_other,default_permissions,use_ino,inodecalc=path-hash,noforget,\
  cache.files=off,dropcacheonclose=true,\
  category.create=ff,category.action=epall,moveonenospc=true,minfreespace=100G,fsname=media  0  0
```

### Media Mover

A systemd timer (`media-mover.timer`, daily at 02:00) runs
`/usr/local/sbin/media-mover.sh`, which moves files older than 12 hours
(`media_mover_min_age`) from `/mnt/nvme/media/library` to
`/mnt/tank/media/library`.

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
  +-- media                (bind: /mnt/media - mergerfs union)
  +-- tank-proxmox         (bind: /mnt/tank/proxmox)
```

### Access Control

| Export | Clients | Access | Transport |
|--------|---------|--------|-----------|
| /export | Proxmox hosts, k3s VMs, .154 | RW, crossmnt | plaintext (fsid=0 root; no `xprtsec`) |
| /export/appdata | k3s VMs (192.168.0.200/29, 192.168.0.220/29) | RW | require TLS (`xprtsec=tls`); plaintext rejected |
| /export/share | k3s VMs | RW | require TLS (`xprtsec=tls`); plaintext rejected |
| /export/media | k3s VMs, .154 (Home Assistant) | RW (k3s), RO (.154) | require TLS (`xprtsec=tls` on k3s lines); **.154 plaintext** via its own line (HAOS can't do `xprtsec`) |
| /export/tank-proxmox | Proxmox hosts only | RW, no_root_squash | plaintext (TLS deferred) |

**The k3s client lines require TLS.** They carry `xprtsec=tls`, so a plaintext
mount from those CIDRs is rejected; the k3s PVs *mount* with `xprtsec=tls`, **by
hostname** (so the `*.esweiss.com` cert verifies). `xprtsec` is applied per
client line, so the require-TLS k3s lines coexist with the plaintext `.154`
(HAOS) line on `/export/media`. `tlshd` is live on `pve-nas-01` and all six k3s
agents, so the TLS path always completes.

**Note**: Plex LXC (.152) uses a bind mount (`/mnt/media`) directly, not NFS.

### Mounting from Clients

```bash
# NFSv4 mount (recommended)
mount -t nfs4 192.168.0.102:/media /mnt/media

# In fstab
192.168.0.102:/media  /mnt/media  nfs4  defaults,_netdev  0  0
```

**TLS clients MUST mount by hostname, never by IP.** A `xprtsec=tls` mount
verifies the server certificate, whose only SAN is the wildcard
`*.esweiss.com`. Mounting `192.168.0.102:/media` with `xprtsec=tls` fails the
handshake (`tlshd`: "Certificate owner unexpected"). Mount
`pve-nas-01.esweiss.com:/media` instead — AdGuard resolves it to .102 and the
name matches the wildcard:

```bash
mount -t nfs4 -o xprtsec=tls pve-nas-01.esweiss.com:/media /mnt/media
```

The k3s NFS PVs already use `server: pve-nas-01.esweiss.com` for this reason
(plaintext clients like HAOS may keep using the IP).

## Transport Security

- **Samba**: every SMB session is encrypted — `smb encrypt = required` and
  `server min protocol = SMB3_00` in smb.conf.
- **NFS**: NFSv4-over-TLS (`xprtsec=tls`) is enabled for k3s clients via the
  `nfs_tls` role (`nfs_tls_enabled: true` on `pve-nas-01` + every k3s agent —
  `tlshd` runs everywhere a pod might schedule). The k3s client lines of
  `/export/{appdata,share,media}` carry `xprtsec=tls` — **require TLS**: a
  plaintext mount from those CIDRs is rejected. The k3s PVs *mount* with
  `xprtsec=tls`, **by hostname** (`pve-nas-01.esweiss.com`, so the
  `*.esweiss.com` cert verifies — an IP mount fails the handshake).
  Two documented plaintext exceptions:
  - **HAOS (.154) on `/export/media`** — Home Assistant's Supervisor hardcodes
    its NFS mount options and the appliance ships no `tlshd`, so it cannot
    speak `xprtsec`. Its client line omits `xprtsec` entirely and stays
    plaintext; `xprtsec` is per-client, so it is not locked out by the
    require-TLS k3s lines on the same export. See docs/24.
  - **Proxmox `tank-proxmox` backup target** — Proxmox-managed NFS storage;
    TLS rollout deferred (see docs/16).

  The fsid=0 `/export` root carries no `xprtsec` (HAOS and Proxmox traverse
  it). `xprtsec` is applied per client line, so the require-TLS k3s lines and a
  plaintext-only client (.154) share one export. See
  `ansible/roles/nfs_tls/README.md` and docs/06's in-transit matrix.

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
task storage:deploy
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
du -sh /mnt/nvme/media /mnt/tank/media
```
