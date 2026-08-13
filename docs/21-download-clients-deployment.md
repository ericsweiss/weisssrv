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
`observability` namespace like the other exporters. The control server is
API-key authenticated (see VPN Management §Control-Server Auth); the exporter
sends the key via `GLUETUN_APIKEY`.

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
  `vpn_enabled` — see the download-clients README §Per-App VPN Control for the
  canonical coupling warning and step-by-step.

## Prerequisites

### 1. 1Password Setup

Create VPN credential items in your Homelab vault:

**For PrivadoVPN (default provider — user/password auth):**
```
# Item: "PrivadoVPN Credentials"
# Vault: Homelab
# Fields:
#   - openvpn-user: Your PrivadoVPN username
#   - openvpn-password: Your PrivadoVPN password
```

**For VPN Unlimited (KeepSolid) — user/password + client cert/key, optional:**
```
# Item: "VPN Unlimited Credentials"
# Vault: Homelab
# Gluetun needs ALL FOUR fields below. Its generated OpenVPN config for VPN
# Unlimited is cert/key-based (auth-user-pass off), but gluetun's settings
# validation still requires a non-empty user + password for the provider — omit
# either and the sidecar fails validation and crash-loops. Generate a
# Manual/OpenVPN config for one device in the VPN Unlimited portal, note the
# login it issues, then set:
#   - openvpn-user: the VPN Unlimited (KeepSolid) OpenVPN username
#   - openvpn-password: the VPN Unlimited (KeepSolid) OpenVPN password
#   - openvpn-clientcrt: the full PEM <cert>...</cert> block
#   - openvpn-clientkey: the full PEM <key>...</key> block
# Then uncomment the vpnunlimited-* entries in externalsecret.yaml.
```

**For the Gluetun control-server API key:**
```
# Item: "Download Client API Keys" (existing item — add one field)
# Vault: Homelab
# Field:
#   - gluetun-control-apikey: generate with `openssl rand -hex 32`
# Rendered into the gluetun-control-auth Secret's config.toml and consumed by
# the gluetun-exporter as GLUETUN_APIKEY (see VPN Operations below).
```

### 2. NFS Storage Preparation

The NFS exports and per-app appdata directories are provisioned by the
`nas_storage` role: the `nas_storage_appdata_dirs` list in the role defaults creates
`/mnt/ssd/appdata/<app>` (owned `1000:2000`) for every app that persists
config on the appdata export. No manual `mkdir` is needed — add the app name
to `nas_storage_appdata_dirs` and run `task storage:deploy`. Verify:

```bash
ssh pve-nas-01 "ls -la /mnt/ssd/appdata/"
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
├── _vpn-sidecar/               # shared Gluetun VPN sidecar Component (killswitch defined once)
├── _nfs-pv/                    # shared NFS PV+PVC Component (TLS mountOptions defined once)
├── _nfs-pv-arr/                # *arr variant of _nfs-pv (10Gi + actimeo/lookupcache)
├── storage/                    # per-app NFS PV/PVC overlays over _nfs-pv + storage/shared.yaml (RWX media PV)
├── certificate.yaml            # Wildcard cert in the downloads namespace
├── nzbget/                     # overlay over _vpn-sidecar: resources.yaml (Deployment + nzbget-vpn-config) + kustomization.yaml
├── qbittorrent/                # as nzbget + gluetun-exporter + PodMonitor (VPN-always-on)
├── prowlarr.yaml
├── _arr/                       # shared *arr base: deployment.yaml + service.yaml + kustomization.yaml
├── sonarr/                     # per-*arr overlay (kustomization.yaml) over _arr base
├── radarr/                     # per-*arr overlay
├── lidarr/                     # per-*arr overlay
├── pulsarr.yaml
├── _ingressroute/              # shared IngressRoute skeleton component (middleware chain + TLS once)
├── ingress-routes/             # per-app IngressRoute overlays (name/host/service/port only)
├── ingress-routes-ha-bypass.yaml  # High-priority routes bypassing SSO for Home Assistant's IP
├── networkpolicy.yaml          # default-deny + per-app allowlist (incl. observability->qbittorrent:8002 VPN scrape)
├── vpa.yaml                    # VerticalPodAutoscalers (per-container sizing, incl. gluetun-exporter)
├── README.md
└── kustomization.yaml
```

