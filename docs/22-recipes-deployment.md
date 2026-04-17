# Recipe Management Stack Deployment

This guide covers deploying the recipe management stack including Mealie (food recipes and meal planning) and Bar Assistant (cocktail/bar recipes).

## Overview

### Components

| Component | Purpose | Port | URL |
|-----------|---------|------|-----|
| **Mealie** | Recipe management, meal planning | 9000 | food.esweiss.com / food.ericsweiss.com |
| **Bar Assistant** | Cocktail recipe management | 8080 | bar.esweiss.com / bar.ericsweiss.com |
| **Salt Rim** | Bar Assistant web UI | 8080 | (served at bar.esweiss.com) |
| **Mealie PostgreSQL** | Mealie database | 5432 | Internal only |
| **Redis** | Bar Assistant cache and sessions | 6379 | Internal only |
| **Meilisearch** | Bar Assistant search engine | 7700 | Internal only |

### Architecture

```
                        Internet
                            |
                      Cloudflare
                            |
                    +-------+-------+
                    |               |
            food.ericsweiss.com  bar.ericsweiss.com
                    |               |
                    v               v
            +-------+---------------+-------+
            |         Traefik (.100)        |
            |   (public LoadBalancer VIP)   |
            +-------+---------------+-------+
                    |               |
        +-----------+    +----------+-----------+
        |                |          |           |
        v                v          v           v
    +-------+       +--------+  +-------+  +-----------+
    | Mealie|       | Salt   |  | Bar   |  | Meilisearch|
    | :9000 |       | Rim    |  | Asst  |  | :7700     |
    +---+---+       | :8080  |  | :8080 |  +-----+-----+
        |           +--------+  +---+---+        |
        |                           |            |
        v                           |      +-----+
    +----------+              +-----+      |
    | Mealie   |              | Redis|<----+
    | Postgres |              | :6379|
    | :5432    |              +------+
    +----------+                |
         \                     /
          \  (pinned to NAS)  /
           \                 /
    +-------+----------------+------+
    |      k3s-agt-nas-01 (NFS)    |
    |   /appdata/mealie             |
    |   /appdata/bar-assistant      |
    +-------------------------------+
```

### Node Placement

| Component | Placement Strategy | Reason |
|-----------|-------------------|--------|
| Mealie | `nodeSelector: esweiss.com/general` + prefer non-NAS | General workload, spreads across cluster |
| Mealie PostgreSQL | Required hostname affinity: `k3s-agt-nas-01` | Pinned to NAS node for ZFS zvol storage |
| Bar Assistant | Required hostname affinity: `k3s-agt-nas-01` | Pinned for NFS data locality (SQLite) |
| Redis | Required hostname affinity: `k3s-agt-nas-01` | Pinned alongside Bar Assistant |
| Meilisearch | Required hostname affinity: `k3s-agt-nas-01` | Pinned alongside Bar Assistant |
| Salt Rim | `nodeSelector: esweiss.com/general` + prefer non-NAS | General workload, spreads across cluster |

**Node placement details**:
- **Mealie and Salt Rim** use `nodeSelector: esweiss.com/general: "true"` with `preferredDuringSchedulingIgnoredDuringExecution` affinity to prefer non-NAS nodes, allowing them to run anywhere if needed.
- **Database and storage-dependent components** (Mealie PostgreSQL, Bar Assistant, Redis, Meilisearch) use `requiredDuringSchedulingIgnoredDuringExecution` with `kubernetes.io/hostname: k3s-agt-nas-01` to ensure they run on the NAS agent node where their persistent storage resides.

## Prerequisites

### 1. NFS Storage Preparation

The NFS exports should already be configured. Create the appdata directories:

```bash
# Create directories for Mealie (app data only - PostgreSQL uses a dedicated ZFS zvol)
ssh pve-nas-01 "sudo mkdir -p /mnt/ssd/appdata/mealie"
ssh pve-nas-01 "sudo chown -R 1000:2000 /mnt/ssd/appdata/mealie"
ssh pve-nas-01 "sudo chmod -R 2775 /mnt/ssd/appdata/mealie"

# Create directories for Bar Assistant (Meilisearch uses subPath within the NFS mount)
ssh pve-nas-01 "sudo mkdir -p /mnt/ssd/appdata/bar-assistant/meilisearch"
ssh pve-nas-01 "sudo chown -R 1000:2000 /mnt/ssd/appdata/bar-assistant"
ssh pve-nas-01 "sudo chmod -R 2775 /mnt/ssd/appdata/bar-assistant"
```

