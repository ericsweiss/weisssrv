# Authentik

SSO/OIDC identity provider for the homelab.

> **Ground rule**: applications, providers, groups and policy bindings are
> **Terraform-managed** in `terraform/authentik` and applied under supervision
> ([docs/40](../../../docs/40-authentik-terraform.md)). A change made in the
> Authentik UI is drift and gets reverted on the next apply. This README covers
> the Kubernetes deployment; docs/40 owns the object layer.

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
| Server | Web UI, API, embedded outpost | 2-4 (HPA) | Yes |
| Worker | Background tasks (email, sync) | 1 | Yes |
| PostgreSQL | Primary database (all state) | 1 | No |

## Prerequisites

### 1. Create 1Password Items

Create the following items in your 1Password "Homelab" vault:

The **Authentik Secrets** and **SMTP Relay Auth** items, with the exact fields
this app's ExternalSecret reads, are listed in
[docs/15-credential-rotation.md](../../../docs/15-credential-rotation.md)
§ Required 1Password Items — that table is canonical, and rotation procedures
live there too. `externalsecret.yaml` is the second source of truth: every
`remoteRef` in it must exist in the item, or the whole Secret fails to sync.

### 2. DNS Configuration

Both sides are codified. **Never edit AdGuard by hand** — the role reconciles
the rewrite list, so a hand-added entry is either reverted or silently diverges.

**Internal (AdGuard Home)** — `ansible/inventories/prod/group_vars/dns.yml`,
shipped by `task dns:deploy`:

- `auth.esweiss.com` → `192.168.0.101` (the **internal** Traefik VIP, like every
  other Traefik-fronted internal host).
- `auth.ericsweiss.com` → `192.168.0.101` as well. This is a deliberate,
  documented exception to the "no rewrite for the external domain" rule:
  server-side OIDC backends (hermes, immich, nextcloud, grafana) fetch
  discovery/JWKS from the **issuer host**, which must stay the external name for
  browsers — and over the WAN hairpin those non-browser fetches hit Cloudflare's
  UA-keyed bot filter with a 403. The rewrite moves only the
  fetch path inside; the reasoning lives next to the entry in `dns.yml`.

**External (Cloudflare)** — created by external-dns from the IngressRoute's
`external-dns.alpha.kubernetes.io/target` annotation
(`ingress-route.yaml`), which points `auth.ericsweiss.com` at the apex rather
than a literal VIP. Nothing to add by hand.

## Deployment

Authentik is Flux-managed. Everything in this folder is reconciled by the
top-level `apps` Kustomization on every push.

- **HelmRelease**: `release.yaml` -- chart `goauthentik/authentik`, release name `authentik`, values inlined (including image tags via `${authentik_version}` / `${postgresql_version}` substituted from the `cluster-versions` ConfigMap)
- **Secret**: `externalsecret.yaml` -- ExternalSecret `authentik-secrets` sourcing `secret-key`, `postgresql-password`, `postgresql-admin-password`, `smtp-username`, `smtp-password` from 1Password via ESO
- **Storage**: `storage.yaml` -- PV + PVC binding the ZFS zvol `ssd/appdata/authentik/postgres` on k3s-agt-nas-01
- **Ingress**: `ingress-route.yaml` + `middleware.yaml` + `certificate.yaml`
- **Backup**: `pg-dump.yaml` -- nightly `authentik-pg-dump` CronJob (see Backup and Restore)
- **Autoscaling**: `vpa.yaml` -- memory-only VPA for the server (the chart owns CPU via its HPA)
- **Network**: `networkpolicy.yaml` -- default-deny egress plus the scoped ingress/egress allows

Deploy workflow:

```bash
# Edit manifests
vim kubernetes/apps/authentik/release.yaml  # or any other file

# Commit + push; the GitLab agent's Flux Receiver triggers reconciliation
# on push (poll is the fallback)
git add kubernetes/apps/authentik/
git commit -m "..."
git push

# Force reconciliation manually if needed
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

3. **Store the akadmin password in 1Password** as a break-glass credential.
   Nothing in-cluster reads it: it is not an ExternalSecret field and does not
   appear in the docs/15 field list for `Authentik Secrets`.

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

For services that speak OIDC natively (Grafana, Mealie, Homarr, Nextcloud,
Immich):

1. Add the OAuth2 provider + application to `terraform/authentik` and apply it
   under supervision (docs/40) -- do **not** create them in the UI
2. Configure the service with:
   - Client ID: (from Authentik provider)
   - Client Secret: (from Authentik provider)
   - Authorization URL: `https://auth.ericsweiss.com/application/o/authorize/`
   - Token URL: `https://auth.ericsweiss.com/application/o/token/`
   - Userinfo URL: `https://auth.ericsweiss.com/application/o/userinfo/`

**Note**: Use the canonical external domain (auth.ericsweiss.com) for OAuth URLs.
This ensures callbacks work correctly for both internal and external clients.

### Method 3: LDAP Integration

For services that only support LDAP: declare the LDAP provider in
`terraform/authentik` (docs/40), then deploy an LDAP Outpost and point the
service at it.

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

The IngressRoutes reference per-host certificates issued by cert-manager in
the `authentik` namespace (see `certificate.yaml`):
- `authentik-ericsweiss-tls` (auth.ericsweiss.com)
- `authentik-esweiss-tls` (auth.esweiss.com)

If certificates are not found:
```bash
# Check certificate status (READY should be True)
kubectl get certificate -n authentik
kubectl describe certificate authentik-ericsweiss-tls -n authentik

# Check the issued secrets
kubectl get secret authentik-ericsweiss-tls authentik-esweiss-tls -n authentik
```

## Backup and Restore

### Backup

`pg-dump.yaml` runs the `authentik-pg-dump` CronJob nightly at 02:30 local time.
It writes a gzipped `--clean --if-exists` dump to the NFS export
`/backups-apps/authentik` (`tank/backups/apps/authentik`), keeps the newest 7,
and that landing zone rides both the nightly archive replication and the restic
B2 offsite walk ([docs/42](../../../docs/42-offsite-backup.md)). Staleness is
alerted by `AuthentikBackupStale`.

Ad-hoc dump, if one is needed outside the schedule:

```bash
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
| Authentik server | 2-4 (HPA, anti-affinity) | stateless |
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