### Deploying Changes (ongoing work)

1. Edit the appropriate YAML under `kubernetes/apps/download-clients/`.
2. Commit and push.
3. The GitLab agent's Flux module triggers reconciliation on push (fallback:
   ~1-minute poll).

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
    # Provider-prefixed keys: the gluetun wrapper picks the set that matches
    # vpn_provider and mounts them as files at /vpn-secrets (SECRETFILE variants).
    - secretKey: privadovpn-user
      remoteRef:
        key: PrivadoVPN Credentials
        property: openvpn-user
    - secretKey: privadovpn-password
      remoteRef:
        key: PrivadoVPN Credentials
        property: openvpn-password
    # VPN Unlimited (user/password + client cert/key) — uncomment all four after
    # populating 1P (see above); gluetun requires every one of them.
    # - secretKey: vpnunlimited-user
    #   remoteRef: {key: VPN Unlimited Credentials, property: openvpn-user}
    # - secretKey: vpnunlimited-password
    #   remoteRef: {key: VPN Unlimited Credentials, property: openvpn-password}
    # - secretKey: vpnunlimited-clientcrt
    #   remoteRef: {key: VPN Unlimited Credentials, property: openvpn-clientcrt}
    # - secretKey: vpnunlimited-clientkey
    #   remoteRef: {key: VPN Unlimited Credentials, property: openvpn-clientkey}
```

A second ExternalSecret, `gluetun-control-auth`, renders the control-server
roles `config.toml` (with the exporter apikey) and exposes the raw `apikey` key
for the exporter env — see VPN Operations below.

The 1P Connect provider uses `key: <item-title>` and `property: <field-name>`.
See `docs/29-flux-operations.md` for the format rules.

To rotate VPN credentials: update the value in 1Password, then either wait
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

### Live VPN Operations (no git round-trip)

Three `task downloads:*` commands change VPN state live for day-2 ops:

```bash
task downloads:vpn -- APP=nzbget      STATE=on          # turn a client's VPN on/off
task downloads:vpn -- APP=qbittorrent STATE=off
task downloads:vpn-provider -- APP=qbittorrent PROVIDER=privadovpn [COUNTRIES=Netherlands]
task downloads:vpn-status                                # per-app state + logs + public IP
task downloads:verify-vpn                                # egress != LAN + LAN containment
```

Each command `kubectl patch`es the app's `*-vpn-config` ConfigMap; **Reloader**
rolls the pod and the task waits for the rollout. Provider aliases normalise to
gluetun's exact `VPN_SERVICE_PROVIDER` string (`privadovpn` → `privado`,
`vpnunlimited` → `vpn unlimited`). Multi-word countries need the native
(quoted) form: `task downloads:vpn-provider APP=... PROVIDER=... COUNTRIES="United States"`.

**GitOps-safe by design.** Both `*-vpn-config` ConfigMaps carry
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`. Flux **creates** them on a
fresh cluster from the committed defaults, then never reconciles/reverts them,
so a live patch is not drift-reverted. The committed values are only the
**bootstrap default**; once the object exists, live state is authoritative and
git edits do not propagate. To resync git on top:

```bash
kubectl delete configmap <app>-vpn-config -n downloads
task flux:reconcile          # Flux recreates it from git
```

Committed defaults stay: **nzbget VPN off, qbittorrent VPN on**.

