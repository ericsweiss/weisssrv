# Download Clients and Media Stack Deployment

This guide covers deploying the complete media management stack including VPN-protected download clients and the *arr suite of applications.

## Overview

### Components

| Component | Purpose | Port | URL | VPN |
|-----------|---------|------|-----|-----|
| **NZBGet** | Usenet download client | 6789 | nzbget.esweiss.com | Optional |
| **qBittorrent** | BitTorrent download client | 8080 | qbittorrent.esweiss.com | Optional |
| **Prowlarr** | Indexer manager | 9696 | prowlarr.esweiss.com | No |
| **Sonarr** | TV show management | 8989 | tv.esweiss.com | No |
| **Radarr** | Movie management | 7878 | movies.esweiss.com | No |
| **Lidarr** | Music management | 8686 | music.esweiss.com | No |
| **Pulsarr** | Plex Watchlist automation | 3003 | pulsarr.esweiss.com | No |

### Architecture

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

### VPN Protection (Sidecar Pattern)

Each download client (NZBGet, qBittorrent) runs in its own pod with an **optional Gluetun VPN sidecar**. When VPN is enabled:

1. **Killswitch**: Both containers share the network namespace - if Gluetun loses VPN, the app loses all network
2. **No bypass**: Apps physically cannot bypass VPN because they don't have their own network interface
3. **Flexible**: Each app can independently run with or without VPN

When VPN is disabled:
1. **Gluetun sleeps**: The sidecar runs `sleep infinity` (minimal resources)
2. **Direct networking**: The app uses the pod's network directly
3. **No overhead**: No VPN tunnel, no encryption overhead

The *arr apps (Sonarr, Radarr, etc.) do **NOT** use VPN because:
- They only query APIs (indexers, TheMovieDB, etc.)
- Actual downloads go through VPN-protected download clients
- VPN would add unnecessary latency and complexity

### VPN Health Monitoring

The qBittorrent pod carries a `gluetun-exporter` sidecar
(`ghcr.io/thecfu/gluetun-exporter`, pinned by `@sha256` digest) that polls the
Gluetun control server on `127.0.0.1:8001` and exposes a Prometheus
`gluetun_vpn_status` gauge on `:8002` (`1` = running, `0` = stopped/unreachable,
`-1` = error, `-2` = unknown). The killswitch means only an in-pod sidecar can reach the control
API, so the exporter shares the pod network namespace rather than running in the
`observability` namespace like the other exporters.

- **Scrape**: a `PodMonitor` (`gluetun-qbittorrent`) in the `downloads`
  namespace targets the `vpn-metrics` port; the kube-prometheus-stack discovers
  PodMonitors cluster-wide. Because `downloads` is default-deny ingress, the
  `allow-observability-vpn-scrape` NetworkPolicy opens TCP 8002 from the
  `observability` namespace. This is inbound metrics only — it does NOT relax
  the Gluetun egress killswitch.
- **Alerts** (in `kube-prometheus-stack` `release.yaml`): `VPNDown` fires when
  `gluetun_vpn_status != 1` for 15m; `VPNExporterDown` fires when the series is
  absent for 15m (pod/exporter gone). The 15m window exceeds a Recreate rollout
  plus OpenVPN reconnect, so normal restarts do not page.
- **Why qBittorrent only**: the exporter's gauge defaults to `0` when the
  control server is unreachable, and NZBGet ships VPN-off by default (Gluetun
  runs `sleep infinity`, control server not listening). Adding the sidecar there
  would report `gluetun_vpn_status=0` forever and false-fire `VPNDown`. If
  NZBGet's VPN is ever enabled, replicate the sidecar + PodMonitor on it.
- **Toggling qBittorrent's VPN off**: the exporter and PodMonitor are coupled to
  `vpn_enabled` for the same reason — disabling the VPN stops Gluetun's control
  server, so `gluetun_vpn_status` reads `0` forever and `VPNDown` pages falsely.
  Remove the `gluetun-exporter` sidecar **and** the `gluetun-qbittorrent`
  PodMonitor from `qbittorrent.yaml` in the same edit that sets
  `vpn_enabled: "false"` (re-add both when re-enabling). See the download-clients
  README for the step-by-step.

