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

A systemd timer (`media-mover.timer`, daily at 06:00 —
`nas_storage_media_mover_schedule` in `host_vars/pve-nas-01.yml` is the source of truth;
role default 06:00)
runs `/usr/local/sbin/media-mover.sh`, which moves files older than 12 hours
(`nas_storage_media_mover_min_age`) from `/mnt/nvme/media/library` to
`/mnt/tank/media/library`. The service is load-shaped so a large overnight
move cannot starve Plex or backups: `Nice`/`ionice` plus cgroup-v2
`CPUWeight`/`IOWeight` (defaults 20), with an optional hard rsync cap via
`nas_storage_media_mover_bwlimit`.

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
-rw-rw-r--  eric  media  /mnt/tank/share/newfile.txt
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

## NFS Exports

### Export Structure

```
/export                    (NFSv4 root, fsid=0)
  +-- appdata              (bind: /mnt/ssd/appdata)
  +-- share                (bind: /mnt/tank/share)
  +-- media                (bind: /mnt/media - mergerfs union)
  +-- tank-proxmox         (bind: /mnt/tank/proxmox)
  +-- k3s-etcd             (bind: /mnt/ssd/k3s-etcd)
  +-- backups-apps/        (per-app logical-dump targets, bind: /mnt/tank/backups/apps/*)
        +-- authentik      (k3s agents + servers)
        +-- mealie         (k3s agents + servers)
        +-- gitlab         (.153)
        +-- immich         (.157)
        +-- nextcloud      (.156)
        +-- home-assistant (.154 — plaintext; HAOS ships no tlshd)
```

`nas_storage_exports` in `host_vars/pve-nas-01.yml` is the source of truth for
the export set, its per-client options and `xprtsec`.

### Access Control

Transport values are the short form; § Transport Security below is the single
statement of the rule.

| Export | Clients | Access | Transport |
|--------|---------|--------|-----------|
| /export | Proxmox hosts (.102-.107), k3s VMs (10.0.10.200/29 agents, .222/.223/.227 servers), and the app VMs that traverse into their own backups-apps export (.153, .154, .156, .157) | RO, no crossmnt | plaintext (fsid=0 pseudo-root, traversal only; no `xprtsec`) |
| /export/appdata | k3s VMs (10.0.10.200/29, 10.0.10.220/29, .227/32) | RW | require TLS |
| /export/share | k3s VMs (10.0.10.200/29, 10.0.10.220/29, .227/32) | RW | require TLS |
| /export/media | k3s VMs (10.0.10.200/29, 10.0.10.220/29, .227/32), .154 (Home Assistant) | RW (k3s), RO (.154) | require TLS on the k3s lines; plaintext on the .154 line |
| /export/tank-proxmox | Proxmox hosts only | RW, no_root_squash | require TLS |
| /export/k3s-etcd | k3s servers only (.222/.223/.227, as explicit /32s) | RW, no_root_squash (mode 0700) | require TLS. Off-node etcd snapshot copies, see docs/17 |
| /export/backups-apps/authentik, /export/backups-apps/mealie | k3s agents (10.0.10.200/29) + servers (10.0.10.220/29, .227/32) | RW, all_squash to eric:media | require TLS |
| /export/backups-apps/gitlab | .153 | RW, all_squash | require TLS |
| /export/backups-apps/immich | .157 | RW, all_squash | require TLS |
| /export/backups-apps/nextcloud | .156 | RW, all_squash | require TLS |
| /export/backups-apps/home-assistant | .154 | RW, all_squash | plaintext (HAOS exception, docs/24) |

The six `backups-apps/*` exports are the landing zone for the per-app logical
dumps (pg_dump / rake backup / HA native backups) that `restic_offsite` then
ships to B2 — see docs/42 and docs/17.

The `/export` pseudo-root is traversal-only: NFSv4 clients cross from it into
whichever child exports are explicitly listed for them. `crossmnt` is
deliberately absent — it would implicitly export any child bound under
`/export` to every root client *with the root line's options* (plaintext, no
`xprtsec`), bypassing the per-child client lists and TLS requirements. Any new
dataset bound under `/export` must get its own explicit export entry.

**Note**: Plex LXC (.152) uses a bind mount (`/mnt/media`) directly, not NFS.

### Mounting from Clients

```bash
# NFSv4 over TLS, by hostname — the form every client the exports admit must use
mount -t nfs4 -o xprtsec=tls pve-nas-01.esweiss.com:/media /mnt/media

# In fstab
pve-nas-01.esweiss.com:/media  /mnt/media  nfs4  defaults,_netdev,xprtsec=tls  0  0
```

**TLS clients MUST mount by hostname, never by IP.** A `xprtsec=tls` mount
verifies the server certificate, whose only SAN is the wildcard
`*.esweiss.com`. Mounting `10.0.10.102:/media` with `xprtsec=tls` fails the
handshake (`tlshd`: "Certificate owner unexpected"); AdGuard resolves
`pve-nas-01.esweiss.com` to .102 and the name matches the wildcard. The k3s NFS
PVs use `server: pve-nas-01.esweiss.com` for this reason.