> Turning **qbittorrent** VPN off via the live task leaves its gluetun-exporter
> + PodMonitor running, so `gluetun_vpn_status=0` and `VPNDown` fires after 15m.
> The live toggle is for short-lived ops; for a durable VPN-off qbittorrent also
> drop the exporter + PodMonitor in git (README §Per-App VPN Control).

### Provider Switching

Both providers' credentials live in the one `vpn-credentials` Secret under
provider-prefixed keys (mounted read-only at `/vpn-secrets`). The gluetun
command wrapper reads `vpn_provider` and exports the matching
`OPENVPN_*_SECRETFILE` paths — files, not env, so creds never appear in
`kubectl describe pod`.

| `PROVIDER=` | gluetun provider | Auth | `vpn-credentials` keys | Status |
|---|---|---|---|---|
| `privadovpn` | `privado` | OpenVPN user/pass | `privadovpn-user`, `privadovpn-password` | Wired (default) |
| `vpnunlimited` | `vpn unlimited` | OpenVPN user/pass **and** client cert/key | `vpnunlimited-user`, `vpnunlimited-password`, `vpnunlimited-clientcrt`, `vpnunlimited-clientkey` | Mechanism wired; needs all four in 1P (Prerequisites §1) |

VPN Unlimited (KeepSolid) authenticates the tunnel with a client cert/key, but
gluetun's settings validation additionally requires a non-empty user + password
for the provider — so all four keys must be present. `task
downloads:vpn-provider` pre-flights the target provider's keys against the live
`vpn-credentials` Secret: if any are missing it **refuses before patching** when
the app's VPN is ON (so a VPN-on client is never rolled into CrashLoopBackOff),
and only saves the selection with a warning when the VPN is OFF. Populate the
credentials (or switch back) before enabling the VPN on that provider.

### Control-Server Auth (WARN suppression)

Gluetun's loopback control server (`127.0.0.1:8001`) is role-authenticated via a
`config.toml` rendered by ESO (`gluetun-control-auth` Secret) and mounted at
`/gluetun-auth/config.toml` (`HTTP_CONTROL_SERVER_AUTH_CONFIG_FILEPATH` — the
default `/gluetun/auth/config.toml` can't be used because `/gluetun` is a
read-only configMap mount). One `exporter` role grants an **API key** to exactly
the three routes the gluetun-exporter polls (`GET /v1/vpn/status`,
`/v1/publicip/ip`, `/v1/openvpn/portforwarded`); all other control routes return
401. The exporter authenticates with the same key via `GLUETUN_APIKEY`
(X-API-Key header) from the same Secret, so config and client never drift. A
**named** role suppresses gluetun's per-request "route ... is unprotected by
default" WARN spam (gluetun only warns for its auto-generated `public`/none
role), which is what was polluting `task downloads:vpn-status`. Rotate the key
by updating `gluetun-control-apikey` in 1Password, then re-syncing the
`gluetun-control-auth` ExternalSecret and restarting the pods:
`task flux:rotate-secret -- downloads` force-syncs `gluetun-control-auth` +
`vpn-credentials` and rolls nzbget/qbittorrent. A bare pod restart alone
re-reads the *old* key — ESO only re-fetches on its 24h `refreshInterval` and
Reloader ignores Secret changes by design.

### Per-App VPN Control (committed default)

VPN enablement and provider selection are per-app ConfigMaps in each app
overlay's `resources.yaml`: `nzbget-vpn-config` in `nzbget/resources.yaml` and
`qbittorrent-vpn-config` in `qbittorrent/resources.yaml`. The Gluetun sidecar
itself is the shared `_vpn-sidecar/` Kustomize Component (killswitch defined
once); each overlay injects it and points it at the matching ConfigMap.