## Prerequisites

### 1. 1Password Setup

Create VPN credential items in your Homelab vault:

**For PrivadoVPN:**
```
# Item: "PrivadoVPN Credentials"
# Vault: Homelab
# Fields:
#   - openvpn-user: Your PrivadoVPN username
#   - openvpn-password: Your PrivadoVPN password
```

**For VPN Unlimited (KeepSolid):**
```
# Item: "VPN Unlimited Credentials"
# Vault: Homelab
# Fields:
#   - openvpn-user: Your VPN Unlimited OpenVPN username
#   - openvpn-password: Your VPN Unlimited OpenVPN password
```

### 2. NFS Storage Preparation

The NFS exports should already be configured. Verify the appdata directories exist:

```bash
ssh pve-nas-01 "ls -la /mnt/ssd/appdata/"
```

Create app directories if needed:

```bash
ssh pve-nas-01 "sudo mkdir -p /mnt/ssd/appdata/{nzbget,qbittorrent,prowlarr,sonarr,radarr,lidarr,pulsarr}"
ssh pve-nas-01 "sudo chown -R 1000:2000 /mnt/ssd/appdata/"
ssh pve-nas-01 "sudo chmod -R 2775 /mnt/ssd/appdata/"
```

Create media directories:

```bash
ssh pve-nas-01 "sudo mkdir -p /mnt/nvme/media/downloads/{nzbget,qbittorrent}/{intermediate,complete}"
ssh pve-nas-01 "sudo mkdir -p /mnt/nvme/media/library/{TV_Shows,Movies,Music,Books,Audiobooks}"
ssh pve-nas-01 "sudo chown -R 1000:2000 /mnt/nvme/media/"
ssh pve-nas-01 "sudo chmod -R 2775 /mnt/nvme/media/"
```

### 3. DNS Configuration

Add DNS rewrites in AdGuard Home for all services pointing to the internal Traefik VIP (192.168.0.101):

```yaml
# Add to dns.yml or configure via AdGuard Home UI
- domain: nzbget.esweiss.com
  answer: 192.168.0.101
- domain: qbittorrent.esweiss.com
  answer: 192.168.0.101
- domain: prowlarr.esweiss.com
  answer: 192.168.0.101
- domain: tv.esweiss.com           # Sonarr
  answer: 192.168.0.101
- domain: movies.esweiss.com       # Radarr
  answer: 192.168.0.101
- domain: music.esweiss.com        # Lidarr
  answer: 192.168.0.101
- domain: pulsarr.esweiss.com
  answer: 192.168.0.101
```

## Deployment

The downloads stack is Flux-managed. All files live under
`kubernetes/apps/download-clients/` and are reconciled automatically on commit + push.

### Layout

```
kubernetes/apps/download-clients/
├── namespace.yaml              # downloads namespace (pod-security: privileged for Gluetun CAP_NET_ADMIN)
├── externalsecret.yaml         # VPN credentials from 1Password (via ESO)
├── storage.yaml                # PVCs + PVs for appdata and media NFS mounts
├── certificate.yaml            # Wildcard cert in the downloads namespace
├── nzbget.yaml                 # Deployment + per-app VPN ConfigMap (nzbget-vpn-config) + Gluetun sidecar inline
├── qbittorrent.yaml            # As nzbget + gluetun-exporter sidecar + PodMonitor (VPN-always-on)
├── prowlarr.yaml
├── sonarr.yaml
├── radarr.yaml
├── lidarr.yaml
├── pulsarr.yaml
├── ingress-routes.yaml         # Traefik IngressRoutes for all apps
├── ingress-routes-ha-bypass.yaml  # High-priority routes bypassing SSO for Home Assistant's IP
├── networkpolicy.yaml          # default-deny + per-app allowlist (incl. observability->qbittorrent:8002 VPN scrape)
├── vpa.yaml                    # VerticalPodAutoscalers (per-container sizing, incl. gluetun-exporter)
└── kustomization.yaml
```

