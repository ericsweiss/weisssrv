# Downloads Stack

VPN-protected download clients and media management applications with
per-app VPN control (Gluetun sidecar killswitch).

**`docs/21-download-clients-deployment.md` is the source of truth** for the
architecture diagram, component deep-dives, VPN/killswitch design, storage
layout and hard-linking, per-app configuration, SSO integration, maintenance
commands, and troubleshooting. This README covers only what lives in this
folder: the manifest layout, how the overlays wire together, and the VPN
toggle knobs.

## Deployment

This stack is Flux-managed. Everything in this folder is reconciled by the
top-level `apps` Kustomization.

- **Namespace**: `namespace.yaml` (labeled `pod-security.kubernetes.io/enforce: privileged` — required for Gluetun `CAP_NET_ADMIN`)
- **Secrets**: `externalsecret.yaml` — ExternalSecret `vpn-credentials` (provider OpenVPN creds under provider-prefixed keys, mounted at `/vpn-secrets`) + `gluetun-control-auth` (control-server roles `config.toml` + exporter apikey), both from 1Password via ESO
- **Workloads**: `prowlarr.yaml` and `pulsarr.yaml` are standalone manifests; `nzbget/` and `qbittorrent/` are overlays over the shared Gluetun VPN sidecar component (`_vpn-sidecar/`); `sonarr/`, `radarr/`, and `lidarr/` are overlays over the shared `_arr` component (`_arr/`). Each overlay's `resources.yaml` holds its app-specific resources (incl. the per-app VPN ConfigMap for nzbget/qbittorrent). Image tags use `${<app>_version}` placeholders substituted from the `cluster-versions` ConfigMap at reconcile time.
- **Storage**: `storage/` — per-app NFS PV/PVC overlays over the shared `_nfs-pv/` component (TLS mountOptions defined once), plus `storage/shared.yaml` for the RWX media PV
- **Ingress**: per-app IngressRoute overlays under `ingress-routes/` over the shared `_ingressroute/` component (middleware chain + TLS defined once) + `ingress-routes-ha-bypass.yaml` (full-host SSO bypass routes scoped to Home Assistant's IP, 192.168.0.154 — see docs/24)
- **NetworkPolicy**: `networkpolicy.yaml` (default-deny ingress + Traefik/observability allows, incl. the VPN-exporter scrape exception)
- **Certificate**: `certificate.yaml` (single wildcard cert for `*.esweiss.com`)
- **Autoscaling**: `vpa.yaml` (VPA recommendations for the stack — see docs/33)

Deploy workflow (edit + commit + push):

```bash
vim kubernetes/apps/download-clients/qbittorrent/resources.yaml  # or any file
git add kubernetes/apps/download-clients/
git commit -m "..."
git push

# Push triggers reconciliation via the GitLab agent's Flux Receiver
# (poll is the fallback); force it manually with:
task flux:reconcile

# Ops checks (unchanged)
task downloads:status
task downloads:vpn-status
```

Default VPN state: NZBGet sidecar VPN disabled, qBittorrent VPN enabled.
Toggle by editing `vpn_enabled` in the app's VPN ConfigMap (see VPN
Management below) and pushing.

## VPN Management

### Live VPN Toggles (operational, no git round-trip)

For day-2 ops there are three `task downloads:*` commands that flip VPN state
live without a commit/push cycle:

```bash
# Turn a client's VPN on or off (Reloader rolls the pod; waits for rollout)
task downloads:vpn -- APP=nzbget      STATE=on
task downloads:vpn -- APP=qbittorrent STATE=off

# Switch provider (+ optional country); runs a public-IP check afterwards
task downloads:vpn-provider -- APP=qbittorrent PROVIDER=privadovpn
task downloads:vpn-provider -- APP=qbittorrent PROVIDER=privadovpn COUNTRIES=Netherlands
# multi-word countries need the native (quoted) form:
task downloads:vpn-provider APP=qbittorrent PROVIDER=privadovpn COUNTRIES="United States"

task downloads:vpn-status     # per-app state + logs + public IP
task downloads:verify-vpn     # assert VPN egress != LAN egress + LAN containment
```

