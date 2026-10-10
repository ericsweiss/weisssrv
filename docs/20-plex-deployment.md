# Plex Media Server Deployment

This document covers the deployment and configuration of Plex Media Server as an unprivileged LXC container on pve-nas-01.

## Overview

Plex Media Server runs in an unprivileged LXC container on pve-nas-01, providing media streaming services for the homelab. The container has direct access to the NAS storage via bind mounts, with proper UID/GID mapping to access media files.

### Key Features

- **Unprivileged container**: Enhanced security with UID mapping
- **Direct storage access**: Bind mounts to NVMe, SSD, and mergerfs storage
- **Intel Arc B580 GPU**: Hardware-accelerated transcoding via GPU passthrough
- **NVMe transcoding**: Fast transcoding workspace via NVMe scratch space
- **Traefik ingress**: TLS termination with automatic certificates
- **Split-horizon DNS**: Internal (plex.esweiss.com) and external (plex.ericsweiss.com) access

## Architecture

```
                          Internet
                              |
                    [Port 32400 Forward]
                              |
         +--------------------+--------------------+
         |                                         |
    Traefik Public                         Traefik Internal
   (10.0.10.100)                        (10.0.10.101)
         |                                         |
plex.ericsweiss.com                      plex.esweiss.com
(external domain)                        (internal domain)
         |                                         |
         +--------------------+--------------------+
                              |
                    +---------v---------+
                    |   Plex LXC (152)  |
                    |   pve-nas-01      |
                    |  + Intel Arc GPU  |
                    +---------+---------+
                              |
         +--------------------+--------------------+
         |                    |                    |
    /config              /transcode            /media
     (SSD)                (NVMe)             (mergerfs)
```

### Container Specifications

| Resource | Value |
|----------|-------|
| **Container ID** | 152 |
| **IP Address** | 10.0.10.152 |
| **Hostname** | plex |
| **Proxmox Host** | pve-nas-01 |
| **CPU Cores** | 4 |
| **Memory** | 8192 MB |
| **Swap** | 2048 MB |
| **Root Disk** | 64 GB (`ssd` — encrypted) |
| **OS** | Debian 13 (trixie) |

Source of truth: `ansible/inventories/prod/host_vars/plex.yml` — this table is a
convenience copy.

CT 152 runs `onboot=0` on purpose: its rootfs and the `/config` bind
(`/mnt/ssd/appdata/plex`) both live on the encrypted `ssd` pool, so it is started
by `pve-start-encrypted-guests.service` after `zfs-mount-encrypted.service`
unlocks the pool, not by `pve-guests` at boot. Mechanism:
[docs/32-zfs-encryption.md](32-zfs-encryption.md).

## Prerequisites

Before deploying Plex, ensure the following prerequisites are met. The deployment will fail if these are not satisfied.

### Quick Prerequisites Checklist

Run these verification commands from your laptop before deploying:

```bash
# 1. Sign into 1Password (required for secrets)
eval $(op signin)

# 2. Verify 1Password integration works
op read "op://Homelab/SSH Key/public key" > /dev/null && echo "OK: 1Password connected"

# 3. Test SSH connectivity to pve-nas-01
ssh eric@10.0.10.102 "echo 'OK: SSH to pve-nas-01 works'"

# 4. Verify storage directories exist
ssh eric@10.0.10.102 "ls -la /mnt/ssd/appdata/plex /mnt/nvme/fast/plex-transcode /mnt/media 2>&1"

# 5. Verify GPU is available
ssh eric@10.0.10.102 "ls -la /dev/dri/ && getent group render video"
```

If any check fails, see the detailed sections below.

---

### 1. 1Password Environment Variables

The Plex deployment uses 1Password for secrets (SSH keys). You must be signed into 1Password CLI:

```bash
# Sign into 1Password
eval $(op signin)

# Verify access to required secrets
op read "op://Homelab/SSH Key/public key"
```

**Required 1Password items** (should already exist from base infrastructure):
- `SSH Key` - Contains the public key for SSH access

### 2. Storage Directories on pve-nas-01

The container bind mounts require these directories to exist **before** deployment:

```bash
# SSH to pve-nas-01
ssh eric@10.0.10.102

# Create Plex directories with correct ownership
sudo mkdir -p /mnt/ssd/appdata/plex
sudo mkdir -p /mnt/nvme/fast/plex-transcode

# Set ownership (eric:media = 1000:2000)
sudo chown -R 1000:2000 /mnt/ssd/appdata/plex
sudo chown -R 1000:2000 /mnt/nvme/fast/plex-transcode

# Set permissions (rwxrwsr-x with setgid)
sudo chmod 2775 /mnt/ssd/appdata/plex
sudo chmod 2775 /mnt/nvme/fast/plex-transcode

# Verify mergerfs mount exists (should be automatic from NAS storage setup)
ls -la /mnt/media
```