> **Editing these ConfigMaps in git only sets the *bootstrap default*.** Both
> carry `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so Flux **creates**
> them once (on a fresh cluster, or after a delete+reconcile resync) and then
> **never re-applies git edits to them**. On the running cluster the object
> already exists, so a `git push` that changes `vpn_enabled`/`vpn_provider` does
> **nothing** — no reconcile, no Reloader roll, the pod keeps its current VPN
> state. This is deliberate: it is what makes the live tasks below stick. See
> "Live VPN Operations" above for the full divergence/resync explanation.

**To change VPN state on the running cluster, use the live tasks** (they
`kubectl patch` the live ConfigMap, which Reloader then rolls):

```bash
task downloads:vpn -- APP=<nzbget|qbittorrent> STATE=<on|off>
task downloads:vpn-provider -- APP=<...> PROVIDER=<privadovpn|vpnunlimited> [COUNTRIES=<...>]
```

**Editing the committed default (fresh cluster / after a resync only).** Change
the value that a brand-new cluster will bootstrap with — or that a
delete+reconcile resync will re-apply:

1. Edit the `<app>-vpn-config` ConfigMap section inside `nzbget/resources.yaml`
   or `qbittorrent/resources.yaml` (set `vpn_enabled: "true"` / `"false"` and
   `vpn_provider: "privado"` / `"vpn unlimited"`).
2. Commit and push.
3. On a cluster where the ConfigMap does **not** yet exist, Flux creates it and
   **stakater/Reloader rolls the pod automatically** (the nzbget/qbittorrent
   Deployments carry `reloader.stakater.com/auto: "true"`; Reloader runs in the
   `reloader` namespace, `kubernetes/infrastructure/controllers/reloader/`). On
   an existing cluster this step does nothing — to force git back on top, first
   `kubectl delete configmap <app>-vpn-config -n downloads && task flux:reconcile`
   (see the resync block under "Live VPN Operations"), or just use the live
   tasks above.

**Turning qBittorrent's VPN off needs a coupled edit** (exporter + PodMonitor)
— see the download-clients README §Per-App VPN Control before doing it.

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

1. Update the value in 1Password (`PrivadoVPN Credentials/openvpn-password`).
2. Trigger refresh: `task flux:rotate-secret -- downloads`

To switch provider, use `task downloads:vpn-provider` (Live VPN Operations
above) — no manifest edit needed. VPN Unlimited additionally needs its
`vpnunlimited-*` entries populated in 1Password + uncommented in
`externalsecret.yaml` (Provider Switching above).

## Storage Layout

### Container Mount Points

The media NFS export (`/export/media` on the NAS, the mergerfs view of
`/mnt/media`) is mounted unevenly by design: **sonarr/radarr/lidarr** mount the
full `/media`; **nzbget/qbittorrent** mount only `/media/downloads`
(`subPath: downloads`) so they cannot see `/media/library`;
**prowlarr/pulsarr** mount no media at all. The full tree as the *arr apps
see it:

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

> **AVX Requirement**: Pulsarr uses the Bun JavaScript runtime (v0.10.0+) which
> requires AVX CPU instructions. The three Core 2 Quad opt agents do NOT support
> AVX and crash it with "Illegal instruction". Its `nodeSelector` is therefore
> `esweiss.com/general: "true"` + `esweiss.com/cpu: "modern"` — the cpu label
> (docs/33) is the constraint that actually matters, and it is carried today by
> k3s-agt-nas-01, k3s-agt-laptop-01 and k3s-agt-prec-01.

1. Access: https://pulsarr.esweiss.com
2. Configure:
   - **Plex Token**: Get from Plex account settings
   - **Sonarr URL**: `http://sonarr.downloads.svc.cluster.local:8989`
   - **Radarr URL**: `http://radarr.downloads.svc.cluster.local:7878`
   - **API Keys**: Get from each app's settings

## Authentik SSO Integration

All apps are protected by Authentik forward-auth on their IngressRoutes
(`authentik-auth` middleware; `media-admins` group binding — the providers,
applications, and bindings are code in `terraform/authentik/`, docs/40). The
`lan-tailscale-only` middleware scopes the routes to LAN/Tailscale, and the
namespace NetworkPolicy makes that chain the only path in **from outside the
cluster** — its in-cluster ingress rules also admit Homarr's widgets and the
*arrs' own API clients (see § qBittorrent for the full peer list).