**How it's GitOps-safe.** Both `*-vpn-config` ConfigMaps carry
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`. Flux **creates** them on a
fresh cluster from the committed defaults, then never reconciles or reverts
them — so a live `kubectl patch` (what the tasks do) **sticks** instead of being
drift-reverted on the next reconcile. Reloader rolls the pod on the ConfigMap
change.

> **Divergence + resync.** The values committed in git are only the **bootstrap
> default**. Once a ConfigMap exists, editing it in git does **not** propagate —
> live state is the source of truth. To force git back on top (resync):
> ```bash
> kubectl delete configmap <app>-vpn-config -n downloads
> task flux:reconcile          # Flux recreates it from git
> ```
> The object is still tracked/prunable (unlike `reconcile: disabled`).

### Per-App VPN Control (committed default)

Each download client has a per-app ConfigMap (e.g., `nzbget-vpn-config`,
`qbittorrent-vpn-config`) that Gluetun reads. The Gluetun sidecar itself is the
shared `_vpn-sidecar/` component; the per-app ConfigMap lives in the app
overlay's `resources.yaml`. Editing it in git sets the bootstrap default (see
the divergence note above); the live tasks are what you use for ongoing ops.

```yaml
# nzbget-vpn-config / qbittorrent-vpn-config
data:
  vpn_enabled: "true"           # "true" or "false"
  vpn_provider: "privado"       # or "vpn unlimited" (Gluetun wants the space, not a hyphen)
  server_countries: "Netherlands"
```

**On the running cluster, do not edit this in git — use the live tasks.**
`IfNotPresent` means a `git push` that changes `vpn_enabled`/`vpn_provider` is
inert once the ConfigMap exists (no reconcile, no roll). Change live state with:

```bash
task downloads:vpn -- APP=qbittorrent STATE=off
task downloads:vpn-provider -- APP=qbittorrent PROVIDER=privadovpn [COUNTRIES=Netherlands]
```

Editing the committed default in git only matters for a **fresh cluster** (or
after a `kubectl delete configmap <app>-vpn-config -n downloads && task
flux:reconcile` resync — see the divergence note above):

```bash
vim kubernetes/apps/download-clients/qbittorrent/resources.yaml
# Find the ConfigMap section and edit vpn_enabled / vpn_provider