**If mergerfs mount is missing**, ensure the NAS storage role has been deployed:
```bash
task storage:deploy
```

### 3. DNS

Both names are codified — nothing to add by hand.

- Internal: `plex.esweiss.com` and `plex-direct.esweiss.com` are declared in
  `adguard_home_rewrites` (`ansible/inventories/prod/group_vars/dns.yml`), with a
  matching PTR rewrite for `10.0.10.152`. Apply with `task dns:deploy`. The
  `adguard_home` role deletes any rewrite not in the codified list, so an entry
  added in the AdGuard UI is reverted on the next deploy.
- External: `plex.ericsweiss.com` is managed by external-dns from the Traefik
  IngressRoute.

### 4. WAN port forward

`32400/TCP -> 10.0.10.152` is codified in `terraform/unifi/networks.tf`
(`port_forwards.plex`) and applied under supervision — see
[docs/46](46-unifi-network.md) § Port forwards. A matching `iot-to-homelab-plex`
zone policy lets IoT-VLAN clients reach `:32400`. Verify with
`task terraform:unifi-plan`; a change made in the UniFi console shows up there as
drift.

### 5. Intel Arc GPU Available on pve-nas-01

The GPU passthrough configuration dynamically detects the host's video and render group GIDs:

```bash
# SSH to pve-nas-01
ssh eric@10.0.10.102

# Verify GPU devices exist
ls -la /dev/dri/

# Should show renderD128 (and possibly card0/card1)
# Example output:
# crw-rw---- 1 root video  226,   0 Jan  5 12:00 card0
# crw-rw---- 1 root render 226, 128 Jan  5 12:00 renderD128

# Check the video and render group GIDs (used for container passthrough)
getent group video render
# Expected output shows the GIDs that will be automatically used:
# video:x:44:
# render:x:108:
```

**Note**: The GID values (44, 108, etc.) vary by system. The Ansible role automatically detects these values - no manual configuration needed.

## Deployment

### Full Deployment

```bash
# Deploy Plex (provisions container + installs Plex)
task plex:deploy

# Or with verbose output
task plex:deploy -- -v
```

### Check Mode (Dry Run)

```bash
# See what would change without making changes
task plex:check
```

### Manual Steps (if needed)

```bash
# Equivalent of task plex:deploy
ansible-playbook -i ansible/inventories/prod ansible/playbooks/plex.yml

# Dry-run the same thing (equivalent of task plex:check)
ansible-playbook -i ansible/inventories/prod ansible/playbooks/plex.yml --check --diff
```

`plex.yml` declares **no tags**, and both plays target `hosts: plex` (play 1
provisions the LXC through `proxmox_lxc`, which `delegate_to`s the Proxmox host).
There is therefore no `--tags proxmox_lxc` / `--skip-tags proxmox_lxc` split —
those would match nothing and Ansible would exit 0 having done nothing. The
provisioning play is idempotent, so a full re-run is the normal path.

## Storage Configuration

### Bind Mounts

| Host Path | Container Path | Purpose |
|-----------|----------------|---------|
| /mnt/ssd/appdata/plex | /config | Plex database, metadata, settings |
| /mnt/nvme/fast/plex-transcode | /transcode | Temporary transcoding files |
| /mnt/media | /media | Media library (mergerfs union of NVMe + HDD) |

### Storage Characteristics

- **Config (/config)**: SSD RAIDZ1 - fast random I/O for database operations
- **Transcode (/transcode)**: NVMe - maximum speed for transcoding workloads (with GPU acceleration)
- **Media (/media)**: MergerFS (NVMe hot tier + HDD cold storage) - balanced performance, read-write access for Plex metadata operations

## GPU Hardware Transcoding

### Intel Arc B580 Configuration

The Plex container has direct access to the Intel Arc B580 GPU on pve-nas-01 for hardware-accelerated transcoding:

- **GPU Device**: `/dev/dri` (includes renderD128 for compute)
- **Drivers**: `intel-media-va-driver-non-free` (VA-API support for Arc)
- **User Groups**: plex user is member of `video` and `render` groups
- **Benefits**: Significantly faster transcoding with lower CPU usage
- **Shared card**: the same `/dev/dri` is also passed into the `immich-ml` LXC
  (vmid 158) for Immich's OpenVINO ML inference — LXC device passthrough is
  non-exclusive and the kernel `xe` driver arbitrates between the two guests
  (see [docs/36-immich.md](36-immich.md), "GPU machine learning")

