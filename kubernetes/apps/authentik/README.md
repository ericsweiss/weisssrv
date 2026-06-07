# Authentik

SSO/OIDC identity provider for the homelab.

## Overview

- **URL (External)**: https://auth.ericsweiss.com
- **URL (Internal)**: https://auth.esweiss.com
- **Initial Setup**: https://auth.esweiss.com/if/flow/initial-setup/
- **Admin Interface**: https://auth.esweiss.com/if/admin/
- **Chart**: [goauthentik/authentik](https://github.com/goauthentik/helm)

## Architecture

```
+----------+     +----------+     +-------------------+     +-----------------+
|  Traefik |---->| Authentik|---->|  Authentik Server |---->|   PostgreSQL    |
| Ingress  |     | Outpost  |     |  (Web UI + API)   |     |  (Helm subchart)|
+----------+     +----------+     +-------------------+     +-----------------+
                                             |                       ^
                                             |                       |
                                  +----------v----------+            |
                                  |  Authentik Worker   |------------+
                                  |  (Background tasks) |
                                  +---------------------+
```

Redis is not required as of Authentik 2025.10 - all state is in PostgreSQL.

## Components

| Component | Purpose | Replicas | Stateless? |
|-----------|---------|----------|------------|
| Server | Web UI, API, embedded outpost | 2 | Yes |
| Worker | Background tasks (email, sync) | 1 | Yes |
| PostgreSQL | Primary database (all state) | 1 | No |

## Prerequisites

### 1. Create 1Password Items

Create the following items in your 1Password "Homelab" vault:

**Item: "Authentik Secrets"**
| Field | Description | Generation |
|-------|-------------|------------|
| `secret-key` | JWT signing key (50+ chars) | `openssl rand -base64 50` |
| `postgresql-password` | PostgreSQL user password | `openssl rand -base64 32` |
| `postgresql-admin-password` | PostgreSQL admin password | `openssl rand -base64 32` |

Generate secrets:
```bash
# Generate all secrets at once
echo "secret-key: $(openssl rand -base64 50)"
echo "postgresql-password: $(openssl rand -base64 32)"
echo "postgresql-admin-password: $(openssl rand -base64 32)"
```

**SMTP Authentication (uses existing 1Password item)**

SMTP credentials are sourced from the existing "SMTP Relay Auth" item in 1Password.
No additional secrets need to be created - the deployment task automatically includes
`smtp-username` and `smtp-password` from this item in the authentik-secrets.

### 2. DNS Configuration

**Cloudflare (external-dns handles this automatically)**:
- `auth.ericsweiss.com` -> Traefik LB (192.168.0.100)

**AdGuard Home (manual configuration required)**:
Add DNS rewrite in AdGuard Home:
- `auth.esweiss.com` -> `192.168.0.100`

## Deployment

Authentik is Flux-managed. Everything in this folder is reconciled by the
top-level `apps` Kustomization on every push.

- **HelmRelease**: `release.yaml` -- chart `goauthentik/authentik`, release name `authentik`, values inlined (including image tags via `${authentik_version}` / `${postgresql_version}` substituted from the `cluster-versions` ConfigMap)
- **Secret**: `externalsecret.yaml` -- ExternalSecret `authentik-secrets` sourcing `secret-key`, `postgresql-password`, `postgresql-admin-password`, `smtp-username`, `smtp-password` from 1Password via ESO
- **Storage**: `storage.yaml` -- PV + PVC binding the ZFS zvol `ssd/appdata/authentik/postgres` on k3s-agt-nas-01
- **Ingress**: `ingress-route.yaml` + `middleware.yaml` + `certificate.yaml`

Deploy workflow:

```bash
# Edit manifests
vim kubernetes/apps/authentik/release.yaml  # or any other file

# Commit + push; Flux reconciles within ~1 minute
git add kubernetes/apps/authentik/
git commit -m "..."
git push

# Force reconciliation if you don't want to wait for the poll interval
task flux:reconcile
```

For bumping the Authentik or PostgreSQL image version:

```bash
# Edit ansible/inventories/prod/group_vars/all.yml (authentik_version / postgresql_version)
task flux:sync-versions
git add ansible/inventories/prod/group_vars/all.yml \
        kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump Authentik to <version>"
git push
```

Secret rotation (e.g., after changing the secret-key in 1Password):

```bash
task flux:rotate-secret -- authentik
```

See `docs/29-flux-operations.md` for the full Flux workflow.

## Initial Setup

1. **Wait for pods to be ready**:
   ```bash
   kubectl wait --namespace authentik \
     --for=condition=ready pod \
     --selector=app.kubernetes.io/name=authentik \
     --timeout=300s
   ```

2. **Access initial setup**:
   - Navigate to: https://auth.esweiss.com/if/flow/initial-setup/
   - Create the default `akadmin` user and set password

3. **Store admin password in 1Password**:
   Add to "Authentik Secrets" item:
   - `admin-password`: The password you set for akadmin

## Integrating SSO with Services

### Method 1: Traefik Forward Auth (Recommended)

Add the `authentik-auth` middleware to any IngressRoute:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-service
  namespace: default
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`myservice.esweiss.com`)
      kind: Rule
      middlewares:
        # Add Authentik authentication
        - name: authentik-auth
          namespace: authentik
        # Existing middlewares
        - name: hsts-header
          namespace: traefik
      services:
        - name: my-backend
          port: 8080
  tls:
    secretName: esweiss-com-tls
