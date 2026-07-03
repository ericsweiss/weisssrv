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

This stack is Flux-managed. Everything in this folder is reconciled by the
top-level `apps` Kustomization.

- **Namespace**: `namespace.yaml` (labeled `pod-security.kubernetes.io/enforce: privileged` — required for Gluetun `CAP_NET_ADMIN`)
- **Secret**: `externalsecret.yaml` — ExternalSecret `vpn-credentials` sourcing `openvpn-user` / `openvpn-password` from 1Password via ESO
- **Workloads**: `prowlarr.yaml` and `pulsarr.yaml` are standalone manifests; `nzbget/` and `qbittorrent/` are overlays over the shared Gluetun VPN sidecar component (`_vpn-sidecar/`); `sonarr/`, `radarr/`, and `lidarr/` are overlays over the shared `_arr` component (`_arr/`). Each overlay's `resources.yaml` holds its app-specific resources (incl. the per-app VPN ConfigMap for nzbget/qbittorrent). Image tags use `${<app>_version}` placeholders substituted from the `cluster-versions` ConfigMap at reconcile time.
- **Storage**: `storage/` — per-app NFS PV/PVC overlays over the shared `_nfs-pv/` component (TLS mountOptions defined once), plus `storage/shared.yaml` for the RWX media PV
- **Ingress**: `ingress-routes.yaml` (standard) + `ingress-routes-ha-bypass.yaml` (HA integration API-only routes)
- **Certificate**: `certificate.yaml` (single wildcard cert for `*.esweiss.com`)

Deploy workflow (edit + commit + push):

```bash
vim kubernetes/apps/download-clients/qbittorrent/resources.yaml  # or any file
git add kubernetes/apps/download-clients/
git commit -m "..."
git push

# Force reconciliation instead of waiting for the ~1m poll
task flux:reconcile

# Ops checks (unchanged)
task downloads:status
task downloads:vpn-status
```

Default VPN state: NZBGet sidecar VPN disabled, qBittorrent VPN enabled.
Toggle by editing `vpn_enabled` in the app's VPN ConfigMap (see VPN
Management below) and pushing.

## VPN Management

### Per-App VPN Control

Each download client has a per-app ConfigMap (e.g., `nzbget-vpn-config`,
`qbittorrent-vpn-config`) that Gluetun reads. The Gluetun sidecar itself is the
shared `_vpn-sidecar/` component; the per-app ConfigMap lives in the app
overlay's `resources.yaml`. Edit it to toggle `vpn_enabled` or change
`vpn_provider`:

```bash
vim kubernetes/apps/download-clients/qbittorrent/resources.yaml
# Find the ConfigMap section and edit:
#   vpn_enabled: "true"          # or "false"
#   vpn_provider: "privado"      # or "vpn unlimited" (note space for Gluetun)
#   server_countries: "Netherlands"

git add kubernetes/apps/download-clients/qbittorrent/resources.yaml
git commit -m "Disable VPN on qbittorrent" # or similar
git push
```

The nzbget and qbittorrent Deployments carry the
`reloader.stakater.com/auto: "true"` annotation, so once Flux reconciles the
ConfigMap change, **stakater/Reloader automatically rolls the pod** to pick up
the new mounted VPN config — no manual `kubectl rollout restart` (or
`task flux:rotate-secret -- downloads`) is needed. Reloader runs in the
`reloader` namespace (`kubernetes/infrastructure/controllers/reloader/`). If you
ever need to force a restart out of band, `kubectl rollout restart
deployment/qbittorrent -n downloads` still works.

> **Disabling the VPN on qBittorrent requires one more edit.** qBittorrent
> carries a `gluetun-exporter` sidecar plus a `gluetun-qbittorrent` PodMonitor
> that feed the `VPNDown` alert. When `vpn_enabled: "false"`, Gluetun sleeps and
> its control server stops listening, so the exporter reports
> `gluetun_vpn_status=0` forever and `VPNDown` pages permanently and falsely. So
> in the **same** edit that sets `vpn_enabled: "false"`, remove the
> `gluetun-exporter` container and the `gluetun-qbittorrent` PodMonitor from
> `qbittorrent/resources.yaml` (and re-add both when re-enabling the VPN). NZBGet ships
> VPN-off and carries neither, which is why it has no false-fire problem.