### Verifying GPU Access

After deployment, verify GPU is accessible in the container:

```bash
# SSH to Plex container
ssh eric@10.0.10.152

# Check GPU devices are present
ls -la /dev/dri/

# Verify VA-API driver loads
vainfo

# Should show Intel Arc GPU with supported codecs
# Example output:
# libva info: VA-API version 1.20.0
# libva info: Trying to open /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so
# libva info: Found init function __vaDriverInit_1_20
# libva info: va_openDriver() returns 0
# vainfo: VA-API version: 1.20 (libva 2.20.0)
# vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 23.4.0
```

### Enabling in Plex

After deployment, enable hardware transcoding in Plex:

1. Go to Settings > Transcoder
2. Check "Use hardware acceleration when available"
3. Select "Intel Quick Sync Video" or "VAAPI"
4. Save changes

### LAN Networks (segmented VLANs)

**Settings > Network > LAN Networks** must list every client VLAN:

```
10.0.10.0/24,10.0.20.0/24,10.0.30.0/24,100.64.0.0/10
```

Plex treats a client as local only if its address falls in that list. The network
is segmented into homelab VLAN 10, Home VLAN 20 and IoT VLAN 30
([docs/46](46-unifi-network.md)), so phones, laptops and TVs reach the server
from a different subnet, and the tailnet `100.64.0.0/10` is the fourth local
range — a Tailscale client left out of it is charged as a remote stream. Without
this they count as *remote*: remote quality caps, transcoding where there used
to be direct play, and sessions charged against the remote-streaming limits.

This value is Plex UI state, not code — nothing in this repo reconciles it.
Re-check it after any renumber, alongside the other stored-address holders in
[docs/12](12-runbooks.md) § After an addressing change.

### Monitoring GPU Usage

```bash
# Monitor GPU usage in real-time
ssh eric@10.0.10.152 "sudo intel_gpu_top"

# Check which processes are using the GPU
ssh eric@10.0.10.152 "sudo fuser -v /dev/dri/renderD128"
```

## UID/GID Mapping

### The Challenge

Unprivileged LXC containers use UID/GID mapping for security. By default:
- Container UID 0 maps to host UID 100000
- Container UID 1000 maps to host UID 101000

This means the container cannot access files owned by host UID 1000 (eric) or GID 2000 (media).

### The Solution

We configure custom UID/GID mapping in the container configuration to allow specific UIDs/GIDs to pass through unchanged. The Ansible role **automatically detects** the host's video and render group GIDs and generates the appropriate mapping.

> The role writes the `lxc.idmap` block and the `/dev/dri` passthrough **when the
> container is created**. Both are create-time only (`proxmox_lxc` role), so
> changing them in inventory for an existing container is a no-op and repairing
> them on CT 152 means editing `/etc/pve/lxc/152.conf` and restarting the
> container by hand — see § Troubleshooting. The in-container group membership
> (`video`, `render`, `media`) *is* reconciled on every `task plex:deploy`.

```
# /etc/pve/lxc/152.conf (added automatically by Ansible)

# UID mapping
lxc.idmap: u 0 100000 1000      # UIDs 0-999 -> 100000-100999
lxc.idmap: u 1000 1000 1        # UID 1000 -> 1000 (passthrough)
lxc.idmap: u 1001 101001 64535  # UIDs 1001+ -> 101001+

# GID mapping (dynamically generated based on host video/render GIDs)
# Example with video=44, render=108:
lxc.idmap: g 0 100000 44        # GIDs 0-43 -> 100000-100043
lxc.idmap: g 44 44 1            # GID 44 (video) -> passthrough
lxc.idmap: g 45 100045 63       # GIDs 45-107 -> 100045-100107
lxc.idmap: g 108 108 1          # GID 108 (render) -> passthrough
lxc.idmap: g 109 100109 1891    # GIDs 109-1999 -> 100109-101999
lxc.idmap: g 2000 2000 1        # GID 2000 (media) -> passthrough
lxc.idmap: g 2001 102001 63535  # GIDs 2001+ -> 102001+

# GPU device passthrough
lxc.cgroup2.devices.allow: c 226:* rwm     # Allow DRI devices (character device 226)
lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir
```

**Note**: The exact GID mapping entries vary based on the host system's video and render group GIDs. The mapping ensures that video, render, and media (2000) GIDs pass through unchanged while all other GIDs are offset to the unprivileged range (100000+).