```

### Method 2: OAuth2/OIDC Native Integration

For services that support OAuth2/OIDC natively (Grafana, ArgoCD, etc.):

1. Create an OAuth2 Provider in Authentik admin
2. Create an Application linked to the provider
3. Configure the service with:
   - Client ID: (from Authentik provider)
   - Client Secret: (from Authentik provider)
   - Authorization URL: `https://auth.ericsweiss.com/application/o/authorize/`
   - Token URL: `https://auth.ericsweiss.com/application/o/token/`
   - Userinfo URL: `https://auth.ericsweiss.com/application/o/userinfo/`

**Note**: Use the canonical external domain (auth.ericsweiss.com) for OAuth URLs.
This ensures callbacks work correctly for both internal and external clients.

### Method 3: LDAP Integration

For services that only support LDAP:

1. Create an LDAP Provider in Authentik admin
2. Deploy an LDAP Outpost (separate deployment)
3. Configure service to use Authentik LDAP

## Verification

```bash
# Check all pods are running
kubectl get pods -n authentik

# Check services
kubectl get svc -n authentik

# Check IngressRoutes
kubectl get ingressroute -n authentik

# Check certificates are being served
curl -v https://auth.esweiss.com 2>&1 | grep "SSL certificate"

# Check middleware is available
kubectl get middleware -n authentik
```

## Troubleshooting

### Pods not starting

```bash
# Check pod events
kubectl describe pod -n authentik -l app.kubernetes.io/name=authentik

# Check logs
kubectl logs -n authentik -l app.kubernetes.io/name=authentik-server
kubectl logs -n authentik -l app.kubernetes.io/name=authentik-worker

# Check PostgreSQL
kubectl logs -n authentik -l app.kubernetes.io/name=postgresql
```

### Database connection issues

```bash
# Verify secret exists
kubectl get secret authentik-secrets -n authentik

# Check PostgreSQL is running
kubectl get pods -n authentik -l app.kubernetes.io/name=postgresql

# Test database connectivity
kubectl exec -it -n authentik deploy/authentik-server -- \
  python -c "from authentik.lib.utils.http import get_http_session; print('OK')"
```

### Forward auth not working

```bash
# Verify middleware exists
kubectl get middleware authentik-auth -n authentik -o yaml

# Check Traefik can reach Authentik
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl -v http://authentik-server.authentik.svc.cluster.local/outpost.goauthentik.io/auth/traefik
```

### Certificate issues

The IngressRoutes reference existing wildcard certificates:
- `ericsweiss-com-tls` in `default` namespace
- `esweiss-com-tls` in `default` namespace

If certificates are not found:
```bash
# Check certificates exist
kubectl get certificate -A
kubectl get secret ericsweiss-com-tls -n default
kubectl get secret esweiss-com-tls -n default

# Copy secrets to authentik namespace if needed (Traefik handles cross-namespace by default)
```

## Backup and Restore

### Backup

```bash
# Backup PostgreSQL
kubectl exec -n authentik authentik-postgresql-0 -- \
  pg_dump -U authentik authentik > authentik-backup-$(date +%Y%m%d).sql
```

### Restore

```bash
# Restore PostgreSQL
kubectl exec -i -n authentik authentik-postgresql-0 -- \
  psql -U authentik authentik < authentik-backup-YYYYMMDD.sql
```

## Security Notes

- Admin password is stored in 1Password (`Authentik Secrets`); never log or echo `secret_key`.
- MFA is enforced on admin flows in the Authentik UI (Stages → MFA validation).
- NetworkPolicies in `networkpolicy.yaml` default-deny ingress + scope egress to required destinations.

## Topology

| Component | Replicas | Storage |
|---|---|---|
| Authentik server | 2 (anti-affinity) | stateless |
| Authentik worker | 1 | stateless |
| Bundled PostgreSQL | 1 | hostPath PV on `k3s-agt-nas-01` → ZFS zvol `ssd/appdata/authentik/postgres` (10 GB, ext4) |

Server + worker survive pod restarts and node failures (rescheduled by k8s).
PostgreSQL is the single point of failure: if `k3s-agt-nas-01` is unreachable,
all SSO is offline until the node recovers. Acceptable for a homelab; HA
upgrade options (CloudNativePG, external Postgres on NAS LXC) are tracked
in `docs/16-next-steps.md`.

## Upgrading

1. Backup the database first (see `task k3s:backup` for cluster etcd; for
   the Authentik postgres zvol, snapshot the ZFS dataset on pve-nas-01
   before bumping versions).
2. Discover the latest version:
   ```bash
   task maintenance:check-versions | grep -i authentik
   ```
   Or query the chart directly:
   ```bash
   helm search repo authentik/authentik --versions | head -5
   ```
3. Update `ansible/inventories/prod/group_vars/all.yml`:
   - `authentik_version` -- this single variable pins **both** the chart version and the server/worker image tags (Authentik chart appVersion matches the image tag by convention)
4. Regenerate the Flux ConfigMap and commit:
   ```bash
   task flux:sync-versions
   git add ansible/inventories/prod/group_vars/all.yml \
           kubernetes/infrastructure/sources/versions-configmap.yaml
   git commit -m "Bump Authentik to <version>"
   git push
   ```
5. Flux reconciles the HelmRelease; watch with `flux get hr authentik -n authentik` and `task authentik:status`.
6. Rollback (if needed): `git revert <commit>; git push` -- Flux reconciles the reverted manifest. Alternatively `flux suspend hr authentik -n authentik` to freeze the current state while you investigate.

## References

- [Authentik Docs](https://docs.goauthentik.io/)
- [Helm Chart](https://github.com/goauthentik/helm)
- [Traefik Integration](https://docs.goauthentik.io/integrations/services/traefik/)
- [HA Guide](https://docs.goauthentik.io/install-config/high-availability/)
- [CloudNativePG](https://cloudnative-pg.io/)