### Check VPN Status

```bash
task downloads:vpn-status
```

Shows: VPN enabled/disabled, current provider, Gluetun logs (if enabled),
and public IP (to verify VPN is working).

### Update VPN Credentials

Credentials live in 1Password and flow through the `vpn-credentials`
ExternalSecret. To switch providers (PrivadoVPN vs VPN Unlimited), edit the
ExternalSecret to point at the other 1Password item:

```bash
# Edit externalsecret.yaml to reference the desired provider's 1P item title
vim kubernetes/apps/download-clients/externalsecret.yaml
# Change remoteRef.key to "PrivadoVPN Credentials" or "VPN Unlimited Credentials"
git commit -am "Switch downloads VPN to <provider>"
git push
task flux:rotate-secret -- downloads
```

After editing, also update each app's ConfigMap `vpn_provider` to match
(see previous section) — the credentials and the provider selector must
agree.

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

**Note**: Gluetun expects the provider name with a space (`"vpn unlimited"`),
not a hyphen. Set the ConfigMap value accordingly when switching to
VPN Unlimited.

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

# Delete and redeploy (Flux redeploys on next reconcile)
task downloads:delete
task flux:reconcile
```

### Common Issues

#### VPN Not Connecting

1. Check Gluetun logs: `task downloads:logs APP=nzbget CONTAINER=gluetun`
2. Verify credentials are correct in 1Password
3. Try switching VPN providers: edit the app's ConfigMap (`vpn_provider`) and the `externalsecret.yaml` to reference the alternate 1P item (see "Update VPN Credentials" above), commit + push, then `task flux:rotate-secret -- downloads`

#### App Has No Network When VPN Enabled

This is the killswitch working correctly. If Gluetun can't establish VPN:
1. Check VPN provider status.
2. Check credentials in 1Password match what Gluetun expects.
3. Temporarily disable VPN by editing the app's `vpn_enabled` ConfigMap entry in its YAML → commit + push → `task flux:rotate-secret -- downloads`.

#### Need to Use Different VPN Providers for Each App

Both apps share the same `vpn-credentials` secret. If you need different providers:
1. Split the ExternalSecret into two separate ExternalSecrets (e.g., `vpn-credentials-privado`, `vpn-credentials-vpnunlimited`) referencing the different 1P items.
2. Update each app's `envFrom`/`secretKeyRef` in the pod spec to point at its own secret.
3. Commit + push.

Today both apps use the single `vpn-credentials` secret (see
`externalsecret.yaml`), which is sourced from PrivadoVPN. Switching
providers for the whole stack is documented above under "Update VPN
Credentials"; switching providers per-app requires the split described
in this section.

## Files

- `namespace.yaml` - Downloads namespace (privileged PSS label for Gluetun CAP_NET_ADMIN)
- `_nfs-pv/` - shared NFS PV+PVC Kustomize component (TLS mountOptions defined once)
- `_vpn-sidecar/` - shared Gluetun VPN sidecar Kustomize component (killswitch defined once)
- `storage/` - per-app NFS PV/PVC overlays over `_nfs-pv/` + `storage/shared.yaml` (RWX media PV)
- `externalsecret.yaml` - ExternalSecret `vpn-credentials` sourced from 1Password via ESO
- `certificate.yaml` - Wildcard cert for *.esweiss.com (issued by cert-manager/letsencrypt-prod)
- `nzbget/` - NZBGet overlay over `_vpn-sidecar` (resources.yaml + kustomization.yaml)
- `qbittorrent/` - qBittorrent overlay over `_vpn-sidecar` (+ gluetun-exporter + PodMonitor)
- `prowlarr.yaml` - Prowlarr deployment
- `_arr/` - shared Deployment+Service Kustomize component for sonarr/radarr/lidarr
- `sonarr/`, `radarr/`, `lidarr/` - per-app overlays over the `_arr` component
- `pulsarr.yaml` - Pulsarr deployment
- `ingress-routes.yaml` - Traefik IngressRoutes with SSO
- `kustomization.yaml` - Kustomize configuration
