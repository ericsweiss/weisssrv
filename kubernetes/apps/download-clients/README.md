# Downloads Stack

VPN-protected download clients and media management applications with flexible per-app VPN control.

## Architecture

```
                             Internet
                                 |
                    +------------+------------+
                    |                         |
               VPN Tunnel              VPN Tunnel
               (optional)              (optional)
                    |                         |
+-------------------+---+   +-------------------+---+
|     NZBGet Pod        |   |   qBittorrent Pod     |
|  +---------+-------+  |   |  +---------+-------+  |
|  | Gluetun | NZBGet|  |   |  | Gluetun | qBit  |  |
|  |  (VPN)  | :6789 |  |   |  |  (VPN)  | :8080 |  |
|  +---------+-------+  |   |  +---------+-------+  |
|  Shared Network NS    |   |  Shared Network NS    |
|  (killswitch enforced)|   |  (killswitch enforced)|
+-----------------------+   +-----------------------+
        ^                           ^
        |                           |
  +-----+---------------------------+-----+
  |                                       |
  |     *arr Apps (no VPN needed)        |
  | +----------+ +--------+ +---------+   |
  | | Prowlarr | | Sonarr | | Radarr  |   |
  | | Lidarr   | | Pulsarr|           |   |
  | +----------+ +--------+ +---------+   |
  +---------------------------------------+
                    |
                    v
  +------------------------------------------------+
  |           NFS Storage (pve-nas-01)             |
  | /media/downloads <--hard link--> /media/library|
  +------------------------------------------------+
```

## Key Features

- **Independent VPN Control**: NZBGet and qBittorrent can each run with or without VPN
- **Multiple VPN Providers**: Support for PrivadoVPN, VPN Unlimited, or no VPN
- **Killswitch Enforcement**: When VPN is enabled, apps share network namespace with Gluetun - if VPN drops, app loses network
- **Easy Switching**: Change VPN status per-app with a single command
- **No Redeployment**: Toggle VPN without recreating deployments (just restarts pod)

## Components

| Service | Purpose | URL | VPN |
|---------|---------|-----|-----|
| NZBGet | Usenet downloads | nzbget.esweiss.com | Optional |
| qBittorrent | BitTorrent downloads | qbittorrent.esweiss.com | Optional |
| Prowlarr | Indexer manager | prowlarr.esweiss.com | No |
| Sonarr | TV shows | tv.esweiss.com | No |
| Radarr | Movies | movies.esweiss.com | No |
| Lidarr | Music | music.esweiss.com | No |
| Pulsarr | Plex Watchlist automation | pulsarr.esweiss.com | No |

## Deployment

```bash
# Deploy entire stack
# Default VPN state: NZBGet=disabled, qBittorrent=enabled
task downloads:deploy

# Check status
task downloads:status

# Check VPN connection for apps with VPN enabled
task downloads:vpn-status
```

## VPN Management

### Per-App VPN Control

Each download client can be configured independently:

```bash
# Disable VPN for NZBGet (use direct internet)
task downloads:vpn APP=nzbget ENABLED=false

# Enable VPN for NZBGet with PrivadoVPN
task downloads:vpn APP=nzbget ENABLED=true PROVIDER=privado

# Enable VPN for qBittorrent with VPN Unlimited
task downloads:vpn APP=qbittorrent ENABLED=true PROVIDER=vpn-unlimited

# Disable VPN for qBittorrent
task downloads:vpn APP=qbittorrent ENABLED=false
```

### Check VPN Status

```bash
# Show VPN status for all download clients
task downloads:vpn-status
```

This shows:
- VPN enabled/disabled status
- Current VPN provider
- Gluetun logs (if VPN enabled)
- Public IP (to verify VPN is working)

### Update VPN Credentials

```bash
# Update to PrivadoVPN credentials
task downloads:vpn-credentials PROVIDER=privado

# Update to VPN Unlimited credentials
task downloads:vpn-credentials PROVIDER=vpn-unlimited
```

## How the Sidecar Pattern Works

When VPN is enabled for an app:

1. **Shared Network Namespace**: The app container and Gluetun sidecar share the same network namespace
2. **Gluetun Owns the Network**: All traffic goes through Gluetun's VPN tunnel
3. **Killswitch by Design**: If Gluetun crashes or loses VPN connection, the app container has no network access
4. **No Bypass Possible**: The app cannot circumvent the VPN because it doesn't have its own network interface

When VPN is disabled:

1. **Gluetun Sleeps**: The sidecar container runs `sleep infinity` (uses minimal resources)
2. **Direct Networking**: The app uses the pod's network directly
3. **No VPN Overhead**: No VPN tunnel, no encryption overhead

## Configuration Files