### Deploying Changes (ongoing work)

1. Edit the appropriate YAML under `kubernetes/apps/download-clients/`.
2. Commit and push.
3. Flux polls every ~1 minute (a planned webhook will reduce this to seconds).

```bash
# Example: bump Sonarr image tag
vim ansible/inventories/prod/group_vars/all.yml  # bump sonarr_version
task flux:sync-versions                            # regenerate versions-configmap
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump sonarr"
git push

# Optional: trigger Flux immediately
task flux:reconcile

# Watch the rollout
task downloads:status
kubectl rollout status deploy/sonarr -n downloads
```

For fast local iteration without committing:

```bash
# Renders the Kustomization and applies it (Flux will revert within ~1 min)
task flux:dev-apply -- kubernetes/apps/download-clients
```

### Initial Install

The downloads stack is included in the top-level `apps` Kustomization, so first
reconcile after `task flux:bootstrap` creates everything. No manual deploy step
is needed.

### VPN Credentials (ExternalSecret)

VPN credentials are NOT created with `kubectl create secret`. They are managed by
`kubernetes/apps/download-clients/externalsecret.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: vpn-credentials
  namespace: downloads
spec:
  refreshInterval: 24h
  secretStoreRef:
    name: onepassword-homelab       # ClusterSecretStore (1P Connect provider)
    kind: ClusterSecretStore
  target:
    name: vpn-credentials
    creationPolicy: Owner
  data:
    - secretKey: openvpn-user
      remoteRef:
        key: PrivadoVPN Credentials
        property: openvpn-user
    - secretKey: openvpn-password
      remoteRef:
        key: PrivadoVPN Credentials
        property: openvpn-password
```

The 1P Connect provider uses `key: <item-title>` and `property: <field-name>`.
See `docs/29-flux-operations.md` for the format rules.

To rotate VPN credentials: update the password in 1Password, then either wait
24h for the refresh interval or:

```bash
task flux:rotate-secret -- downloads
# (triggers ExternalSecret refresh + restarts the Gluetun-bearing pods)
```

### Verify Deployment

```bash
# Check all pods
task downloads:status

# Check VPN connection for both download clients
task downloads:vpn-status

# Verify public IP is VPN IP (not your home IP)
kubectl exec -n downloads deployment/nzbget -c gluetun -- wget -qO- https://ipinfo.io
kubectl exec -n downloads deployment/qbittorrent -c gluetun -- wget -qO- https://ipinfo.io
```

## VPN Management

### Per-App VPN Control

VPN enablement and provider selection are per-app ConfigMaps inlined in the
relevant Deployment YAMLs: `nzbget-vpn-config` in `nzbget.yaml` and
`qbittorrent-vpn-config` in `qbittorrent.yaml`. There is no shared
`gluetun.yaml` — the sidecar is injected per-Deployment and its config is
loaded from the matching ConfigMap.

To change a VPN setting:

1. Edit the `<app>-vpn-config` ConfigMap section inside `nzbget.yaml` or
   `qbittorrent.yaml` (set `vpn_enabled: "true"` / `"false"` and
   `vpn_provider: "privado"` / `"vpn unlimited"`).
2. Commit and push.
3. Flux reconciles; run `task flux:rotate-secret -- downloads` to roll the
   pod so Gluetun picks up the ConfigMap change (mounted ConfigMaps don't
   trigger pod restart automatically).

### Check VPN Status

```bash
# Check VPN status for all download clients
task downloads:vpn-status
```

This shows:
- VPN enabled/disabled status per app
- Current VPN provider per app
- Gluetun logs (if VPN enabled)
- Public IP (to verify VPN is working)

### Rotate VPN Credentials

1. Update the password in 1Password (`PrivadoVPN Credentials/openvpn-password`).
2. Trigger refresh: `task flux:rotate-secret -- downloads`

(Or switch provider: edit the `vpn_provider` key in the per-app
ConfigMap inside `nzbget.yaml` or `qbittorrent.yaml`, commit, push.
If switching to VPN Unlimited, also update `externalsecret.yaml` to
reference the `VPN Unlimited Credentials` 1P item title.)

