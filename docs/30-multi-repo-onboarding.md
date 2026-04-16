# Multi-Repo Onboarding Guide

How to attach an external Git repository to the weisssrv k3s cluster so Flux reconciles its workloads into a dedicated namespace. Covers two backends for secret management — 1Password (my own repos) and GitLab CI/CD variables (friends without 1Password) — plus namespace isolation rules, rate-limit considerations, and removal.

## Table of Contents

1. [Overview](#overview)
2. [Path A — 1Password Backend](#path-a--1password-backend)
3. [Path B — GitLab CI/CD Variables Backend](#path-b--gitlab-cicd-variables-backend)
4. [Namespace Isolation](#namespace-isolation)
5. [Rate Limits (1Password Families Plan)](#rate-limits-1password-families-plan)
6. [Removal](#removal)
7. [Future: weisssrv-project-template Repo](#future-weisssrv-project-template-repo)
8. [Worked Example: Onboarding `example-app`](#worked-example-onboarding-example-app)

---

## Overview

Each external repo that deploys to this cluster gets **one wiring file** in `kubernetes/clusters/weisssrv/tenants/<repo-slug>.yaml`. That file defines three things:

1. **Secret backend** — a `ClusterSecretStore` (1Password) or `SecretStore` (GitLab variables) scoped to the tenant.
2. **Git source** — a Flux `GitRepository` pointing at the tenant's repo and branch.
3. **Top-level Kustomization** — reconciles the tenant's `kubernetes/flux/` path into a dedicated namespace.

The tenant's repo contains its own `kubernetes/flux/` tree with workloads, ExternalSecrets (referencing their own store), etc. The wiring file stays in this repo.

See `kubernetes/clusters/weisssrv/tenants/README.md` for the canonical templates.

### Choosing a Backend

| Backend | Use when | Tradeoffs |
|---|---|---|
| 1Password | You have a 1P Families account and can scope a service account to a tenant vault | Costs 1P read budget; strong isolation via per-tenant vaults |
| GitLab variables | Friend without 1P; tenant already has a GitLab project | Secrets are visible to anyone with project Maintainer access; no 1P budget impact |

Prefer GitLab variables for friends — see [Rate Limits](#rate-limits-1password-families-plan).

---

## Path A — 1Password Backend

For repos where secrets live in a dedicated 1Password vault and ESO reads them via the 1Password SDK provider.

### 1. Create the Tenant Vault in 1Password

In the 1Password web UI or CLI, create a new vault named after the tenant — convention: `Homelab-<repo-slug>`, e.g. `Homelab-Example`. This vault will hold every secret the tenant workloads need.

Move or create the tenant's secrets inside this vault. Do not share items with the main `Homelab` vault — the per-tenant vault is the isolation boundary.

### 2. Create a Scoped Service Account

In 1Password → Integrations → Service Accounts → New Service Account:

- Name: `weisssrv-<repo-slug>`.
- Vault access: **only** the tenant vault (`Homelab-<repo-slug>`), read-only.
- Save the token to the main `Homelab` 1P vault as an item titled e.g. `Service Account Auth Token weisssrv <repo-slug>` with field `credential`.

### 3. Seed the Bootstrap Secret in the Tenant Namespace

The ESO ClusterSecretStore references a k8s Secret containing the SDK token. That Secret is manually created, one time, because ESO can't fetch its own auth token with itself.

```bash
# Replace <repo-slug> with the tenant's slug and <sa-item> with the 1P item name
kubectl create namespace <repo-slug>

# NOTE: `--from-literal=token=-` sets the secret's token to the literal
# string "-" (NOT stdin). Read into a variable first, then interpolate.
TOKEN="$(op read "op://Homelab/Service Account Auth Token weisssrv <repo-slug>/credential")"
kubectl -n <repo-slug> create secret generic onepassword-sdk-token \
  --from-literal=token="$TOKEN" \
  --dry-run=client -o yaml \
  | kubectl apply -f -
unset TOKEN
```

The secret lives in the tenant namespace (not `external-secrets`) so a `ClusterSecretStore` scoped to this tenant can reference it without reaching into a shared namespace.

### 4. Add the Wiring File

Create `kubernetes/clusters/weisssrv/tenants/<repo-slug>.yaml`. Copy-adapt the 1Password template from `kubernetes/clusters/weisssrv/tenants/README.md`:

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: <repo-slug>
  labels:
    app.kubernetes.io/managed-by: flux
    fluxcd.io/tenant: <repo-slug>
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: onepassword-<repo-slug>
spec:
  provider:
    onepasswordSDK:
      vault: Homelab-<repo-slug>
      auth:
        serviceAccountSecretRef:
          name: onepassword-sdk-token
          namespace: <repo-slug>
          key: token
---
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: <repo-slug>
  namespace: flux-system
spec:
  interval: 1m
  url: https://git.ericsweiss.com/eric/<repo-slug>
  ref:
    branch: main
  # For private repos, attach a deploy token:
  # secretRef:
  #   name: <repo-slug>-git-creds
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <repo-slug>
  namespace: flux-system
spec:
  interval: 10m
  retryInterval: 1m
  timeout: 10m
  sourceRef:
    kind: GitRepository
    name: <repo-slug>
  path: ./kubernetes/flux
  prune: true
  targetNamespace: <repo-slug>
  wait: true
```

Then register the file in the tenants Kustomize aggregate
(`kubernetes/clusters/weisssrv/tenants/kustomization.yaml`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - <repo-slug>.yaml   # <-- add this line
```

Commit + push both files. Flux picks up the new tenant on the next
reconcile.

### 5. Build Out the Tenant Repo

In the tenant's own repo, create `kubernetes/flux/` with:

- Workload manifests (Deployments, HelmReleases, etc.).
- `ExternalSecret` CRs that reference `ClusterSecretStore/onepassword-<repo-slug>`.
- A top-level `kustomization.yaml` aggregating everything under `kubernetes/flux/`.

ExternalSecret example for a tenant workload:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: app-secrets
  namespace: <repo-slug>
spec:
  refreshInterval: 24h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-<repo-slug>
  target:
    name: app-secrets
    creationPolicy: Owner
  data:
    - secretKey: api-key
      remoteRef:
        key: <1P-ITEM-ID>/credential
```

Use 1Password item IDs (not titles) in `remoteRef.key` — same format as this repo's ExternalSecrets. See `docs/29-flux-operations.md` for the format rules.

### 6. Verify

```bash
# Wiring resources in the main cluster
kubectl get kustomization,gitrepository -n flux-system | grep <repo-slug>
kubectl get clustersecretstore onepassword-<repo-slug>

# Tenant workloads
kubectl get all -n <repo-slug>
kubectl get externalsecret -n <repo-slug>
```

---

## Path B — GitLab CI/CD Variables Backend

For friends who have a GitLab project and don't want to deal with 1Password. ESO reads from the project's CI/CD variables via the GitLab provider.

### 1. Create a Project Access Token

In GitLab → the friend's project → Settings → Access Tokens:

- Name: `weisssrv-eso`.
- Role: Reporter (minimum needed to read variables).
- Scopes: `read_api`.
- Save the token — it's shown once.

### 2. Seed the Bootstrap Secret

```bash
kubectl create namespace <repo-slug>

kubectl -n <repo-slug> create secret generic gitlab-api-token \
  --from-literal=token=glpat-<TOKEN>
```

### 3. Add the Wiring File

```yaml
# kubernetes/clusters/weisssrv/tenants/<repo-slug>.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: <repo-slug>
  labels:
    app.kubernetes.io/managed-by: flux
    fluxcd.io/tenant: <repo-slug>
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: gitlab-<repo-slug>
spec:
  provider:
    gitlab:
      url: https://git.ericsweiss.com
      projectID: "<NUMERIC_PROJECT_ID>"
      auth:
        SecretRef:
          accessToken:
            name: gitlab-api-token
            namespace: <repo-slug>
            key: token
      environment: "*"
---
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: <repo-slug>
  namespace: flux-system
spec:
  interval: 1m
  url: https://git.ericsweiss.com/<group>/<repo-slug>
  ref:
    branch: main
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <repo-slug>
  namespace: flux-system
spec:
  interval: 10m
  retryInterval: 1m
  timeout: 10m
  sourceRef:
    kind: GitRepository
    name: <repo-slug>
  path: ./kubernetes/flux
  prune: true
  targetNamespace: <repo-slug>
  wait: true
```

The numeric project ID is under GitLab → Project → Settings → General (visible below the project name).

### 4. Friend Adds Variables

In their GitLab project → Settings → CI/CD → Variables, they add entries like:

| Key | Value | Protected | Masked |
|---|---|---|---|
| `APP_API_KEY` | `...` | No | Yes |
| `DB_PASSWORD` | `...` | No | Yes |

Each key becomes a `remoteRef.key` in their ExternalSecrets:

```yaml
data:
  - secretKey: api-key
    remoteRef:
      key: APP_API_KEY
  - secretKey: db-password
    remoteRef:
      key: DB_PASSWORD
```

The `secretStoreRef` points at the store defined in the wiring file:

```yaml
secretStoreRef:
  kind: ClusterSecretStore
  name: gitlab-<repo-slug>
```

### 5. Verify

Same commands as Path A, step 6.

---

## Namespace Isolation

Every tenant owns **exactly one namespace**. Non-negotiable.

Tenants **must not** create or modify resources in:

- Platform namespaces: `flux-system`, `external-secrets`, `metallb-system`, `cert-manager`, `traefik`, `external-dns`, `authentik`.
- Other tenants' namespaces.

Why: Flux's server-side apply with `prune: true` will fight any resources that appear in a namespace that isn't part of the tenant's Kustomization. The result is reconcile loops and random deletions.

**Enforcement is cooperative today** — trust model. Every tenant is either me or a friend I've invited. A future admission controller (Kyverno or OPA Gatekeeper) could enforce this automatically. Tracked in `docs/16-next-steps.md`.

If a tenant needs to consume a platform service (Traefik ingress, cert-manager certificate, Authentik OIDC), they do so via CRs in *their own* namespace — an IngressRoute in the tenant namespace, a Certificate in the tenant namespace, etc. The platform controllers act on those CRs without the tenant needing to touch platform namespaces.

---

## Rate Limits (1Password Families Plan)

The 1Password Families plan shares **1,000 reads per day across the entire account**, not per service account. Every 1P-backed ExternalSecret refresh consumes reads from this shared pool, regardless of which service account performs the read.

### Current Budget

- This repo's ExternalSecrets: ~25 across all apps, 24h refreshInterval = ~25 reads/day.
- Headroom: ~975 reads/day.

### Adding a Tenant on 1Password

A tenant with 10 ExternalSecrets at 24h refreshInterval adds ~10 reads/day. Multiple tenants stack additively. Keep refreshIntervals at 24h unless there's a specific reason to shorten.

### Manual Refreshes

Every `task flux:refresh-secret` or `task flux:rotate-secret` call adds one extra read. Ad-hoc rotations are fine; scripted polling is not.

### Recommendation

Strongly prefer the GitLab-variables path for friends:

- No impact on the 1P budget.
- Scales without affecting other tenants.
- The friend's own GitLab PAT rate limits apply instead (generous: 2,000 requests/minute per user).

Use 1Password only when you control the vault directly (your own future repos).

---

## Removal

Remove a tenant by deleting its wiring file AND its entry in
`kubernetes/clusters/weisssrv/tenants/kustomization.yaml`:

```bash
git rm kubernetes/clusters/weisssrv/tenants/<repo-slug>.yaml
# Also edit kustomization.yaml to remove the matching `- <repo-slug>.yaml`
# line from `resources:`, then:
git commit -am "Remove tenant <repo-slug>"
git push
```

Flux prunes, in order:

1. The tenant's top-level `Kustomization` (which cascades to everything it reconciled — Deployments, Services, Ingresses, ExternalSecrets, Secrets, PVCs unless protected).
2. The tenant's `GitRepository` source.
3. The tenant's `ClusterSecretStore`. Because ExternalSecrets in that store use `creationPolicy: Owner`, their Secrets are deleted when the store goes away.
4. The tenant namespace.

### Manual Cleanup

Two things Flux doesn't clean up (by design, because it never owned them):

- The `onepassword-sdk-token` or `gitlab-api-token` bootstrap secret in the tenant namespace. But — the namespace is gone anyway, so these are gone with it.
- The 1Password service account (Path A) or GitLab project access token (Path B). These need manual revocation in 1P or GitLab. Do it immediately — no reason to leave a dangling credential.

```bash
# If the namespace somehow survived (shouldn't):
kubectl delete namespace <repo-slug>
```

### PVCs

PVCs have `pvc-protection` finalizers and aren't deleted during tenant removal unless explicitly torn down. If the tenant had persistent data on zvol-backed storage, the PV stays too. Delete manually if the data is truly no longer needed.

---

## Future: weisssrv-project-template Repo

Target: a dedicated GitLab template project that new tenant repos fork. Pre-wired with:

- `.gitlab-ci.yml` with lint/validate/Flux-deploy stubs.
- `kubernetes/flux/` skeleton with a namespace-scoped example.
- `README.md` walking through onboarding from the tenant side.
- Pre-configured kubeconform + yamllint CI jobs.

Once it exists, onboarding becomes:

1. Tenant forks the template.
2. Tenant fills in their workloads.
3. Operator (me) adds the wiring file to this repo.
4. Push — running.

Tracked in `docs/16-next-steps.md`.

---

## Worked Example: Onboarding `example-app`

End-to-end walkthrough using concrete values. Replace `example-app` with your real tenant slug.

### Scenario

- Repo: `https://git.ericsweiss.com/eric/example-app`
- Namespace: `example-app`
- Backend: 1Password, dedicated vault `Homelab-Example`
- Two secrets needed: an API key and a database password.

### Step 1 — 1Password Setup

In 1P:

1. Create vault `Homelab-Example`.
2. Add item `Example App Secrets` with fields `api-key` and `db-password`. Record the item ID (`op item get "Example App Secrets" --vault Homelab-Example --format json | jq -r '.id'`). Call it `abc123defghijklmnopqrstuv` for this example.
3. Create service account `weisssrv-example-app`, grant read access to `Homelab-Example` only. Save the token to the main `Homelab` vault as `Service Account Auth Token weisssrv example-app` / `credential`.

### Step 2 — Bootstrap Secret

```bash
kubectl create namespace example-app

TOKEN="$(op read 'op://Homelab/Service Account Auth Token weisssrv example-app/credential')"
kubectl -n example-app create secret generic onepassword-sdk-token \
  --from-literal=token="$TOKEN" \
  --dry-run=client -o yaml \
  | kubectl apply -f -
unset TOKEN

# Verify
kubectl get secret onepassword-sdk-token -n example-app
```

### Step 3 — Wiring File

Create `kubernetes/clusters/weisssrv/tenants/example-app.yaml`:

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: example-app
  labels:
    app.kubernetes.io/managed-by: flux
    fluxcd.io/tenant: example-app
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: onepassword-example-app
spec:
  provider:
    onepasswordSDK:
      vault: Homelab-Example
      auth:
        serviceAccountSecretRef:
          name: onepassword-sdk-token
          namespace: example-app
          key: token
---
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: example-app
  namespace: flux-system
spec:
  interval: 1m
  url: https://git.ericsweiss.com/eric/example-app
  ref:
    branch: main
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: example-app
  namespace: flux-system
spec:
  interval: 10m
  retryInterval: 1m
  timeout: 10m
  sourceRef:
    kind: GitRepository
    name: example-app
  path: ./kubernetes/flux
  prune: true
  targetNamespace: example-app
  wait: true
```

Register the file in the tenants Kustomization aggregate:

```yaml
# kubernetes/clusters/weisssrv/tenants/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - example-app.yaml
```

Commit + push both files:

```bash
git add kubernetes/clusters/weisssrv/tenants/example-app.yaml \
        kubernetes/clusters/weisssrv/tenants/kustomization.yaml
git commit -m "Onboard example-app tenant"
git push
```

Within ~1 minute Flux reconciles and creates the ClusterSecretStore, GitRepository, and Kustomization.

### Step 4 — Tenant Repo Contents

In `example-app` repo, create `kubernetes/flux/`:

```yaml
# kubernetes/flux/externalsecret.yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: example-app-secrets
  namespace: example-app
spec:
  refreshInterval: 24h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-example-app
  target:
    name: example-app-secrets
    creationPolicy: Owner
  data:
    - secretKey: api-key
      remoteRef:
        key: abc123defghijklmnopqrstuv/api-key  # Example App Secrets
    - secretKey: db-password
      remoteRef:
        key: abc123defghijklmnopqrstuv/db-password  # Example App Secrets
```

```yaml
# kubernetes/flux/deployment.yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: example-app
  namespace: example-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: example-app
  template:
    metadata:
      labels:
        app: example-app
    spec:
      containers:
        - name: app
          image: ghcr.io/example/app:v1.0.0
          env:
            - name: API_KEY
              valueFrom:
                secretKeyRef:
                  name: example-app-secrets
                  key: api-key
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: example-app-secrets
                  key: db-password
```

```yaml
# kubernetes/flux/kustomization.yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: example-app
resources:
  - externalsecret.yaml
  - deployment.yaml
```

Commit + push to the `example-app` repo. Flux's `example-app` Kustomization (living in the main cluster) picks it up within ~1 minute.

### Step 5 — Verify

```bash
# Wiring resources in the cluster
kubectl get kustomization example-app -n flux-system
kubectl get gitrepository example-app -n flux-system
kubectl get clustersecretstore onepassword-example-app

# Tenant workload
kubectl get externalsecret -n example-app
# NAME                  STORE                      REFRESH   STATUS        READY
# example-app-secrets   onepassword-example-app    24h       SecretSynced  True

kubectl get secret example-app-secrets -n example-app
kubectl get deploy -n example-app
kubectl get pods -n example-app
```

All Ready. Tenant is live.

### Step 6 — Removal (Later)

When `example-app` is no longer needed:

```bash
# From weisssrv repo
git rm kubernetes/clusters/weisssrv/tenants/example-app.yaml
git commit -m "Remove example-app tenant"
git push

# Wait for Flux to prune
kubectl get ns example-app  # eventually NotFound
```

Then in 1Password:

- Revoke the `weisssrv-example-app` service account.
- Delete or archive the `Homelab-Example` vault.
- Delete the `Service Account Auth Token weisssrv example-app` item from `Homelab`.

The tenant is fully gone.