git add kubernetes/apps/download-clients/qbittorrent/resources.yaml
git commit -m "Disable VPN on qbittorrent" # or similar
git push
```

On a cluster where the ConfigMap does **not** yet exist, Flux creates it and —
because the nzbget/qbittorrent Deployments carry the
`reloader.stakater.com/auto: "true"` annotation — **stakater/Reloader
automatically rolls the pod** to pick up the mounted VPN config (Reloader runs in
the `reloader` namespace, `kubernetes/infrastructure/controllers/reloader/`). On
an existing cluster this push does nothing; use the live tasks above, or force a
restart out of band with `kubectl rollout restart deployment/qbittorrent -n
downloads`.

> **Disabling the VPN on qBittorrent requires one more edit.** qBittorrent
> carries a `gluetun-exporter` sidecar plus a `gluetun-qbittorrent` PodMonitor
> that feed the `VPNDown` alert. When `vpn_enabled: "false"`, Gluetun sleeps and
> its control server stops listening, so the exporter reports
> `gluetun_vpn_status=0` forever and `VPNDown` pages permanently and falsely. So
> in the **same** edit that sets `vpn_enabled: "false"`, remove the
> `gluetun-exporter` container and the `gluetun-qbittorrent` PodMonitor from
> `qbittorrent/resources.yaml` (and re-add both when re-enabling the VPN). NZBGet ships
> VPN-off and carries neither, which is why it has no false-fire problem; if
> NZBGet's VPN is ever enabled, replicate the exporter sidecar + PodMonitor there.

### Check VPN Status

```bash
task downloads:vpn-status
```

Shows: VPN enabled/disabled, current provider, Gluetun logs (if enabled),
and public IP (to verify VPN is working).

### Provider Switching

Both providers' credentials live in the single `vpn-credentials` Secret under
**provider-prefixed keys** (mounted read-only at `/vpn-secrets`). The gluetun
command wrapper reads `vpn_provider` from the mounted VPN ConfigMap and exports
the matching `OPENVPN_*_SECRETFILE` paths, so switching provider is just a
ConfigMap change (`task downloads:vpn-provider`) — no manifest edits, no git
churn, and credentials never touch git or `kubectl describe`.

| `PROVIDER=` | gluetun `VPN_SERVICE_PROVIDER` | Auth | `vpn-credentials` keys | Status |
|---|---|---|---|---|
| `privadovpn` | `privado` | OpenVPN user/pass | `privadovpn-user`, `privadovpn-password` | Wired (default) |
| `vpnunlimited` | `vpn unlimited` | OpenVPN user/pass **and** client cert/key | `vpnunlimited-user`, `vpnunlimited-password`, `vpnunlimited-clientcrt`, `vpnunlimited-clientkey` | Mechanism wired; needs all four in 1P (see below) |

**VPN Unlimited (KeepSolid) needs all four of user, password, cert and key.**
gluetun's generated OpenVPN config is cert/key-based (`AuthUserPass=false`, so
the tunnel authenticates with an OpenVPN **client certificate + key**), but its
settings validation still requires a non-empty **user + password** for the
provider — supply only cert/key and the sidecar fails validation and
crash-loops. To enable `PROVIDER=vpnunlimited`:

1. In the VPN Unlimited portal, generate a Manual/OpenVPN config for one device;
   note the login user/password it issues.
2. On the **VPN Unlimited Credentials** 1Password item (docs/15) add
   `openvpn-user`, `openvpn-password`, `openvpn-clientcrt` (full PEM
   `<cert>...</cert>` block) and `openvpn-clientkey` (full PEM `<key>...</key>`
   block).
3. Uncomment the four `vpnunlimited-*` entries in `externalsecret.yaml`, commit,
   push (Flux syncs the Secret), then `task downloads:vpn-provider -- APP=... PROVIDER=vpnunlimited`.

### Control-Server Auth

Gluetun's HTTP control server (loopback `127.0.0.1:8001`) is role-authenticated
via a `config.toml` rendered by ESO (`gluetun-control-auth` Secret) and mounted
at `/gluetun-auth/config.toml`. A single `exporter` role grants an **API key**
to exactly the three routes the `gluetun-exporter` sidecar polls
(`GET /v1/vpn/status`, `/v1/publicip/ip`, `/v1/openvpn/portforwarded`); every
other control route returns 401. The exporter authenticates with the same key
via `GLUETUN_APIKEY` (X-API-Key header), sourced from the same Secret so the two
can never drift. This both locks the control API down and silences gluetun's
per-request "route ... is unprotected by default" WARN spam (a **named** role
suppresses the warning — gluetun only warns for its auto-generated `public`/none
role). Rotate the key by updating `gluetun-control-apikey` in 1Password, then
**re-syncing the `gluetun-control-auth` ExternalSecret and restarting the
pods** — `task flux:rotate-secret -- downloads` force-syncs
`gluetun-control-auth` + `vpn-credentials` and rolls nzbget/qbittorrent. A bare
pod restart alone re-reads the *old* key: ESO only re-fetches on its 24h
`refreshInterval` and Reloader ignores Secret changes by design.

### Rotate VPN Credentials

Credentials flow through the `vpn-credentials` ExternalSecret. After changing a
value in 1Password (e.g. `PrivadoVPN Credentials/openvpn-password`):

```bash
task flux:rotate-secret -- downloads   # re-fetch + restart nzbget/qbittorrent
```

## Files

- `namespace.yaml` - Downloads namespace (privileged PSS label for Gluetun CAP_NET_ADMIN)
- `_nfs-pv/` - shared NFS PV+PVC Kustomize component (TLS mountOptions defined once)
- `_nfs-pv-arr/` - *arr variant extending `_nfs-pv` (10Gi + actimeo/lookupcache), used by sonarr/radarr/lidarr
- `_vpn-sidecar/` - shared Gluetun VPN sidecar Kustomize component (killswitch defined once)
- `storage/` - per-app NFS PV/PVC overlays over `_nfs-pv/` + `storage/shared.yaml` (RWX media PV)
- `externalsecret.yaml` - ExternalSecrets `vpn-credentials` (provider OpenVPN creds) + `gluetun-control-auth` (control-server `config.toml` roles + exporter apikey), from 1Password via ESO
- `certificate.yaml` - Wildcard cert for *.esweiss.com (issued by cert-manager/letsencrypt-prod)
- `nzbget/` - NZBGet overlay over `_vpn-sidecar` (resources.yaml + kustomization.yaml)
- `qbittorrent/` - qBittorrent overlay over `_vpn-sidecar` (+ gluetun-exporter + PodMonitor)
- `prowlarr.yaml` - Prowlarr deployment
- `_arr/` - shared Deployment+Service Kustomize component for sonarr/radarr/lidarr
- `sonarr/`, `radarr/`, `lidarr/` - per-app overlays over the `_arr` component
- `pulsarr.yaml` - Pulsarr deployment
- `_ingressroute/`, `ingress-routes/` - shared IngressRoute component + per-app overlays (SSO middleware chain defined once)
- `ingress-routes-ha-bypass.yaml` - *arr API bypass routes for Home Assistant (docs/24)
- `networkpolicy.yaml` - default-deny ingress + Traefik/observability allows (incl. VPN-exporter scrape)
- `vpa.yaml` - VPA resources for the stack (docs/33)
- `kustomization.yaml` - Kustomize configuration

For logs/shell/restart commands, storage paths, service URLs, DNS rewrites,
and all troubleshooting, see `docs/21-download-clients-deployment.md`.