## Storage Layout

### Container Mount Points

All apps mount `/media` which maps to `/export/media` on the NAS (mergerfs view of `/mnt/media`):

```
/media/                      # <- NFS mount of /export/media (mergerfs)
|-- downloads/
|   |-- nzbget/
|   |   |-- intermediate/    # In-progress usenet downloads
|   |   +-- complete/        # Completed usenet downloads
|   +-- qbittorrent/
|       |-- intermediate/    # In-progress torrent downloads
|       +-- complete/        # Completed torrent downloads
+-- library/                 # Organized media (MergerFS: nvme hot + tank cold)
    |-- TV_Shows/
    |-- Movies/
    |-- Music/
    |-- Books/
    +-- Audiobooks/
```

App configs are mounted separately at `/config` from `/export/appdata/{app}` (on SSD).

### Hard Linking

Because `/media/downloads` and `/media/library` are all under the same NFS mount (`/export/media`), *arr apps can use **hard links** instead of copying files. This is:

- **Instant**: No data copying needed
- **Space efficient**: File exists once but appears in two places
- **Atomic**: Import completes instantly

Configure *arr apps to use:
- **Download path**: `/media/downloads/nzbget/complete` or `/media/downloads/qbittorrent/complete`
- **Media path**: `/media/library/{TV_Shows,Movies,Music,Books,Audiobooks}`

## Application Configuration

### NZBGet

1. Access: https://nzbget.esweiss.com
2. Default credentials: `nzbget` / `tegbzn6789`
3. Configure:
   - **MainDir**: `/media/downloads/nzbget`
   - **InterDir**: `/media/downloads/nzbget/intermediate`
   - **DestDir**: `/media/downloads/nzbget/complete`
   - **ScriptDir**: `/media/downloads/nzbget/scripts` (optional)

### qBittorrent

1. Access: https://qbittorrent.esweiss.com
2. Default credentials: `admin` / `adminadmin` (change immediately!)
3. Configure:
   - **Default Save Path**: `/media/downloads/qbittorrent/complete`
   - **Temp folder**: `/media/downloads/qbittorrent/intermediate`
   - **Enable "Keep incomplete torrents in"**: Yes

### Prowlarr

1. Access: https://prowlarr.esweiss.com
2. Initial setup wizard will guide you
3. Add indexers (Usenet and torrent)
4. Configure download clients:
   - NZBGet: `nzbget.downloads.svc.cluster.local:6789`
   - qBittorrent: `qbittorrent.downloads.svc.cluster.local:8080`
5. Add applications (Sonarr, Radarr, etc.) - Prowlarr will sync indexers

### Sonarr

1. Access: https://tv.esweiss.com
2. Configure:
   - **Root Folder**: `/media/library/TV_Shows`
   - **Download Clients**: Add via Prowlarr sync or manually
   - **Remote Path Mappings**: Usually not needed (same mount)

### Radarr

1. Access: https://movies.esweiss.com
2. Configure:
   - **Root Folder**: `/media/library/Movies`
   - **Download Clients**: Add via Prowlarr sync or manually

### Lidarr

1. Access: https://music.esweiss.com
2. Configure:
   - **Root Folder**: `/media/library/Music`
   - **Download Clients**: Add via Prowlarr sync or manually

### Pulsarr

> **AVX Requirement**: Pulsarr uses the Bun JavaScript runtime (v0.10.0+) which requires AVX CPU instructions. It is pinned to NAS nodes (`esweiss.com/nas: "true"`) because only pve-nas-01 (12th Gen Intel i7-12700K) has AVX support. The pve-opt-03 node (Core 2 Quad Q9650) does NOT support AVX and will cause Pulsarr to crash with an "Illegal instruction" error. Do not reschedule Pulsarr to non-NAS nodes without verifying AVX support on the target hardware.

1. Access: https://pulsarr.esweiss.com
2. Configure:
   - **Plex Token**: Get from Plex account settings
   - **Sonarr URL**: `http://sonarr.downloads.svc.cluster.local:8989`
   - **Radarr URL**: `http://radarr.downloads.svc.cluster.local:7878`
   - **API Keys**: Get from each app's settings