**Plaintext clients only (HAOS).** The k3s client lines reject plaintext, so the
IP form below works from exactly one place — the `.154` line on `/export/media`:

```bash
mount -t nfs4 10.0.10.102:/media /mnt/media
```

## Transport Security

- **Samba**: every SMB session is encrypted — `smb encrypt = required` and
  `server min protocol = SMB3_00` in smb.conf.
- **NFS**: NFSv4-over-TLS (`xprtsec=tls`) is enabled via the `nfs_tls` role
  (`nfs_tls_enabled: true` on every k3s agent — `tlshd` runs everywhere a pod
  might schedule — and on all six Proxmox hosts, covering both the NFS server
  and the tank-proxmox mounting hosts). The k3s client lines of
  `/export/{appdata,share,media}` carry `xprtsec=tls` — **require TLS**: a
  plaintext mount from those CIDRs is rejected. `/export/k3s-etcd` (off-node
  etcd snapshot copies, k3s servers only, mode 0700) likewise requires TLS. The k3s PVs *mount* with
  `xprtsec=tls`, **by hostname** (`pve-nas-01.esweiss.com`, so the
  `*.esweiss.com` cert verifies — an IP mount fails the handshake).
  One documented plaintext exception:
  - **HAOS (.154) on `/export/media`** — Home Assistant's Supervisor hardcodes
    its NFS mount options and the appliance ships no `tlshd`, so it cannot
    speak `xprtsec`. Its client line omits `xprtsec` entirely and stays
    plaintext; `xprtsec` is per-client, so it is not locked out by the
    require-TLS k3s lines on the same export. See docs/24.

  The **Proxmox `tank-proxmox` backup target** also requires TLS: `xprtsec: tls`
  is set at the export level, so all six Proxmox client lines reject plaintext,
  and the `proxmox_backup` role pins its `storage.cfg` entry to hostname +
  `vers=4.2,xprtsec=tls`.

  The fsid=0 `/export` root carries no `xprtsec` (HAOS and Proxmox traverse
  it). `xprtsec` is applied per client line, so the require-TLS k3s lines and a
  plaintext-only client (.154) share one export. See
    weisssrv-lib `ansible_collections/weisssrv/infra/roles/nfs_tls/README.md` and
  the in-transit matrix in [docs/47](47-security-posture.md).

### Cutting a node over from plaintext to TLS

A node cannot hold a plaintext mount and an `xprtsec=tls` mount to the same
server at once. Long-running pods and orphaned kubelet mounts pin a node to
plaintext, so the cutover is:

1. Scale every Deployment on that node which mounts the server to 0. The
   `Recreate` strategy keeps a new pod from racing the old one for an RWO mount.
2. Force-unmount the orphans:

   ```bash
   mount -t nfs4 | grep -E '<server-host>|<server-ip>' | grep -v xprtsec=tls \
     | awk '{print $3}' | xargs -rn1 sudo umount -f -l
   ```

3. Scale back up. The first mount establishes the TLS session and the rest reuse
   it. Verify with `mount -t nfs4 | grep -c xprtsec=tls`.

Sweep the fleet afterwards. A client that only ever mounts plaintext is fine —
the rule is per-client internal consistency, not a fleet-wide state.

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
smb://10.0.10.102/share
\\10.0.10.102\share
```

## Ansible Role

Deploy file services with:

```bash
task storage:deploy
# Or directly:
ansible-playbook -i ansible/inventories/prod ansible/playbooks/storage.yml
```

### Role: `nas_storage`

Manages:
- ZFS dataset properties
- MergerFS mounts
- NFS exports
- Samba shares
- Media mover script and timer

Disabling a component converges it away. Turning off
`nas_storage_media_mover_enabled`, `nas_storage_swap_clean_enabled`,
`nas_storage_archive_backup_enabled`, `nas_storage_pve_cluster_backup_enabled`
or `nas_storage_backup_artifact_metrics_enabled`, or dropping MergerFS, removes
that component's units, script and `.prom` file on the next run instead of
leaving a timer firing a script the role no longer renders. Only a file carrying
`nas_storage_managed_marker` is removed, so a hand-written unit of the same name
is reported and kept. On a host that has its own units at those conventional
names, set `nas_storage_manage_absent: false`.

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
showmount -e pve-nas-01.esweiss.com
mount -v -t nfs4 -o xprtsec=tls pve-nas-01.esweiss.com:/share /mnt/test
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

---

## Related documentation

- [docs/06-zfs.md](06-zfs.md) — pool and dataset layout
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) — encryption roots and boot-time unlock
- [docs/47-security-posture.md](47-security-posture.md) — the estate-wide at-rest/in-transit matrix
- [docs/44-storage-bootstrap.md](44-storage-bootstrap.md) — building the export tree from bare pools
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) — restore procedures
