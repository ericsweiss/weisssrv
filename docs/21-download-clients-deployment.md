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
API-key authenticated (`kubernetes/apps/download-clients/README.md`
§ Control-Server Auth); the exporter
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

**Adding a second provider.** Privado is the only wired one. A new provider is
one MR: its credential fields on a 1Password item, matching `secretKey` entries
in `externalsecret.yaml`, a `case` arm in `_vpn-sidecar/vpn-sidecar.yaml`, and
matching arms in `scripts/vpn-credcheck.sh` and
`scripts/downloads-vpn-provider.sh`. `scripts/check-vpn-provider-parity.py`
holds those three in step. Note that gluetun's settings validation requires a
non-empty user and password even for a provider whose OpenVPN config is
cert/key-based, so a cert-only provider still needs all four fields.

**For the Gluetun control-server API key:**
```
# Item: "Download Client API Keys" (existing item — add one field)
# Vault: Homelab
# Field:
#   - gluetun-control-apikey: generate with `openssl rand -hex 32`
# Rendered into the gluetun-control-auth Secret's config.toml and consumed by
# the gluetun-exporter as GLUETUN_APIKEY.
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
# Directories get setgid so new files inherit the media group; files do not.
ssh pve-nas-01 "sudo find /mnt/nvme/media -type d -exec chmod 2775 {} +"
ssh pve-nas-01 "sudo find /mnt/nvme/media -type f -exec chmod 664 {} +"
```

### 3. DNS Configuration

Add DNS rewrites to `adguard_home_rewrites` in
`ansible/inventories/prod/group_vars/dns.yml`, pointing every service at the
internal Traefik VIP (10.0.10.101), then `task dns:deploy`. The role deletes
any rewrite not in the codified list, so a UI-added entry is reverted on the next
deploy (docs/08 § DNS rewrites):

```yaml
- domain: nzbget.esweiss.com
  answer: 10.0.10.101
- domain: qbittorrent.esweiss.com
  answer: 10.0.10.101
- domain: prowlarr.esweiss.com
  answer: 10.0.10.101
- domain: tv.esweiss.com           # Sonarr
  answer: 10.0.10.101
- domain: movies.esweiss.com       # Radarr
  answer: 10.0.10.101
- domain: music.esweiss.com        # Lidarr
  answer: 10.0.10.101
- domain: pulsarr.esweiss.com
  answer: 10.0.10.101
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
├── _nfs-pv-arr/                # *arr variant of _nfs-pv (10Gi + actimeo/lookupcache/local_lock=all)
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
```

A second ExternalSecret, `gluetun-control-auth`, renders the control-server
roles `config.toml` (with the exporter apikey) and exposes the raw `apikey` key
for the exporter env.

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

The committed defaults are **nzbget VPN off, qbittorrent VPN on**, and the
`vpn-credentials` ExternalSecret carries the per-provider 1Password fields listed
in § Prerequisites 1.

Day-2 VPN operations — the live `task downloads:vpn*` toggles, provider
switching, control-server auth, per-app VPN control and credential rotation —
are documented next to the manifests in
`kubernetes/apps/download-clients/README.md` § VPN Management, which is
canonical for them.

Enabling a VPN, or switching provider, whose credentials are not wired rolls the
pod into gluetun settings-validation failure and CrashLoopBackOff. Both tasks
pre-flight with `scripts/vpn-credcheck.sh` and refuse before patching.

**Provider → required `vpn-credentials` keys.** This table is canonical;
`kubernetes/apps/download-clients/README.md` points at it, and
`scripts/check-vpn-provider-parity.py` gates the three code copies of the same
map.

| Provider | Required keys |
|---|---|
| `privadovpn` | `privadovpn-user`, `privadovpn-password` |

**Single-resolver dependency (VPN on).** While gluetun is up, nzbget and
qbittorrent resolve via `DNS_ADDRESS=10.0.10.150` only; dns-02 (.160) is **not**
a fallback on that path — the pod `dnsConfig` applies only when gluetun is
disabled or crashed. The symptom is indexer and tracker lookups failing while
the tunnel and the pods are healthy. Check dns-01 liveness and
`kubectl -n downloads logs <pod> -c gluetun | grep -i dns`.

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
2. Credentials: the `ControlUsername` / `ControlPassword` pair on the 1Password
   **NZBGet** item, injected by Authentik (see § Authentik SSO Integration 2).
   On a fresh install, change them away from the upstream `nzbget` /
   `tegbzn6789` defaults in Settings > Security and mirror the values into
   1Password before exposing the route; the rotation procedure is in
   [docs/15](15-credential-rotation.md).