Because the *arrs/NZBGet/qBittorrent/Pulsarr have no native OIDC/SAML (a known
upstream limitation — see [Sonarr #2477](https://github.com/Sonarr/Sonarr/issues/2477)),
the single-login experience is achieved per app with one of four
passthrough mechanisms:

### 1. *arr apps — `Authentication: External` (in-app setting)

Sonarr/Radarr/Lidarr/Prowlarr: Settings > General > Security >
**Authentication = External**. The app skips its own login entirely and
trusts the reverse proxy. Safe because the only route in from outside the
cluster is Traefik (NetworkPolicy) and every Traefik route carries
forward-auth.

### 2. NZBGet — basic-auth credential injection (codified)

NZBGet validates HTTP Basic against `ControlUsername`/`ControlPassword`
(nzbget.conf) and has no External mode — so authentik injects the
credentials instead:

- The `NZBGet` proxy provider has `basic_auth_enabled` with the
  `nzbget_user`/`nzbget_password` attributes, stored on the `media-admins`
  group from the 1Password **NZBGet** item
  (`terraform/authentik/providers_proxy.tf` + `groups.tf`).
- The nzbget IngressRoute swaps the shared middleware for
  `authentik-auth-basic` (`ingress-routes/nzbget`) — the variant that
  forwards the injected `Authorization` header (the shared one strips it;
  see `kubernetes/apps/authentik/middleware.yaml`).
- The 1Password item's values must match nzbget.conf; rotating the pair
  means updating BOTH nzbget.conf (or its UI) and the 1P item, then a
  supervised terraform apply (docs/40).

In-cluster API clients (the *arrs → `nzbget:6789` via the Service) bypass
Traefik and keep using their own credentials — unaffected.

### 3. qBittorrent — trusted-subnet bypass (declarative init container)

qBittorrent's injection-equivalent is `WebUI\AuthSubnetWhitelist`: requests
from a whitelisted subnet skip the WebUI login. Traefik's forwarded requests
originate from its pod IP, so whitelisting the pod CIDR gives SSO users a
direct dashboard while the API credentials keep working everywhere.

The whitelist is runtime state on the config PVC, not git — qBittorrent
rewrites its conf on shutdown, so a hand-edit is lost. The
**`seed-webui-bypass` init container** (`qbittorrent/resources.yaml`) applies it
on every start instead: a busybox `awk` pass that rewrites the two
`WebUI\AuthSubnetWhitelist*` keys under `[Preferences]`, inserting them if
absent and no-op'ing on a not-yet-created conf. Three CIDRs are whitelisted,
substituted from `cluster-config`:

| CIDR | Why |
|---|---|
| `10.42.0.0/16` (`cluster_pod_cidr`) | the source IP of a Traefik-forwarded request |
| `192.168.0.0/24` (`cluster_lan_cidr`) | direct LAN access |
| `100.64.0.0/10` (`cluster_tailnet_cidr`) | Tailscale CGNAT |

**Trust model and residual risk.** The bypass is wider than "Traefik's pod IP":
anything on the LAN or the tailnet that can reach :8080 gets an unauthenticated
WebUI, and the whitelist cannot tell one pod from another. What bounds it is the
namespace NetworkPolicy, which is load-bearing — and it admits more than Traefik:

- `allow-traefik-ingress` — the SSO path, forward-auth on every route.
- `allow-homarr-ingress` — Homarr's widgets reach :8080 directly, deliberately
  outside the SSO perimeter (docs/41).
- `allow-intra-namespace-arr` — sonarr/radarr/lidarr/prowlarr/pulsarr reach
  :8080 as clients; they are inside the pod CIDR, so they too skip the login.

Removing those policies is not the risk; widening them is. Note the LAN and
tailnet entries mean a `kubectl port-forward` (which arrives from a node IP)
also lands in a trusted range — the login prompt is only expected from a source
outside all three CIDRs. Verify after a change that
`https://qbittorrent.esweiss.com` reaches the dashboard with no WebUI login, and
that the init container logged `whitelist already current` or
`AuthSubnetWhitelist now includes the pod CIDR`
(`kubectl logs -n downloads deploy/qbittorrent -c seed-webui-bypass`).

### 4. Pulsarr — native auth disabled (declarative env)

Pulsarr has no native OIDC and no HTTP Basic backend, so neither the
`External`-style trust nor the credential-injection route applies. Instead it
exposes `authenticationMethod`, which at `disabled` bypasses its own login for
every request. It is set right in the Deployment env
(`kubernetes/apps/download-clients/pulsarr.yaml`):

```yaml
- name: authenticationMethod
  value: disabled
```

This is the cleanest of the four patterns — one env var, no terraform, no
1Password item, no middleware swap, no per-start conf rewrite. Pulsarr treats env
vars as authoritative over its stored DB config on every boot, so the setting is
durable and the UI cannot silently re-enable the login (toggling auth in the
Pulsarr UI is a no-op after a restart — the value is env-managed).

**Trust model**: identical to the *arr `External` precedent — with native auth
off, the sole gate is the NetworkPolicy (admits ONLY the Traefik namespace to
`:3003`) plus the `authentik-auth` forward-auth middleware and the `media-admins`
binding on the IngressRoute. The IngressRoute keeps the shared `authentik-auth`
middleware (NOT `authentik-auth-basic` — there is no credential to inject).
**Residual risk**: same as qBittorrent — the NetworkPolicy is load-bearing;
Pulsarr is not in the HA-bypass route and no intra-namespace peer opens `:3003`.

**Recovery / break-glass**: there is no direct app login while disabled; flip the
env back to `required` (or `requiredExceptLocal`) to restore Pulsarr's native
login instantly — the DB admin account on the `/config` volume is untouched.
Equivalent emergency access is `kubectl port-forward svc/pulsarr -n downloads
3003:3003` (gated by kubeconfig possession, same posture as the *arr `External`
mode).

### Per-app access control

Per-app authorization is group-membership on the application's policy
binding (`terraform/authentik/policy_bindings.tf`) — all seven downloads
apps are gated by `media-admins`. Audit logging comes with the per-app
applications/providers, which are all code in `terraform/authentik/`.

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

# Flux rolls the Deployments on push (fallback: ~1-minute poll)
task flux:status
```

### Backup

App configs live on NFS under `/mnt/ssd/appdata` and are **already captured** by
the automated chain — no manual step is needed:

- `ssd/appdata → archive` raw-encrypted ZFS replication (`archive-backupctl`).
- The nightly restic walk into Backblaze B2, which covers every appdata `/config`
  directory ([docs/42](42-offsite-backup.md)).

Restore is file-wise from either tier — see
[docs/17 § Restore Procedures](17-disaster-recovery.md#restore-procedures).
Do not add an ad-hoc copy job here; a second uncoordinated backup path is how
retention and freshness monitoring drift apart.

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
3. Temporarily disable VPN with `task downloads:vpn -- APP=<app> STATE=off`,
   which patches the LIVE ConfigMap so Reloader rolls the pod. Editing
   `vpn_enabled` in git does **not** do this on a running cluster — those
   ConfigMaps carry `ssa: IfNotPresent` and are only bootstrap defaults (see
   "Per-App VPN Control").

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

## Related documentation

- [K3s Deployment Guide](./19-k3s-deployment.md)
- [Flux Operations](./29-flux-operations.md)
- [Storage Configuration](./07-fileservices.md)
- [DNS Configuration](./08-dns.md)
- Manifests: `kubernetes/apps/download-clients/`
