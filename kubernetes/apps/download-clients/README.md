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
- **Secret**: `externalsecret.yaml` — ExternalSecret `vpn-credentials` sourcing `openvpn-user` / `openvpn-password` from 1Password via ESO
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

### Per-App VPN Control

Each download client has a per-app ConfigMap (e.g., `nzbget-vpn-config`,
`qbittorrent-vpn-config`) that Gluetun reads. The Gluetun sidecar itself is the
shared `_vpn-sidecar/` component; the per-app ConfigMap lives in the app
overlay's `resources.yaml`:

```yaml
# nzbget-vpn-config / qbittorrent-vpn-config
data:
  vpn_enabled: "true"           # "true" or "false"
  vpn_provider: "privado"       # or "vpn unlimited" (Gluetun wants the space, not a hyphen)
  server_countries: "Netherlands"
```

Edit it to toggle `vpn_enabled` or change `vpn_provider`:

```bash
vim kubernetes/apps/download-clients/qbittorrent/resources.yaml
# Find the ConfigMap section and edit vpn_enabled / vpn_provider

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
> VPN-off and carries neither, which is why it has no false-fire problem; if
> NZBGet's VPN is ever enabled, replicate the exporter sidecar + PodMonitor there.

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

Both apps share the single `vpn-credentials` Secret. Per-app providers would
require splitting the ExternalSecret in two and pointing each app's
`secretKeyRef` at its own Secret.

## Files

- `namespace.yaml` - Downloads namespace (privileged PSS label for Gluetun CAP_NET_ADMIN)
- `_nfs-pv/` - shared NFS PV+PVC Kustomize component (TLS mountOptions defined once)
- `_nfs-pv-arr/` - *arr variant extending `_nfs-pv` (10Gi + actimeo/lookupcache), used by sonarr/radarr/lidarr
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
- `_ingressroute/`, `ingress-routes/` - shared IngressRoute component + per-app overlays (SSO middleware chain defined once)
- `ingress-routes-ha-bypass.yaml` - *arr API bypass routes for Home Assistant (docs/24)
- `networkpolicy.yaml` - default-deny ingress + Traefik/observability allows (incl. VPN-exporter scrape)
- `vpa.yaml` - VPA resources for the stack (docs/33)
- `kustomization.yaml` - Kustomize configuration

For logs/shell/restart commands, storage paths, service URLs, DNS rewrites,
and all troubleshooting, see `docs/21-download-clients-deployment.md`.
