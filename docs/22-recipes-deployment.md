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

| Component | Node | Reason |
|-----------|------|--------|
| Mealie | k3s-agt-opt-03 | General workload, prefer non-NAS |
| Mealie PostgreSQL | k3s-agt-nas-01 | Pinned for local storage performance |
| Bar Assistant | k3s-agt-nas-01 | Pinned for NFS data locality (SQLite) |
| Redis | k3s-agt-nas-01 | Pinned alongside Bar Assistant |
| Meilisearch | k3s-agt-nas-01 | Pinned alongside Bar Assistant |
| Salt Rim | k3s-agt-opt-03 | General workload, prefer non-NAS |

## Prerequisites

### 1. NFS Storage Preparation

The NFS exports should already be configured. Create the appdata directories:

```bash
# Create directories for Mealie (app data only - PostgreSQL uses a dedicated ZFS zvol)
ssh pve-nas-01 "sudo mkdir -p /mnt/ssd/appdata/mealie"
ssh pve-nas-01 "sudo chown -R 1000:2000 /mnt/ssd/appdata/mealie"
ssh pve-nas-01 "sudo chmod -R 2775 /mnt/ssd/appdata/mealie"

# Create directories for Bar Assistant (includes SQLite database and Meilisearch data)
ssh pve-nas-01 "sudo mkdir -p /mnt/ssd/appdata/bar-assistant/{meilisearch,bar-assistant}"
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
| **SMTP Relay Auth** | `username` | SMTP authentication for email notifications |
| **SMTP Relay Auth** | `password` | SMTP authentication for email notifications |
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
| food.ericsweiss.com | External | Cloudflare (external-dns) | .100 (public) | `ericsweiss-com-tls` |
| food.esweiss.com | Internal | AdGuard Home | .101 (internal) | `esweiss-com-tls` |
| bar.ericsweiss.com | External | Cloudflare (external-dns) | .100 (public) | `ericsweiss-com-tls` |
| bar.esweiss.com | Internal | AdGuard Home | .101 (internal) | `esweiss-com-tls` |

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

### Quick Deploy

```bash
# Deploy the full stack
task recipes:deploy
```

### Step-by-Step Deploy

> **WARNING**: The manifest files (`mealie.yaml`, `bar-assistant.yaml`) contain `${VERSION}` placeholders that require `envsubst` processing. Do NOT apply them directly with `kubectl apply -f` - the images will have literal `${...}` in their tags and fail to pull.

The `task recipes:deploy` command is **strongly recommended** as it handles version substitution, secret management, and 1Password authentication automatically. For manual deployment:

```bash
# 1. Create namespace and storage
kubectl apply -f kubernetes/apps/recipes/namespace.yaml
kubectl apply -f kubernetes/apps/recipes/storage.yaml

# 2. Create secrets from 1Password (SSO secrets are REQUIRED - password login is disabled)
kubectl create secret generic mealie-secrets \
  --namespace=recipes \
  --from-literal=postgres-password="$(op read 'op://Homelab/Mealie Secrets/postgres-password')" \
  --from-literal=smtp-username="$(op read 'op://Homelab/SMTP Relay Auth/username')" \
  --from-literal=smtp-password="$(op read 'op://Homelab/SMTP Relay Auth/password')" \
  --from-literal=oidc-client-id="$(op read 'op://Homelab/Mealie SSO/oidc-client-id')" \
  --from-literal=oidc-client-secret="$(op read 'op://Homelab/Mealie SSO/oidc-client-secret')" \
  --from-literal=openai-api-key="$(op read 'op://Homelab/OpenAI API Key/api-key' 2>/dev/null || echo '')" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic bar-assistant-secrets \
  --namespace=recipes \
  --from-literal=meilisearch-master-key="$(op read 'op://Homelab/Bar Assistant Secrets/meilisearch-master-key')" \
  --from-literal=smtp-username="$(op read 'op://Homelab/SMTP Relay Auth/username')" \
  --from-literal=smtp-password="$(op read 'op://Homelab/SMTP Relay Auth/password')" \
  --from-literal=authentik-client-id="$(op read 'op://Homelab/Bar Assistant SSO/authentik-client-id')" \
  --from-literal=authentik-client-secret="$(op read 'op://Homelab/Bar Assistant SSO/authentik-client-secret')" \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Deploy Mealie (with version substitution - manifests contain ${VERSION} placeholders)
VARS_FILE="ansible/inventories/prod/group_vars/all.yml"
export MEALIE_VERSION=$(grep '^mealie_version:' "$VARS_FILE" | awk '{print $2}' | tr -d '"')
export BAR_ASSISTANT_VERSION=$(grep '^bar_assistant_version:' "$VARS_FILE" | awk '{print $2}' | tr -d '"')
export SALT_RIM_VERSION=$(grep '^salt_rim_version:' "$VARS_FILE" | awk '{print $2}' | tr -d '"')

ENVSUBST_VARS='$MEALIE_VERSION $BAR_ASSISTANT_VERSION $SALT_RIM_VERSION'
envsubst "$ENVSUBST_VARS" < kubernetes/apps/recipes/mealie.yaml | kubectl apply -f -

# 4. Deploy Bar Assistant (with version substitution)
envsubst "$ENVSUBST_VARS" < kubernetes/apps/recipes/bar-assistant.yaml | kubectl apply -f -

# 5. Deploy ingress routes (no substitution needed)
kubectl apply -f kubernetes/apps/recipes/ingress-routes.yaml
```

**Important**: The `mealie.yaml` and `bar-assistant.yaml` files contain `${VERSION}` placeholders that must be substituted using `envsubst`. Applying them directly with `kubectl apply -f` will result in invalid image references.

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
mealie_version: "v2.0.0"  # or "latest"
bar_assistant_version: "v4.0.0"  # or "latest"
salt_rim_version: "v3.0.0"  # or "latest"
```

Then redeploy:

```bash
task recipes:deploy
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

# Verify certificate
kubectl get certificate -n cert-manager
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
- [Storage Configuration](./07-fileservices.md)
- [DNS Configuration](./08-dns.md)
- [Recipes SSO Setup](./23-recipes-sso-setup.md)
