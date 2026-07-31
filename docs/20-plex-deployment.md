# Plex Media Server Deployment

This document covers the deployment and configuration of Plex Media Server as an unprivileged LXC container on pve-nas-01.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Deployment](#deployment)
5. [Storage Configuration](#storage-configuration)
6. [UID/GID Mapping](#uidgid-mapping)
7. [Accessing Plex](#accessing-plex)
8. [Backup Restoration](#backup-restoration)
9. [Maintenance](#maintenance)
10. [Troubleshooting](#troubleshooting)

---

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
   (192.168.0.100)                        (192.168.0.101)
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
| **IP Address** | 192.168.0.152 |
| **Hostname** | plex |
| **Proxmox Host** | pve-nas-01 |
| **CPU Cores** | 4 |
| **Memory** | 8192 MB |
| **Swap** | 2048 MB |
| **Root Disk** | 64 GB (local-lvm) |
| **OS** | Debian 13 (trixie) |

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
ssh eric@192.168.0.102 "echo 'OK: SSH to pve-nas-01 works'"

# 4. Verify storage directories exist
ssh eric@192.168.0.102 "ls -la /mnt/ssd/appdata/plex /mnt/nvme/fast/plex-transcode /mnt/media 2>&1"

# 5. Verify GPU is available
ssh eric@192.168.0.102 "ls -la /dev/dri/ && getent group render video"
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
ssh eric@192.168.0.102

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

### 3. AdGuard DNS Records

DNS rewrite should already exist in AdGuard Home (dns-01):

| Domain | Target |
|--------|--------|
| plex.esweiss.com | 192.168.0.101 |

The external domain (plex.ericsweiss.com) is managed by external-dns via the Traefik IngressRoute.

### 4. Router Port Forward

Ensure port 32400 TCP is forwarded from the router to 192.168.0.152 (Plex LXC).

### 5. Intel Arc GPU Available on pve-nas-01

The GPU passthrough configuration dynamically detects the host's video and render group GIDs:

```bash
# SSH to pve-nas-01
ssh eric@192.168.0.102

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
ssh eric@192.168.0.152

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

### Monitoring GPU Usage

```bash
# Monitor GPU usage in real-time
ssh eric@192.168.0.152 "sudo intel_gpu_top"

# Check which processes are using the GPU
ssh eric@192.168.0.152 "sudo fuser -v /dev/dri/renderD128"
```

## UID/GID Mapping

### The Challenge

Unprivileged LXC containers use UID/GID mapping for security. By default:
- Container UID 0 maps to host UID 100000
- Container UID 1000 maps to host UID 101000

This means the container cannot access files owned by host UID 1000 (eric) or GID 2000 (media).

### The Solution

We configure custom UID/GID mapping in the container configuration to allow specific UIDs/GIDs to pass through unchanged. The Ansible role **automatically detects** the host's video and render group GIDs and generates the appropriate mapping.

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
- The `plex` user is added to the `media` group (GID 2000)
- The `plex` user is added to the `video` and `render` groups (for GPU access)
- This allows Plex to read media files owned by `eric:media` on the host
- GPU access enables hardware-accelerated transcoding
- Transcoding directory is writable by the plex user

## Accessing Plex

### Internal Access

- **URL**: https://plex.esweiss.com
- **Direct**: http://192.168.0.152:32400/web

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

## Backup Restoration

If restoring from a Windows Plex backup:

### 1. Stop Plex Service

```bash
ssh eric@192.168.0.152
sudo systemctl stop plexmediaserver
```

### 2. Restore Backup

```bash
# Copy backup to container (from laptop)
scp -r "Plex Media Server" eric@192.168.0.152:/tmp/

# On container, restore to config directory
sudo rsync -av /tmp/Plex\ Media\ Server/ /config/Library/Application\ Support/Plex\ Media\ Server/
sudo chown -R plex:plex /config/
```

### 3. Update Library Paths

After restoration, library paths need updating from Windows paths to Linux paths. This is done via Plex web interface:
1. Go to Settings > Manage > Libraries
2. Edit each library
3. Update paths from Windows (e.g., `Z:\media\movies`) to Linux (`/media/movies`)

### 4. Start Plex Service

```bash
sudo systemctl start plexmediaserver
```

## Maintenance

### Service Management

```bash
# Check status
ssh eric@192.168.0.152 "sudo systemctl status plexmediaserver"

# Restart Plex
ssh eric@192.168.0.152 "sudo systemctl restart plexmediaserver"

# View logs
ssh eric@192.168.0.152 "sudo journalctl -u plexmediaserver -f"
```

### Updates

Plex updates are managed via the official APT repository:

```bash
# Update Plex to latest
ssh eric@192.168.0.152 "sudo apt update && sudo apt upgrade plexmediaserver -y"

# Or via Ansible
task plex:deploy
```

### Cleanup Transcoding Cache

```bash
# Clear transcoding cache
ssh eric@192.168.0.152 "sudo rm -rf /transcode/*"
```

## Troubleshooting

### Plex Cannot Access Media

**Symptom**: Libraries show "There was a problem adding this folder" or media files not appearing.

**Check**:
```bash
# Verify bind mounts are active
ssh eric@192.168.0.152 "df -h | grep -E '/config|/transcode|/media'"

# Check permissions inside container
ssh eric@192.168.0.152 "ls -la /media"

# Verify plex user is in media group
ssh eric@192.168.0.152 "id plex"
```

**Fix**: If mounts are missing, the container may need restart from Proxmox:
```bash
ssh eric@192.168.0.102 "pct stop 152 && pct start 152"
```

### Transcoding Errors

**Symptom**: Playback fails with transcoding errors.

**Check**:
```bash
# Verify transcode directory is writable
ssh eric@192.168.0.152 "sudo -u plex touch /transcode/test && rm /transcode/test"

# Check disk space
ssh eric@192.168.0.152 "df -h /transcode"

# Check Plex logs
ssh eric@192.168.0.152 "tail -100 '/config/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log'"
```

### Container Won't Start

**Symptom**: Container fails to start after bind mount changes.

**Check**:
```bash
# On pve-nas-01, check container config
ssh eric@192.168.0.102 "cat /etc/pve/lxc/152.conf"

# Check if host directories exist
ssh eric@192.168.0.102 "ls -la /mnt/ssd/appdata/plex /mnt/nvme/fast/plex-transcode /mnt/media"

# Start with verbose output
ssh eric@192.168.0.102 "pct start 152 --debug"
```

### UID Mapping Issues

**Symptom**: Permission denied when accessing files despite correct ownership.

**Check**:
```bash
# Verify UID mapping in container config
ssh eric@192.168.0.102 "grep lxc.idmap /etc/pve/lxc/152.conf"

# Check actual UIDs inside container
ssh eric@192.168.0.152 "stat /media"

# Compare with host
ssh eric@192.168.0.102 "stat /mnt/media"
```

**Fix**: If UID mapping is missing or incorrect:
1. Stop container: `pct stop 152`
2. Edit config: `nano /etc/pve/lxc/152.conf`
3. Add/fix lxc.idmap lines (see [UID/GID Mapping](#uidgid-mapping) section)
4. Start container: `pct start 152`

### GPU Not Accessible

**Symptom**: Hardware transcoding not working, GPU not visible in container.

**Check**:
```bash
# Verify GPU exists on host
ssh eric@192.168.0.102 "ls -la /dev/dri/"

# Check GPU passthrough config
ssh eric@192.168.0.102 "grep -E 'cgroup2.devices|mount.entry.*dri' /etc/pve/lxc/152.conf"

# Verify GPU visible in container
ssh eric@192.168.0.152 "ls -la /dev/dri/"

# Check plex user group membership
ssh eric@192.168.0.152 "groups plex"

# Test VA-API
ssh eric@192.168.0.152 "vainfo"
```

**Fix**: If GPU is missing in container:
1. Stop container: `ssh eric@192.168.0.102 "pct stop 152"`
2. Add GPU passthrough to config:
   ```bash
   ssh eric@192.168.0.102
   cat >> /etc/pve/lxc/152.conf <<EOF
   lxc.cgroup2.devices.allow: c 226:* rwm
   lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir
   EOF
   ```
3. Start container: `pct start 152`
4. Verify plex user groups: `ssh eric@192.168.0.152 "sudo usermod -aG video,render plex"`
5. Restart Plex: `ssh eric@192.168.0.152 "sudo systemctl restart plexmediaserver"`

### Hardware Transcoding Not Working

**Symptom**: GPU is visible but Plex isn't using it for transcoding.

**Check**:
```bash
# Verify VA-API driver loads correctly
ssh eric@192.168.0.152 "vainfo"

# Check Plex has access to GPU
ssh eric@192.168.0.152 "sudo -u plex ls -la /dev/dri/"

# Monitor GPU usage during transcoding
ssh eric@192.168.0.152 "sudo intel_gpu_top"
```

**Fix**:
1. Ensure hardware transcoding is enabled in Plex (Settings > Transcoder)
2. Try toggling the setting off and back on
3. Restart Plex service
4. Check Plex logs for GPU-related errors:
   ```bash
   ssh eric@192.168.0.152 "tail -100 '/config/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log' | grep -i 'hardware\|vaapi\|qsv'"
   ```

## References

- [Plex Media Server Documentation](https://support.plex.tv/articles/)
- [Proxmox LXC Unprivileged Containers](https://pve.proxmox.com/wiki/Unprivileged_LXC_containers)
- [LXC UID/GID Mapping](https://linuxcontainers.org/lxc/manpages/man5/lxc.container.conf.5.html)
- [docs/06-zfs.md](06-zfs.md) - ZFS storage configuration
- [docs/07-fileservices.md](07-fileservices.md) - NFS and Samba setup
- [docs/18-bootstrap-new-systems.md](18-bootstrap-new-systems.md) - LXC bootstrap process