3. Configure:
   - **MainDir**: `/media/downloads/nzbget`
   - **InterDir**: `/media/downloads/nzbget/intermediate`
   - **DestDir**: `/media/downloads/nzbget/complete`
   - **ScriptDir**: `/media/downloads/nzbget/scripts` (optional)

### qBittorrent

1. Access: https://qbittorrent.esweiss.com
2. First-run password: qBittorrent 5.x generates a random temporary WebUI
   password on first start — read it from
   `kubectl logs -n downloads deploy/qbittorrent -c qbittorrent`, then set a
   permanent one in Tools > Options > Web UI.
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

### 1. *arr apps — `AuthenticationMethod=External` (declarative init container)

Sonarr/Radarr/Lidarr/Prowlarr present no login of their own: `config.xml` says
`External`, so the app trusts the reverse proxy. That is safe because the
`authentik-auth` middleware is the only path in from outside the cluster
(NetworkPolicy), which makes forward-auth the login rather than an extra gate.

Left on `Forms`, these apps show their own form after Authentik on **every**
browser request, LAN included — not just over Tailscale. A request carrying
`X-Forwarded-For` from a proxy that is neither loopback nor named in
`<TrustedNetworks>` is read as non-local whatever address it forwards, and the
same configuration logs `Auth-Success ip <Traefik pod IP>` instead of the real
client. So the init container sets **both** elements: `External`, and
`<TrustedNetworks>` = the pod CIDR, which Traefik forwards from.
**`TrustedNetworks` names the PROXY, not the clients** — widening it to the LAN
or the tailnet would not help, because the proxy would still be untrusted.

The **`seed-external-auth` init container** (`_arr/deployment.yaml`, plus
`prowlarr.yaml`'s own copy) holds both on every start. It normalizes rather than
patches — every copy and spelling of the two elements is stripped and exactly one
of each is reinserted, so a stale duplicate cannot sit in front of the live value
— writes the result to a temporary file it moves into place, and fails the pod
rather than booting into that form. On a fresh install it seeds a minimal
`config.xml` holding only those two elements, which each app merges its own
defaults into, so a new install never starts on a `Forms` default. The CIDR comes
from `cluster-config` as the `TRUSTED_NETWORKS` env var, because `config.xml` is
not Flux-substituted. Lidarr's older build has no `TrustedNetworks` support at
all, so there the element is inert and `External` alone carries the fix. API-key
clients (Homarr, Pulsarr, exportarr, intra-*arr) are untouched. Trust model is
Pulsarr's below — NetworkPolicy plus `authentik-auth` and the `media-admins`
binding, with `kubectl port-forward` as break-glass.

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
| `10.0.10.0/24` (`cluster_lan_cidr`) | direct LAN access |
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

# Restart specific app — delete the pods, never `rollout restart`: these
# Deployments are Flux-managed and kustomize-controller drift-reverts the
# restart annotation (docs/29 § Restarting a Flux-managed workload).
kubectl delete pod -n downloads -l app.kubernetes.io/name=sonarr
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

### *arr restarts during a library rescan

Symptom: sonarr, radarr or lidarr restarts during a Refresh Series with
`rescan=always`. The pod exits 137 and the previous container logged
`database is locked`. Node CPU, NAS I/O and app memory are all idle.

Cause: the *arr config database is WAL-mode SQLite on an NFS mount. Under
server-side locking every SQLite lock is a round trip to the NAS, and `/ping`
stalls long enough for the liveness probe to kill the container.

What is set: the config PVs mount with `local_lock=all`
(`_nfs-pv-arr/kustomization.yaml`), which keeps locks client-local. That is safe
because each *arr app runs one replica with strategy `Recreate` and mounts only
its own appdata export. The liveness budget is five minutes (30s x 10,
`_arr/deployment.yaml`). Readiness drains the pod out of the Service first: a
hung probe runs to its 20s timeout, so five failures take about 100 seconds.

Applying it: mount options are read at mount time, so a running pod keeps the
old options. After the reconcile, recreate each *arr pod once with
`kubectl delete pod -n downloads -l app.kubernetes.io/name=<app>` (a pod delete,
not `rollout restart`, per docs/29 § Restarting a Flux-managed workload), then
confirm with
`kubectl get pv downloads-appdata-<app> -o jsonpath="{.spec.mountOptions}"` and
`nfsstat -m` on the node.

Verify: liveness failures should sit at roughly zero per day.

```promql
sum by (pod) (
  increase(prober_probe_total{namespace="downloads",
                              probe_type="Liveness",
                              result="failed"}[1d])
)
```

Escalation: if failures persist, move the SQLite files off NFS onto block
storage. See [docs/16](16-next-steps.md) § Storage.

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
- Manifests and day-2 VPN operations: `kubernetes/apps/download-clients/README.md`
