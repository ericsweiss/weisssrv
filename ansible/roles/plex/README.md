# Plex Role

Installs and configures Plex Media Server in an LXC container with GPU transcoding, bind-mounted media library, and Traefik ingress.

## What This Role Manages

### Installation
- Plex apt repository configuration
- GPG key installation
- Plex Media Server package installation (pinned version)
- Service enablement and start

### Configuration
- GPU transcoding enablement (via proxmox_lxc role)
- Media library access (bind mount from pve-nas-01)
- Systemd service override (Restart=on-failure)
- TLS custom-certificate hook (plex-cert-reload.sh)

### Traefik Ingress
- IngressRoute deployment for external access
- HTTPS with Let's Encrypt certificates
- Domain: plex.esweiss.com

## Configuration

```yaml
# Version (from group_vars/all.yml)
plex_version: "1.42.2.10156-f737b826c"  # Or "latest" for auto-update

# LXC configuration (host_vars/plex.yml)
plex:
  vmid: 152
  proxmox_host: pve-nas-01
  lxc_bind_mounts:
    - host_path: /mnt/ssd/appdata/plex
      container_path: /config
    - host_path: /mnt/nvme/fast/plex-transcode
      container_path: /transcode
    - host_path: /mnt/media
      container_path: /media
  lxc_gpu_passthrough: true
```

## Deployment

```bash
# Full Plex deployment (LXC + Plex + ingress)
task plex:deploy

# Plex installation only (assumes LXC exists)
ansible-playbook ansible/playbooks/plex.yml

# Dry-run
task plex:check
```

## Architecture

```
Plex LXC (192.168.0.152)
├─ Plex Media Server
├─ Bind mounts (from pve-nas-01):
│  ├─ /mnt/ssd/appdata/plex          → /config
│  ├─ /mnt/nvme/fast/plex-transcode  → /transcode
│  └─ /mnt/media (mergerfs)          → /media (read-write for metadata)
├─ GPU: /dev/dri (Intel/AMD transcoding)
└─ Traefik Ingress
   └─ plex.esweiss.com → 192.168.0.152:32400
```

## Task Flow

```
1. Check if Plex GPG key exists
2. Download + verify Plex GPG key (if needed)
3. Add Plex apt repository
4. Install Plex Media Server (pinned version or latest); hold the pinned version
5. Add plex user to media/video/render groups
6. Deploy TLS cert-reload hook (plex-cert-reload.sh + PFX passphrase)
7. Deploy systemd override (Restart=on-failure)
8. Reload systemd daemon
9. Ensure Plex service is enabled and running
10. Wait for Plex to be ready (port 32400)
```

## Files

- `tasks/main.yml` - Main orchestration (includes install/configure/service)
- `tasks/install.yml` - apt repo, GPG key, package install + version hold
- `tasks/configure.yml` - groups, TLS cert-reload hook, bind-mount checks, systemd override
- `tasks/service.yml` - daemon-reload, enable/start, health check
- `handlers/main.yml` - Restart plex / reload systemd handlers
- `templates/plex-override.conf.j2` - Systemd override
- `templates/plex-cert-reload.sh.j2` - PEM -> PKCS#12 cert-reload hook
- `defaults/main.yml` - Default variables

## TLS Certificate

acme_certs (on dns-01) distributes the wildcard cert to `/etc/ssl/plex`.
`plex-cert-reload.sh` (deployed by this role at `/usr/local/sbin`, root:root 0750)
converts the PEM pair into the PKCS#12 (`.pfx`) bundle Plex's "Custom certificate
location" requires, verifies it, swaps it in atomically, and restarts Plex. The
PFX passphrase comes from 1Password (`PLEX_PFX_PASSPHRASE`, item "Plex Custom
Certificate") and must match the value set under Plex Settings -> Network ->
"Custom certificate encryption key".

## Dependencies

- `proxmox_lxc` role (creates container with GPU and bind mounts)
- `base` role (networking, packages)
- NAS storage (pve-nas-01:/mnt/media via mergerfs)

## Initial Setup

After deployment, complete setup in browser:

1. Access https://plex.esweiss.com
2. Sign in with Plex account
3. Claim server
4. Add library pointing to /mnt/media

## Version Management

### Pinned Version

```yaml
plex_version: "1.42.2.10156-f737b826c"
```

Updates via:
```bash
# Update version in group_vars/all.yml
# Run: task maintenance:update-applications
```

### Auto-Update Mode

```yaml
plex_version: "latest"
```

Plex will auto-update via apt.

## GPU Transcoding

Enabled via LXC GPU passthrough:

```yaml
# In hosts.yml
plex:
  lxc_gpu_passthrough: true
```

This passes /dev/dri to the container for hardware transcoding.

Verify:
```bash
# In Plex LXC
ls -la /dev/dri
# Should show renderD128 and card0
```

## Media Library

Media accessed via read-write bind mount (required for Plex metadata/watch status):

```
pve-nas-01:/mnt/media (mergerfs)
  └─ Bind mounted to → plex:/media (read-write)
     └─ Plex libraries point to /media/*
```

Directory structure (inside the LXC):
```
/media/
├─ movies/
├─ tv/
├─ music/
└─ ...
```

## Troubleshooting

```bash
# Check Plex service
systemctl status plexmediaserver

# View logs
tail -f /var/lib/plexmediaserver/Library/Application\ Support/Plex\ Media\ Server/Logs/Plex\ Media\ Server.log

# Check media mount (inside the LXC)
ls /media
df -h /media

# Check GPU
ls -la /dev/dri
# Should show render and card devices

# Test transcoding
# Play a 4K video and check if GPU is used in Plex dashboard
```

## Operational Notes

### Backup

Important directories:
- `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/`
  - Metadata, posters, preferences

Backup via Proxmox LXC snapshots or rsync.

### Updates

```bash
# Check current version
dpkg -l | grep plexmediaserver

# Update (if plex_version="latest")
apt update && apt upgrade plexmediaserver
systemctl restart plexmediaserver
```

### Remote Access

Enabled automatically through plex.esweiss.com.

Plex Relay not needed (direct connection via Traefik).