## Authentik SSO Integration

All apps are protected by Authentik SSO via the `authentik-auth` ForwardAuth middleware. This provides **gateway-level authentication** - users must authenticate via Authentik before reaching any app.

### Understanding the Authentication Flow

**Current Implementation (Middleware-Only)**:
1. User navigates to `https://tv.esweiss.com`
2. Traefik's `authentik-auth` middleware redirects to Authentik
3. User logs in to Authentik
4. Authentik redirects back to the app
5. User sees the app's native login page (if enabled)

This means users may need to authenticate twice: once to Authentik, once to the app itself.

### Eliminating Double Login

To avoid the double-login experience, configure each *arr app to use **External Authentication**:

1. **Sonarr/Radarr/Lidarr/Prowlarr**:
   - Settings > General > Security
   - Set **Authentication** to `External`
   - This tells the app to trust the reverse proxy's authentication
   - The app will skip its login page entirely

2. **qBittorrent**:
   - Tools > Options > Web UI
   - Check **Bypass authentication for clients in whitelisted IP subnets**
   - Add `10.0.0.0/8` (pod network) to whitelist
   - Or use **Bypass authentication for clients on localhost**

3. **NZBGet**:
   - Settings > Security
   - Set **ControlUsername** and **ControlPassword** to empty
   - Or configure **AuthorizedIP** to include pod network

**Important**: When using External Authentication, the apps will trust anyone who reaches them. This is safe because:
- Traefik's `lan-tailscale-only` middleware blocks external IPs
- Authentik middleware ensures only authenticated users pass through
- All traffic goes through these middleware layers

### Native OIDC/SAML Support (Not Available)

The *arr apps do **NOT** support native OIDC or SAML authentication. They lack:
- OAuth2/OIDC client capability
- SAML service provider capability
- Header-based user identification (like `Remote-User`)

This is a known limitation. The Sonarr developers have stated they have no current plans to add native SSO support. The "External" authentication mode combined with ForwardAuth is the recommended workaround.