### Container User Setup

Inside the container:
- The `plex` user's **primary** group is overridden to `media` by the role's
  systemd drop-in (`Group=media` + `UMask=0002`). This is load-bearing: the
  mergerfs mount runs with `default_permissions`, which honours only the primary
  gid, so a supplementary `media` membership is silently dropped and DVR
  recordings fail. Note `Group=` replaces the primary group rather than adding to
  it.
- The `plex` user is also a member of the `media` group (GID 2000)
- The `plex` user is added to the `video` and `render` groups (for GPU access)
- This allows Plex to read media files owned by `eric:media` on the host
- GPU access enables hardware-accelerated transcoding
- Transcoding directory is writable by the plex user

## Accessing Plex

### Internal Access

- **URL**: https://plex.esweiss.com
- **Direct**: http://10.0.10.152:32400/web

### External Access

- **URL**: https://plex.ericsweiss.com
- **Port**: 32400 (forwarded from router)

### Initial Setup

1. Access Plex at https://plex.esweiss.com (or direct IP)
2. Sign in with your Plex account
3. Complete the server setup wizard
4. Configure libraries pointing to `/media` subdirectories

### Library Configuration

| Library Type | Path in Plex |
|--------------|--------------|
| Movies | /media/movies |
| TV Shows | /media/tv |
| Music | /media/music |

## Backup and restore

Plex `/config` lives on `ssd/appdata` (the LXC bind `mp0` ->
`/mnt/ssd/appdata/plex`), so it rides the `ssd/appdata -> archive` replication
and the nightly restic walk into B2. Restore it file-wise with the same recipe
as Grafana: [docs/17](17-disaster-recovery.md) § Restore Procedures and § Other
backup types. Stop `plexmediaserver`, copy the tree back, `chown -R plex:plex
/config/`, start it again.

The whole-container vzdump on `tank/proxmox` remains the bare-metal path.

## Maintenance

### Service Management

```bash
# Check status
ssh eric@10.0.10.152 "sudo systemctl status plexmediaserver"

# Restart Plex
ssh eric@10.0.10.152 "sudo systemctl restart plexmediaserver"

# View logs
ssh eric@10.0.10.152 "sudo journalctl -u plexmediaserver -f"
```

### Updates

Plex is pinned centrally in `ansible/inventories/prod/group_vars/all.yml`
(`plex_version`) and the `plex` role holds the apt package at that version, so a
bare `apt upgrade plexmediaserver` is a no-op.

```bash
task maintenance:check-versions                  # what is available
task maintenance:update-version SERVICE=plex     # rewrite the pin
# commit the pin bump on a branch and merge
task maintenance:update-plex                     # reconcile the LXC to the pin
```

`task plex:deploy` reconciles the version too, as part of the full Plex play.

### Cleanup Transcoding Cache

```bash
# Clear transcoding cache
ssh eric@10.0.10.152 "sudo rm -rf /transcode/*"
```

## Troubleshooting

### Plex Cannot Access Media

**Symptom**: Libraries show "There was a problem adding this folder" or media files not appearing.

**Check**:
```bash
# Verify bind mounts are active
ssh eric@10.0.10.152 "df -h | grep -E '/config|/transcode|/media'"

# Check permissions inside container
ssh eric@10.0.10.152 "ls -la /media"

# Verify plex user is in media group
ssh eric@10.0.10.152 "id plex"
```

