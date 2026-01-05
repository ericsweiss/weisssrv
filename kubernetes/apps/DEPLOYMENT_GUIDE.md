# Kubernetes Apps Deployment Guide

Complete deployment instructions for all k3s platform services and applications.

## Architecture Overview

### Split-Horizon DNS

- **ericsweiss.com** - Public domain (Cloudflare DNS, external-dns managed)
- **esweiss.com** - Internal-only domain (AdGuard Home DNS, manual configuration)

**All services accept both domains** for consistency and fail-safe behavior.

### Service Categories

**Public Services** (internet accessible, no IP restrictions):
- bar, food, home, plex
- Work on both *.ericsweiss.com and *.esweiss.com

**Internal Services** (LAN + Tailscale only, IP allowlist enforced):
- router, qbittorrent, nzbget, movies (radarr), tv (sonarr)
- Work on both *.ericsweiss.com (returns 403 from internet) and *.esweiss.com

### Infrastructure

- **Traefik LoadBalancer**: 192.168.0.100 (MetalLB public pool)
- **Cluster API**: 192.168.0.161 (kube-vip)
- **External DNS**: Cloudflare for ericsweiss.com
- **Internal DNS**: AdGuard Home for esweiss.com

## Prerequisites

1. **k3s cluster deployed and running**
   ```bash
   task k3s:deploy
   task k3s:kubeconfig
   export KUBECONFIG=~/.kube/config-k3s
   ```

2. **Platform services installed**
   - MetalLB (bootstrap)
   - Traefik (apps)
   - external-dns (apps)

3. **1Password CLI authenticated**
   ```bash
   eval $(op signin)
   ```

## Deployment Steps

### Step 1: Install cert-manager

cert-manager provides automatic TLS certificate management using Let's Encrypt.

```bash
cd kubernetes/apps/cert-manager

# Add Helm repository
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Create namespace
kubectl apply -f namespace.yaml

# Install cert-manager with CRDs
helm install cert-manager jetstack/cert-manager \
  -n cert-manager \
  -f values.yaml

# Wait for cert-manager to be ready
kubectl wait --namespace cert-manager \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/name=cert-manager \
  --timeout=90s

# Verify installation
kubectl get pods -n cert-manager
```

### Step 2: Configure Cloudflare API Token

```bash
# Get token from 1Password
export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare DNS Token/credential")

# Create secret in cert-manager namespace
kubectl create secret generic cloudflare-api-token \
  --namespace=cert-manager \
  --from-literal=api-token="${CLOUDFLARE_API_TOKEN}"

# Verify secret
kubectl get secret cloudflare-api-token -n cert-manager
```

### Step 3: Deploy Certificate Resources

```bash
# Still in kubernetes/apps/cert-manager

# Apply ClusterIssuer and Certificate
kubectl apply -k .

# Wait for certificate to be ready (may take 2-5 minutes)
kubectl wait --namespace default \
  --for=condition=ready certificate \
  ericsweiss-com-wildcard \
  --timeout=300s

# Verify certificate
kubectl get certificate -n default
kubectl describe certificate ericsweiss-com-wildcard -n default

# Verify TLS secret was created
kubectl get secret ericsweiss-com-tls -n default
```

**Expected output:**
```
NAME                      READY   SECRET                AGE
ericsweiss-com-wildcard   True    ericsweiss-com-tls    2m
```

### Step 4: Deploy Ingress Routes

```bash
cd kubernetes/apps/ingress-routes

# Deploy all middleware, services, and IngressRoutes
kubectl apply -k .

# Verify middleware
kubectl get middleware -n traefik

# Verify backend services
kubectl get svc -n default

# Verify IngressRoutes
kubectl get ingressroute -n default
```

**Expected output:**
```
# Middleware
NAME              AGE
hsts-header       10s
lan-tailscale-only   10s
strip-www         10s

# Services (external backends)
NAME                      TYPE        CLUSTER-IP      PORT(S)
bar-backend              ClusterIP   10.43.x.x       3000/TCP
food-backend             ClusterIP   10.43.x.x       9925/TCP
home-assistant-backend   ClusterIP   10.43.x.x       8123/TCP
plex-backend             ClusterIP   10.43.x.x       32400/TCP
router-backend           ClusterIP   10.43.x.x       80/TCP

# IngressRoutes
NAME             AGE
bar              10s
food             10s
home-assistant   10s
movies           10s
nzbget           10s
plex             10s
qbittorrent      10s
router           10s
tv               10s
```

### Step 5: Configure Internal DNS (AdGuard Home)

Configure DNS rewrites in AdGuard Home for ALL services using esweiss.com domain:

1. **Access AdGuard Home**: https://dns-01.esweiss.com or https://192.168.0.150
2. **Navigate to**: Filters → DNS rewrites
3. **Add DNS rewrites for all services**:
   ```
   bar.esweiss.com         → 192.168.0.100
   food.esweiss.com        → 192.168.0.100
   home.esweiss.com        → 192.168.0.100
   plex.esweiss.com        → 192.168.0.100
   router.esweiss.com      → 192.168.0.100
   qbittorrent.esweiss.com → 192.168.0.100
   nzbget.esweiss.com      → 192.168.0.100
   movies.esweiss.com      → 192.168.0.100
   tv.esweiss.com          → 192.168.0.100
   ```

**Note**: ericsweiss.com services are automatically configured by external-dns in Cloudflare. Both domains work for all services - internal services are protected by IP allowlist middleware.

### Step 6: Verify Deployment

#### Public Services (from anywhere)

```bash
# Test HTTP → HTTPS redirect
curl -v http://bar.ericsweiss.com
# Should redirect to https://bar.ericsweiss.com

# Test HTTPS (requires DNS propagation)
curl -v https://bar.ericsweiss.com
# Should proxy to 192.168.0.103:3000

# Test WWW stripping
curl -v http://www.bar.ericsweiss.com
# Should redirect to https://bar.ericsweiss.com

# Test other public services
curl -v https://food.ericsweiss.com
curl -v https://home.ericsweiss.com
curl -v https://plex.ericsweiss.com
```

#### Internal Services (from LAN or Tailscale)

```bash
# Test internal services (requires AdGuard Home DNS)
curl -v https://router.esweiss.com

# Verify IP allowlist (from internet - should fail)
curl -v https://router.ericsweiss.com
# Expected: 403 Forbidden
```

#### Verify External-DNS

```bash
# Check external-dns logs
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns

# Verify Cloudflare DNS records (requires Cloudflare API token)
# Should see A records for ALL services:
# - bar.ericsweiss.com
# - food.ericsweiss.com
# - home.ericsweiss.com
# - plex.ericsweiss.com
# - router.ericsweiss.com (protected by IP allowlist)
# - qbittorrent.ericsweiss.com (protected by IP allowlist)
# - nzbget.ericsweiss.com (protected by IP allowlist)
# - movies.ericsweiss.com (protected by IP allowlist)
# - tv.ericsweiss.com (protected by IP allowlist)
# All pointing to 192.168.0.100
```

## DNS Propagation

After deployment:

1. **External DNS (ericsweiss.com)**:
   - external-dns creates records in Cloudflare automatically
   - Propagation: Usually 1-5 minutes
   - Verify: `dig bar.ericsweiss.com`

2. **Internal DNS (esweiss.com)**:
   - Manual configuration in AdGuard Home
   - Effective immediately for clients using 192.168.0.150/160 DNS
   - Verify: `dig @192.168.0.150 router.esweiss.com`

## Testing Checklist

- [ ] cert-manager pods running
- [ ] Wildcard certificate issued (ericsweiss-com-wildcard)
- [ ] TLS secret created (ericsweiss-com-tls)
- [ ] Middleware deployed (3 total)
- [ ] Backend services created (5 total)
- [ ] IngressRoutes deployed (9 total)
- [ ] AdGuard Home DNS rewrites configured
- [ ] Public services accessible from internet
- [ ] Internal services accessible from LAN/Tailscale only
- [ ] Internal services blocked from internet (403)
- [ ] HTTP → HTTPS redirects working
- [ ] HSTS headers present
- [ ] WWW stripping working for public services

## Troubleshooting

### Certificate Not Issued

```bash
# Check certificate status
kubectl describe certificate ericsweiss-com-wildcard -n default

# Check certificate request
kubectl get certificaterequest -n default

# Check ACME challenge
kubectl get challenges -n default

# Check cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager
```

### IngressRoute Not Working

```bash
# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik

# Verify IngressRoute was created
kubectl get ingressroute bar -n default -o yaml

# Check if backend service is reachable
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl -v http://bar-backend.default.svc.cluster.local:3000
```

### DNS Issues

```bash
# Test external DNS resolution
dig bar.ericsweiss.com

# Test internal DNS resolution
dig @192.168.0.150 router.esweiss.com

# Check external-dns logs
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns
```

### IP Allowlist Not Working

```bash
# Check middleware configuration
kubectl get middleware lan-tailscale-only -n traefik -o yaml

# Test from different source IPs
curl -v https://router.esweiss.com  # From LAN: should work
curl -v https://router.ericsweiss.com  # From internet: should 403
```

## Next Steps

After all services are verified:

1. **Deploy download clients and *arrs** as k8s pods:
   - qBittorrent on k3s-agt-nas-01
   - NZBGet on k3s-agt-nas-01
   - Radarr, Sonarr, Prowlarr on k3s cluster

2. **Add monitoring**:
   - Prometheus + Grafana stack
   - Monitor Traefik metrics
   - Alert on certificate expiration

3. **Add authentication**:
   - Deploy Authentik SSO
   - Add OAuth2 middleware to services
   - Centralized authentication

4. **Migrate remaining services**:
   - Nextcloud
   - Immich
   - GitLab
   - Home Assistant to k8s (currently external)