See: [Sonarr Issue #2477](https://github.com/Sonarr/Sonarr/issues/2477) for ongoing discussion.

### Creating Authentik Applications (Optional)

For audit logging and per-app access control, you can create individual Authentik Applications:

1. Go to Authentik Admin > Applications > Create
2. Name: `{App Name}` (e.g., "Sonarr")
3. Slug: `{app-name}` (e.g., "sonarr")
4. Provider: Create new "Proxy Provider"
   - Name: `{app-name}-proxy`
   - Authorization flow: default-provider-authorization-implicit-consent
   - External Host: `https://tv.esweiss.com`
   - Mode: Forward auth (single application)

This enables per-application audit logs and the ability to restrict access to specific users/groups.

## Config Migration from Windows

### Sonarr/Radarr Migration

1. **Stop the Windows service**

2. **Locate config on Windows**:
   ```
   %APPDATA%\Sonarr\  (or Radarr)
   ```

3. **Copy database and config**:
   ```bash
   # From Windows (using WSL or SCP)
   scp -r /mnt/c/Users/Eric/AppData/Roaming/Sonarr/* pve-nas-01:/mnt/ssd/appdata/sonarr/
   ```

4. **Fix permissions**:
   ```bash
   ssh pve-nas-01 "sudo chown -R 1000:2000 /mnt/ssd/appdata/sonarr/"
   ```

5. **Update paths in config.xml**:
   - Open `/mnt/ssd/appdata/sonarr/config.xml`
   - Update any Windows paths to Linux paths
   - Common changes:
     - `C:\Downloads\` -> `/media/downloads/`
     - `D:\Media\TV Shows\` -> `/media/library/TV_Shows/`

6. **Update database paths** (if needed):
   ```bash
   # Connect to the app and use System > Tasks > Update All Series Paths
   # Or use the API/sqlite to bulk update
   ```

### Deluge to qBittorrent Migration

Deluge and qBittorrent use different config formats, but you can:

1. **Export torrents from Deluge**:
   - Copy `.torrent` files from Deluge's `state` folder
   - Or use Deluge's "Export Torrent" feature

2. **Import to qBittorrent**:
   - Add torrents manually to qBittorrent
   - Point to existing downloaded files
   - qBittorrent will verify and continue seeding

### Important Migration Notes

- **Quality Profiles**: Will need to be recreated or carefully migrated
- **Indexers**: Re-add via Prowlarr (simpler than migrating)
- **Download Clients**: Update URLs to Kubernetes service names
- **Root Folders**: Must be updated to new Linux paths
- **Custom Scripts**: Review and adapt for Linux environment

## Maintenance

### View Logs

```bash
# Specific app
task downloads:logs APP=sonarr

# Download client VPN logs
task downloads:logs APP=nzbget CONTAINER=gluetun
```

### Shell Access

```bash
# Access app shell
task downloads:shell APP=sonarr

# Access VPN container
task downloads:shell APP=nzbget CONTAINER=gluetun
```

### Restart Apps

```bash
# Restart everything
task downloads:restart

# Restart specific app
kubectl rollout restart deployment/sonarr -n downloads
```

### Update Apps

Images are pinned in `ansible/inventories/prod/group_vars/all.yml` and flow through
the `cluster-versions` ConfigMap. To upgrade:

```bash
# Check for new versions
task maintenance:check-versions

# Bump one (or many) services
task maintenance:update-version SERVICE=sonarr
# or: task maintenance:update-all-versions

# Regenerate the ConfigMap and push
task flux:sync-versions
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump sonarr" && git push

# Flux rolls the Deployments within ~1 minute
task flux:status
```

### Backup

App configs are stored on NFS (`/mnt/ssd/appdata`). Include in your regular NAS backup strategy.

Critical files to backup:
- `*.db` - Application databases
- `config.xml` - App configuration
- Custom scripts and profiles

## Troubleshooting

### VPN Not Connecting

```bash
# Check Gluetun logs for NZBGet
kubectl logs -n downloads -l app.kubernetes.io/name=nzbget -c gluetun

# Check Gluetun logs for qBittorrent
kubectl logs -n downloads -l app.kubernetes.io/name=qbittorrent -c gluetun

# Common issues:
# - Wrong credentials in secret
# - VPN provider service down
# - /dev/net/tun not available on node
```

### App Has No Network When VPN Enabled

This is the killswitch working correctly. If Gluetun can't establish VPN:
1. Check VPN provider status
2. Check credentials in 1Password (rotate if needed, then `task flux:rotate-secret -- downloads`)
3. Temporarily disable VPN by setting `vpn_enabled: "false"` in the app's
   per-app ConfigMap (inside `nzbget.yaml` or `qbittorrent.yaml`) and
   pushing. Then `task flux:rotate-secret -- downloads` to roll the pod.

### Apps Can't Access Storage

```bash
# Check NFS mounts
kubectl exec -n downloads deployment/sonarr -- df -h

# Check permissions
kubectl exec -n downloads deployment/sonarr -- ls -la /media/

# Verify NFS export on NAS
ssh pve-nas-01 "exportfs -v"
```

### Download Clients Not Reachable

```bash
# From *arr pod, test connectivity
kubectl exec -n downloads deployment/sonarr -- wget -qO- http://nzbget.downloads.svc.cluster.local:6789

# Check service endpoints
kubectl get endpoints -n downloads
```

### Hard Links Not Working

```bash
# Must be same filesystem - verify:
kubectl exec -n downloads deployment/sonarr -- stat -f /media/downloads/
kubectl exec -n downloads deployment/sonarr -- stat -f /media/library/
# Device numbers must match for hard links to work
```

## Related Documentation

- [K3s Deployment Guide](./19-k3s-deployment.md)
- [Flux Operations](./29-flux-operations.md)
- [Storage Configuration](./07-fileservices.md)
- [DNS Configuration](./08-dns.md)
- Manifests: `kubernetes/apps/download-clients/`