**Note**: Mealie PostgreSQL uses a dedicated ZFS zvol (`ssd/appdata/mealie/postgres`) attached to k3s-agt-nas-01 as a SCSI disk and mounted at `/mnt/mealie-postgres-data`. This provides persistent storage that survives VM recreation. The zvol is managed by the `proxmox_vm` role via `vm_additional_disks` in `hosts.yml` and exposed to Kubernetes as a hostPath PV.

### 2. 1Password Secrets (Required)

Create the following items in your **Homelab** vault before deploying. The deployment task reads secrets from 1Password on every run, enabling credential rotation by simply updating 1Password and redeploying.

**IMPORTANT**: All secrets must exist in 1Password before deployment. The task will NOT generate random passwords - it will fail with a clear error if required items are missing. This ensures idempotent deployments (re-running won't break your database with new passwords).

**Required Items (deployment fails without these):**

| 1Password Item | Field | Purpose |
|----------------|-------|---------|
| **Mealie Secrets** | `postgres-password` | Mealie PostgreSQL database password |
| **Bar Assistant Secrets** | `meilisearch-master-key` | Meilisearch search engine API key |
| **Mealie SSO** | `oidc-client-id` | Authentik OAuth2 client ID for Mealie |
| **Mealie SSO** | `oidc-client-secret` | Authentik OAuth2 client secret for Mealie |
| **Bar Assistant SSO** | `authentik-client-id` | Authentik OAuth2 client ID for Bar Assistant |
| **Bar Assistant SSO** | `authentik-client-secret` | Authentik OAuth2 client secret for Bar Assistant |

> **IMPORTANT**: SSO secrets are REQUIRED, not optional. Password-based login is disabled in both applications - Authentik SSO is the only way to log in. You must configure Authentik providers BEFORE deploying. See [SSO Setup Guide](./23-recipes-sso-setup.md) for complete instructions.

**Create the items:**

```bash
# Generate secure passwords for new installations
echo "Mealie Postgres password: $(openssl rand -base64 32)"
echo "Meilisearch master key: $(openssl rand -base64 32)"
```

Then create in 1Password:
- **Mealie Secrets** (type: Password) with field `postgres-password`
- **Bar Assistant Secrets** (type: Password) with field `meilisearch-master-key`
- **Mealie SSO** (type: Password) with fields `oidc-client-id`, `oidc-client-secret` (from Authentik)
- **Bar Assistant SSO** (type: Password) with fields `authentik-client-id`, `authentik-client-secret` (from Authentik)

**Optional Items:**

| 1Password Item | Field | Purpose |
|----------------|-------|---------|
| **OpenAI API Key** | `api-key` | OpenAI API key for Mealie recipe parsing from images |

The deployment proceeds with a note if the OpenAI API key is missing - this feature is optional.

### 3. DNS Configuration

Both apps are accessible via internal and external domains with proper TLS certificates for each:

| Domain | Type | DNS Provider | Traefik VIP | Certificate |
|--------|------|--------------|-------------|-------------|
| food.ericsweiss.com | External | Cloudflare (external-dns) | .100 (public) | `recipes-ericsweiss-tls` |
| food.esweiss.com | Internal | AdGuard Home | .101 (internal) | `recipes-esweiss-tls` |
| bar.ericsweiss.com | External | Cloudflare (external-dns) | .100 (public) | `recipes-ericsweiss-tls` |
| bar.esweiss.com | Internal | AdGuard Home | .101 (internal) | `recipes-esweiss-tls` |

**Note**: Per-namespace certificates are created by cert-manager via `certificate.yaml` in each namespace. This ensures certificate secrets exist in the same namespace as the IngressRoutes that reference them.

**Add DNS rewrites in AdGuard Home:**

```yaml
# Internal (esweiss.com) -> internal Traefik VIP
- domain: food.esweiss.com
  answer: 192.168.0.101
- domain: bar.esweiss.com
  answer: 192.168.0.101
```

External DNS (ericsweiss.com) is managed automatically by external-dns via Cloudflare.

## Deployment

The recipes stack is Flux-managed. All files live under `kubernetes/apps/recipes/` and are reconciled on commit + push. Version placeholders (`${mealie_version}`, `${bar_assistant_version}`, etc.) are substituted by Flux from the `cluster-versions` ConfigMap — no `envsubst` is required.

### Layout

```
kubernetes/apps/recipes/
├── namespace.yaml
├── externalsecret.yaml     # Single ExternalSecret consolidating all recipe keys (Mealie PG, SSO, OpenAI, Bar Assistant meilisearch + SSO). SMTP creds not included — LAN relay accepts unauthenticated submissions from pod CIDRs.
├── storage.yaml            # PVCs + PVs (NFS for appdata, hostPath PV for Mealie PG zvol)
├── mealie.yaml             # Deployment + Service (Mealie, Mealie Postgres)
├── bar-assistant.yaml      # Deployment + Service (Bar Assistant, Redis, Meilisearch, Salt Rim)
├── certificate.yaml        # cert-manager Certificates (recipes-esweiss-tls, recipes-ericsweiss-tls)
├── ingress-routes.yaml     # Traefik IngressRoutes for food/bar domains
└── kustomization.yaml
```

### Deploying Changes

1. Edit the appropriate YAML under `kubernetes/apps/recipes/`.
2. Commit and push.
3. Flux reconciles within ~1 minute.

For image-version bumps:

```bash
task maintenance:update-version SERVICE=mealie
task flux:sync-versions
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump mealie" && git push
task flux:reconcile  # optional: skip the 1-min poll
```

### Secrets (ExternalSecret)

All recipe secrets are provided by one consolidated ExternalSecret. It references
1Password item IDs (not titles — spaces break the SDK parser) with the format
`<item-id>/<field>`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: recipes-secrets
  namespace: recipes
spec:
  refreshInterval: 24h
  secretStoreRef:
    name: onepassword-homelab
    kind: ClusterSecretStore
  target:
    name: recipes-secrets
    creationPolicy: Owner
  data:
    - secretKey: mealie-postgres-password
      remoteRef: { key: <MEALIE-SECRETS-ITEM-ID>/postgres-password }
    - secretKey: mealie-oidc-client-id
      remoteRef: { key: <MEALIE-SSO-ITEM-ID>/oidc-client-id }
    - secretKey: mealie-oidc-client-secret
      remoteRef: { key: <MEALIE-SSO-ITEM-ID>/oidc-client-secret }
    - secretKey: mealie-openai-api-key
      remoteRef: { key: <OPENAI-ITEM-ID>/api-key }
    - secretKey: bar-assistant-meilisearch-master-key
      remoteRef: { key: <BAR-ASSISTANT-SECRETS-ITEM-ID>/meilisearch-master-key }
    - secretKey: bar-assistant-authentik-client-id
      remoteRef: { key: <BAR-ASSISTANT-SSO-ITEM-ID>/authentik-client-id }
    - secretKey: bar-assistant-authentik-client-secret
      remoteRef: { key: <BAR-ASSISTANT-SSO-ITEM-ID>/authentik-client-secret }
```

(SMTP credentials are NOT in this ExternalSecret — the LAN SMTP relay
accepts unauthenticated submissions from k3s pod CIDRs via Postfix's
`permit_mynetworks`, so Mealie and Bar Assistant don't need SMTP creds.
See `kubernetes/apps/recipes/{mealie,bar-assistant}.yaml` for the
relevant env vars.)

All 1P items referenced here must exist *before* the ExternalSecret reconciles
successfully. If any are missing, the corresponding `data` entry fails and the
Secret will not be created. See `docs/23-recipes-sso-setup.md` for how to create
the SSO items from Authentik. See `docs/29-flux-operations.md` for how to look up
item IDs and the `<item-id>/<field>` convention.

To rotate any secret: change the value in 1Password, then either wait 24h or:

```bash
task flux:rotate-secret -- recipes
# (forces ExternalSecret refresh and restarts Mealie + Bar Assistant Deployments)
```

### Verify Deployment

```bash
# Check all pods
task recipes:status

# Verify services are accessible
curl -k https://food.esweiss.com/api/app/about
curl -k https://bar.esweiss.com/api/server/version
```

## Application Configuration

### Mealie Initial Setup

1. Access: https://food.esweiss.com
2. Create your first admin user
3. Configure settings:
   - General: Set site name, timezone
   - Email: Already configured via environment variables
   - Groups: Create user groups if needed

### Bar Assistant Initial Setup

1. Access: https://bar.esweiss.com
2. Create your first admin user
3. Import default data:
   - Go to Settings > Data
   - Import IBA cocktails or other defaults
4. Configure:
   - Units and measurements
   - Default ingredients

### Restoring from Backup

Both applications support backup/restore. If you have backups from previous instances:

**Mealie:**
1. Access https://food.esweiss.com/admin/backups
2. Upload your backup file
3. Restore the backup

**Bar Assistant:**
1. Export data via Settings > Data in the web UI
2. To restore, copy backup files to the data volume or use the API import

## Maintenance

### View Logs

```bash
# Mealie logs
task recipes:logs APP=mealie

# Mealie PostgreSQL logs
task recipes:logs APP=mealie-postgres

# Bar Assistant logs
task recipes:logs APP=bar-assistant

# Meilisearch logs
task recipes:logs APP=bar-assistant-meilisearch

# Salt Rim (frontend) logs
task recipes:logs APP=salt-rim
```

### Shell Access

```bash
# Access Mealie shell
task recipes:shell APP=mealie

# Access Bar Assistant shell
task recipes:shell APP=bar-assistant

# Access Mealie PostgreSQL
kubectl exec -it -n recipes deployment/mealie-postgres -- psql -U mealie
```

### Restart Apps

```bash
# Restart everything
task recipes:restart

# Restart specific app
kubectl rollout restart deployment/mealie -n recipes
kubectl rollout restart deployment/bar-assistant -n recipes
```

### Update Apps

Update versions in `ansible/inventories/prod/group_vars/all.yml`:

```yaml
# Current pinned versions (check for latest at maintenance:check-versions)
mealie_version: "v3.12.0"
mealie_postgresql_version: "18.3-alpine"  # PostgreSQL for Mealie database
bar_assistant_version: "5.13.2"
salt_rim_version: "4.14.1"
```

Note: We pin to specific versions (not "latest") for reproducible deployments. Use `task maintenance:check-versions` to discover available updates. The PostgreSQL version should generally stay on stable major versions (17.x, 18.x) unless Mealie requires an upgrade.

Then regenerate the ConfigMap and push — Flux rolls the Deployments:

```bash
task flux:sync-versions
git add ansible/inventories/prod/group_vars/all.yml kubernetes/infrastructure/sources/versions-configmap.yaml
git commit -m "Bump mealie and bar-assistant"
git push
```

### Backup

App data is stored on NFS (`/mnt/ssd/appdata`). Include in your regular NAS backup strategy.

**NFS-backed data (automatically included in NAS backups):**
- `/mnt/ssd/appdata/mealie/` - Mealie data and uploads
- `/mnt/ssd/appdata/bar-assistant/` - Bar Assistant data and SQLite database
- `/mnt/ssd/appdata/bar-assistant/meilisearch/` - Meilisearch index data

**ZFS zvol storage (backed by SSD pool):**
- Mealie PostgreSQL data is stored on a ZFS zvol (`ssd/appdata/mealie/postgres`) attached to k3s-agt-nas-01
- Mounted at `/mnt/mealie-postgres-data` inside the VM, exposed as a hostPath PV
- The zvol inherits ZFS pool-level snapshots and replication capabilities
- **Recommended**: Use Mealie's built-in backup feature (Admin > Backups) which exports the full database

For application-level backups:
- **Mealie**: Use the built-in backup feature in Admin > Backups
- **Bar Assistant**: Export data via Settings > Data

## Troubleshooting

### Mealie Can't Connect to Database

```bash
# Check PostgreSQL pod is running
kubectl get pods -n recipes -l app.kubernetes.io/component=database

# Check PostgreSQL logs
kubectl logs -n recipes -l app.kubernetes.io/name=mealie-postgres

# Verify secrets exist
kubectl get secrets -n recipes
```

### NFS Mount Issues

```bash
# Check PVC status
kubectl get pvc -n recipes

# Check NFS connectivity from pod
kubectl exec -n recipes deployment/mealie -- df -h

# Verify NFS export on NAS
ssh pve-nas-01 "exportfs -v"
```

### IngressRoute Not Working

```bash
# Check IngressRoute exists
kubectl get ingressroute -n recipes

# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik

# Verify certificates exist in the recipes namespace
kubectl get certificate -n recipes

# Check certificate status and secrets
kubectl describe certificate -n recipes
kubectl get secrets -n recipes | grep tls
```

### Bar Assistant Search Not Working

```bash
# Check Meilisearch is running
kubectl get pods -n recipes -l app.kubernetes.io/name=bar-assistant-meilisearch

# Check Meilisearch health
kubectl exec -n recipes deployment/bar-assistant-meilisearch -- wget -qO- http://localhost:7700/health

# Rebuild search index
# Access Bar Assistant > Settings > Server > Rebuild search index
```

## Future Enhancements

### High Availability

For production HA:
1. Deploy PostgreSQL with replication (consider CloudNativePG operator)
2. Scale app replicas to 2+
3. Consider external PostgreSQL service

## Related Documentation

- [K3s Deployment Guide](./19-k3s-deployment.md)
- [Flux Operations](./29-flux-operations.md)
- [Storage Configuration](./07-fileservices.md)
- [DNS Configuration](./08-dns.md)
- [Recipes SSO Setup](./23-recipes-sso-setup.md)
- Manifests: `kubernetes/apps/recipes/`
