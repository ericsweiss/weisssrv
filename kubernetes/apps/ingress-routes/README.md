# Ingress Routes

Traefik IngressRoute configurations for all services, migrated from Apache virtual hosts.

## Architecture

This configuration implements a **split-horizon DNS** setup:

- **ericsweiss.com** - Public domain (external DNS via Cloudflare)
- **esweiss.com** - Internal-only domain (internal DNS via AdGuard Home)

Services are categorized as either **public** (accessible from internet) or **internal-only** (LAN + Tailscale only).

### Public Services

Internet-accessible services (no IP restrictions):

- **bar** → http://192.168.0.103:3000 (pve-laptop-01)
  - bar.ericsweiss.com (public DNS via Cloudflare)
  - bar.esweiss.com (internal DNS via AdGuard Home)
- **food** → http://192.168.0.103:9925 (pve-laptop-01)
  - food.ericsweiss.com (public DNS via Cloudflare)
  - food.esweiss.com (internal DNS via AdGuard Home)
- **home** → http://192.168.0.104:8123 (Home Assistant with WebSocket)
  - home.ericsweiss.com (public DNS via Cloudflare)
  - home.esweiss.com (internal DNS via AdGuard Home)
- **plex** → http://192.168.0.152:32400
  - plex.ericsweiss.com (public DNS via Cloudflare)
  - plex.esweiss.com (internal DNS via AdGuard Home)

### Internal-Only Services

LAN + Tailscale only (IP allowlist enforced):

- **router** → http://192.168.0.1 (router web UI)
  - router.esweiss.com (internal DNS via AdGuard Home)
- **qbittorrent** → k8s pod on k3s-agt-nas-01 (future)
  - qbittorrent.esweiss.com (internal DNS via AdGuard Home)
- **nzbget** → k8s pod on k3s-agt-nas-01 (future)
  - nzbget.esweiss.com (internal DNS via AdGuard Home)
- **movies** → Radarr k8s pod (future)
  - movies.esweiss.com (internal DNS via AdGuard Home)
- **tv** → Sonarr k8s pod (future)
  - tv.esweiss.com (internal DNS via AdGuard Home)

Note: Internal-only services only have DNS rewrites for the **esweiss.com** (internal) domain. The ericsweiss.com variants will not resolve (no Cloudflare DNS, no AdGuard rewrite). This provides clean domain separation: esweiss.com = internal, ericsweiss.com = external. IngressRoutes accept both domain variants for flexibility (in case DNS strategy changes), but only esweiss.com is actually reachable.

## Prerequisites

- Traefik installed and configured
- cert-manager installed with wildcard certificates for both *.ericsweiss.com and *.esweiss.com
- Backend services running (some on external hosts, some in cluster)

## Middleware

### strip-www
Removes www prefix from public ericsweiss.com domains (301 redirect).

### hsts-header
Adds Strict-Transport-Security headers (182.5 days, includeSubdomains).

### lan-tailscale-only
IP allowlist for internal services:
- 192.168.0.0/24 (Local LAN)
- 100.64.0.0/10 (Tailscale CGNAT range)

## Deployment

```bash
cd kubernetes/apps/ingress-routes

# Deploy all middleware, services, and IngressRoutes
kubectl apply -k .

# Verify
kubectl get middleware -n traefik
kubectl get svc -n default
kubectl get ingressroute -n default
```

## DNS Configuration

### External DNS (Cloudflare - ericsweiss.com)

external-dns automatically creates A records in Cloudflare for public services with ericsweiss.com hosts:

```
bar.ericsweiss.com      → 192.168.0.100
food.ericsweiss.com     → 192.168.0.100
home.ericsweiss.com     → 192.168.0.100
plex.ericsweiss.com     → 192.168.0.100
```

Note: Internal-only services (router, qbittorrent, nzbget, movies, tv) do not have external DNS records in Cloudflare.

### Internal DNS (AdGuard Home)

DNS rewrites are managed via Ansible in `ansible/inventories/prod/group_vars/dns.yml`.
All esweiss.com services route to the internal VIP (192.168.0.101).
Internal-only services only have esweiss.com rewrites (ericsweiss.com variants do not resolve).

```
# Public services - internal access via .101
bar.esweiss.com         → 192.168.0.101
food.esweiss.com        → 192.168.0.101
home.esweiss.com        → 192.168.0.101
plex.esweiss.com        → 192.168.0.101

# Internal-only services - esweiss.com only
router.esweiss.com        → 192.168.0.101
qbittorrent.esweiss.com   → 192.168.0.101
nzbget.esweiss.com        → 192.168.0.101
movies.esweiss.com        → 192.168.0.101
tv.esweiss.com            → 192.168.0.101
```

## Verify Services

### Public Services (from internet or LAN)

```bash
# Test HTTP → HTTPS redirect
curl -v http://bar.ericsweiss.com

# Test HTTPS
curl -v https://bar.ericsweiss.com

# Test WWW stripping
curl -v http://www.bar.ericsweiss.com  # → https://bar.ericsweiss.com
```

### Internal Services (LAN/Tailscale only)

```bash
# From LAN or Tailscale
curl -v https://router.esweiss.com
curl -v https://qbittorrent.esweiss.com

# From internet - these domains don't resolve (no external DNS)
```

## Migration from Apache

These IngressRoutes replicate and improve upon Apache virtual host behavior:

- **Public services accept both domains** - ericsweiss.com and esweiss.com for consistency
- **Internal services have IngressRoutes for both domains** - But only esweiss.com resolves via DNS
- **WWW stripping** - Removes www prefix (301 redirect) on public services
- **HTTP → HTTPS redirect** - Global redirect via Traefik entrypoint
- **HSTS headers** - Strict-Transport-Security on all services
- **IP restrictions** - Internal services use allowlist middleware (LAN + Tailscale only)
- **WebSocket support** - Home Assistant works correctly
- **Split-horizon DNS** - ericsweiss.com = external (Cloudflare), esweiss.com = internal (AdGuard Home)

## TLS Configuration

IngressRoutes reference the appropriate TLS secret:
- **Public services**: `ericsweiss-com-tls` (external domain) and `esweiss-com-tls` (internal domain)
- **Internal-only services**: Both TLS secrets configured in IngressRoutes (for flexibility), but only esweiss.com resolves via DNS

Both certificates are wildcard certificates managed by cert-manager.

## Future Deployments

When deploying download clients and *arrs as k8s pods:

1. Deploy qBittorrent and NZBGet to k3s-agt-nas-01 (node selector)
2. Deploy Radarr and Sonarr to k3s cluster (various nodes)
3. IngressRoutes are already configured and waiting for Services with matching names:
   - `qbittorrent` (port 8080)
   - `nzbget` (port 6789)
   - `radarr` (port 7878)
   - `sonarr` (port 8989)

## Troubleshooting

```bash
# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik

# Check if backend services are reachable
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl -v http://bar-backend.default.svc.cluster.local:3000

# Verify TLS secrets exist
kubectl get secret ericsweiss-com-tls -n default
kubectl get secret esweiss-com-tls -n default

# Check middleware configuration
kubectl get middleware -n traefik -o yaml

# Test internal-only services from LAN
curl -v https://router.esweiss.com
curl -v https://qbittorrent.esweiss.com
```
