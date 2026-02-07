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
- Systemd service override (Restart=always)

### Traefik Ingress
- IngressRoute deployment for external access
- HTTPS with Let's Encrypt certificates
- Domain: plex.esweiss.com

## Configuration

```yaml
# Version (from group_vars/all.yml)
plex_version: "1.42.2.10156-f737b826c"  # Or "latest" for auto-update

# LXC configuration (in hosts.yml)
plex:
  vmid: 152
  proxmox_host: pve-nas-01
  lxc_bind_mounts:
    - "/tank/media/unified,mp=/mnt/media,ro=1"
  lxc_gpu_passthrough: true
```

## Deployment

```bash
# Full Plex deployment (LXC + Plex + ingress)
task deploy:plex

# Plex installation only (assumes LXC exists)
ansible-playbook ansible/playbooks/plex.yml

# Dry-run
task deploy:plex-check
```

## Architecture

```
Plex LXC (192.168.0.152)
├─ Plex Media Server
├─ Bind mount: /mnt/media (from pve-nas-01:/tank/media/unified)
├─ GPU: /dev/dri (Intel/AMD transcoding)
└─ Traefik Ingress
   └─ plex.esweiss.com → 192.168.0.152:32400
```

## Task Flow

```
1. Check if Plex GPG key exists
2. Download Plex GPG key (if needed)
3. Add Plex apt repository
4. Install Plex Media Server (pinned version or latest)
5. Deploy systemd override (Restart=always)
6. Reload systemd daemon
7. Ensure Plex service is enabled and running
8. Wait for Plex to be ready (port 32400)
```

## Files

- `tasks/main.yml` - Main orchestration
- `tasks/install.yml` - Plex installation
- `templates/plex-override.conf.j2` - Systemd override
- `defaults/main.yml` - Default variables

## Dependencies

- `proxmox_lxc` role (creates container with GPU and bind mounts)
- `base` role (networking, packages)
- NAS storage (pve-nas-01:/tank/media/unified)

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

Media accessed via read-only bind mount:

```
pve-nas-01:/tank/media/unified
  └─ Bind mounted to → plex:/mnt/media (read-only)
     └─ Plex libraries point to /mnt/media/*
```

Directory structure:
```
/mnt/media/
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

# Check media mount
ls /mnt/media
df -h /mnt/media

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