**Fix**: If mounts are missing, the container may need restart from Proxmox.
This fails with a confusing error if the `ssd` pool is locked — check the unlock
first (see § Container Won't Start).
```bash
ssh eric@10.0.10.102 "pct stop 152 && pct start 152"
```

### Transcoding Errors

**Symptom**: Playback fails with transcoding errors.

**Check**:
```bash
# Verify transcode directory is writable
ssh eric@10.0.10.152 "sudo -u plex touch /transcode/test && rm /transcode/test"

# Check disk space
ssh eric@10.0.10.152 "df -h /transcode"

# Check Plex logs
ssh eric@10.0.10.152 "tail -100 '/config/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log'"
```

### Container Won't Start

**Symptom**: Container fails to start, or is down after a NAS reboot.

**Check the encrypted pool first.** The rootfs and the `/config` bind both live
on `ssd`, so `pct start` cannot succeed until the pool unlocks:

```bash
ssh eric@10.0.10.102 "zfs get -H keystatus ssd; systemctl status zfs-mount-encrypted pve-start-encrypted-guests --no-pager"
```

If `keystatus` is `unavailable` the pool has not unlocked — see
[docs/32](32-zfs-encryption.md). Otherwise:

```bash
# On pve-nas-01, check container config
ssh eric@10.0.10.102 "cat /etc/pve/lxc/152.conf"

# Check if host directories exist
ssh eric@10.0.10.102 "ls -la /mnt/ssd/appdata/plex /mnt/nvme/fast/plex-transcode /mnt/media"

# Start with verbose output
ssh eric@10.0.10.102 "pct start 152 --debug"
```

### UID Mapping Issues

**Symptom**: Permission denied when accessing files despite correct ownership.

**Check**:
```bash
# Verify UID mapping in container config
ssh eric@10.0.10.102 "grep lxc.idmap /etc/pve/lxc/152.conf"

# Check actual UIDs inside container
ssh eric@10.0.10.152 "stat /media"

# Compare with host
ssh eric@10.0.10.102 "stat /mnt/media"
```

**Fix**: these stanzas are written by Ansible only when the container is
created, so on CT 152 they are repaired by hand — a redeploy will not rewrite
them.
1. Stop container: `pct stop 152`
2. Edit config: `nano /etc/pve/lxc/152.conf`
3. Add/fix lxc.idmap lines (see [UID/GID Mapping](#uidgid-mapping) section)
4. Start container: `pct start 152`

### GPU Not Accessible

**Symptom**: Hardware transcoding not working, GPU not visible in container.

**Check**:
```bash
# Verify GPU exists on host
ssh eric@10.0.10.102 "ls -la /dev/dri/"

# Check GPU passthrough config
ssh eric@10.0.10.102 "grep -E 'cgroup2.devices|mount.entry.*dri' /etc/pve/lxc/152.conf"

# Verify GPU visible in container
ssh eric@10.0.10.152 "ls -la /dev/dri/"

# Check plex user group membership
ssh eric@10.0.10.152 "groups plex"

# Test VA-API
ssh eric@10.0.10.152 "vainfo"
```

**Fix**: the passthrough stanza is written by Ansible only when the container is
created, so on CT 152 it is repaired by hand — a redeploy will not rewrite it.
1. Stop container: `ssh eric@10.0.10.102 "pct stop 152"`
2. Add GPU passthrough to config:
   ```bash
   ssh eric@10.0.10.102
   cat >> /etc/pve/lxc/152.conf <<EOF
   lxc.cgroup2.devices.allow: c 226:* rwm
   lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir
   EOF
   ```
3. Start container: `pct start 152`
4. Reconcile the in-container groups: `task plex:deploy` (the `plex` role adds
   the user to `video` and, where a render node exists, `render`).
5. Restart Plex: `ssh eric@10.0.10.152 "sudo systemctl restart plexmediaserver"`

### Hardware Transcoding Not Working

**Symptom**: GPU is visible but Plex isn't using it for transcoding.

**Check**:
```bash
# Verify VA-API driver loads correctly
ssh eric@10.0.10.152 "vainfo"

# Check Plex has access to GPU
ssh eric@10.0.10.152 "sudo -u plex ls -la /dev/dri/"

# Monitor GPU usage during transcoding
ssh eric@10.0.10.152 "sudo intel_gpu_top"
```

**Fix**:
1. Ensure hardware transcoding is enabled in Plex (Settings > Transcoder)
2. Try toggling the setting off and back on
3. Restart Plex service
4. Check Plex logs for GPU-related errors:
   ```bash
   ssh eric@10.0.10.152 "tail -100 '/config/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log' | grep -i 'hardware\|vaapi\|qsv'"
   ```

## Related documentation

- [docs/06-zfs.md](06-zfs.md) - ZFS storage configuration
- [docs/07-fileservices.md](07-fileservices.md) - NFS and Samba setup
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) - restoring `/config` from the archive or B2
- [docs/18-bootstrap-new-systems.md](18-bootstrap-new-systems.md) - LXC bootstrap process
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) - the encrypted `ssd` pool and the unlock-ordered start of CT 152
- [docs/46-unifi-network.md](46-unifi-network.md) - VLANs, the `:32400` forward and the `iot-to-homelab-plex` zone policy

## External references

- [Plex Media Server Documentation](https://support.plex.tv/articles/)
- [Proxmox LXC Unprivileged Containers](https://pve.proxmox.com/wiki/Unprivileged_LXC_containers)
- [LXC UID/GID Mapping](https://linuxcontainers.org/lxc/manpages/man5/lxc.container.conf.5.html)