Each download client has its own VPN configuration via ConfigMap:

```yaml
# nzbget-vpn-config / qbittorrent-vpn-config
data:
  vpn_enabled: "true"           # "true" or "false"
  vpn_provider: "privado"       # "privado" or "vpn unlimited" (Gluetun format)
  server_countries: "Netherlands"
```

**Note**: The `task downloads:vpn` command accepts `PROVIDER=vpn-unlimited` (with hyphen) for CLI convenience and automatically normalizes it to `"vpn unlimited"` (with space) in the ConfigMap, which is the format Gluetun expects.

## Storage Layout

All apps mount `/media` which maps to `/export/media` on the NAS (mergerfs view):

```
/media/
  downloads/
    nzbget/              # NZBGet downloads
      intermediate/      # In-progress downloads
      complete/          # Completed downloads
    qbittorrent/         # qBittorrent downloads
      intermediate/      # In-progress downloads
      complete/          # Completed downloads
  library/               # Organized media (MergerFS: nvme hot + tank cold)
    TV_Shows/
    Movies/
    Music/
    Books/
    Audiobooks/
```

Hard linking works because downloads and library are on the same NFS mount (both under `/media`).

## Download Paths

- NZBGet: `/media/downloads/nzbget/complete`
- qBittorrent: `/media/downloads/qbittorrent/complete`

## Media Paths

- TV: `/media/library/TV_Shows`
- Movies: `/media/library/Movies`
- Music: `/media/library/Music`

## Internal Service URLs

For *arr app configuration, use Kubernetes service DNS names:

- NZBGet: `nzbget.downloads.svc.cluster.local:6789`
- qBittorrent: `qbittorrent.downloads.svc.cluster.local:8080`
- Sonarr: `sonarr.downloads.svc.cluster.local:8989`
- Radarr: `radarr.downloads.svc.cluster.local:7878`

## DNS Configuration

All services use internal domain (esweiss.com) via AdGuard Home rewrites:

```yaml
- domain: nzbget.esweiss.com
  answer: 192.168.0.101
- domain: qbittorrent.esweiss.com
  answer: 192.168.0.101
- domain: prowlarr.esweiss.com
  answer: 192.168.0.101
- domain: tv.esweiss.com
  answer: 192.168.0.101
- domain: movies.esweiss.com
  answer: 192.168.0.101
- domain: music.esweiss.com
  answer: 192.168.0.101
- domain: pulsarr.esweiss.com
  answer: 192.168.0.101
```

## Troubleshooting

```bash
# View logs for specific app
task downloads:logs APP=nzbget
task downloads:logs APP=nzbget CONTAINER=gluetun
task downloads:logs APP=sonarr

# Shell access
task downloads:shell APP=nzbget
task downloads:shell APP=nzbget CONTAINER=gluetun

# Restart all apps
task downloads:restart

# Delete and redeploy
task downloads:delete
task downloads:deploy
```

### Common Issues

#### VPN Not Connecting

1. Check Gluetun logs: `task downloads:logs APP=nzbget CONTAINER=gluetun`
2. Verify credentials are correct in 1Password
3. Try switching VPN providers: `task downloads:vpn APP=nzbget PROVIDER=vpn-unlimited`

#### App Has No Network When VPN Enabled

This is the killswitch working correctly. If Gluetun can't establish VPN:
1. Check VPN provider status
2. Check credentials
3. Temporarily disable VPN: `task downloads:vpn APP=nzbget ENABLED=false`

#### Need to Use Different VPN Providers for Each App

Both apps share the same `vpn-credentials` secret. If you need different providers:
1. Configure one app: `task downloads:vpn APP=nzbget PROVIDER=privado`
2. Configure other app: `task downloads:vpn APP=qbittorrent PROVIDER=vpn-unlimited`

Note: The credentials secret is shared, so the last `PROVIDER` used will set the credentials for both. If apps need truly different credentials, you would need to modify the deployments to use separate secrets.

## Files

- `namespace.yaml` - Downloads namespace
- `storage.yaml` - PV/PVC definitions for NFS storage
- `vpn-common.yaml` - Shared VPN credentials secret
- `nzbget.yaml` - NZBGet deployment with optional Gluetun sidecar
- `qbittorrent.yaml` - qBittorrent deployment with optional Gluetun sidecar
- `prowlarr.yaml` - Prowlarr deployment
- `sonarr.yaml` - Sonarr deployment
- `radarr.yaml` - Radarr deployment
- `lidarr.yaml` - Lidarr deployment
- `pulsarr.yaml` - Pulsarr deployment
- `ingress-routes.yaml` - Traefik IngressRoutes with SSO
- `kustomization.yaml` - Kustomize configuration
