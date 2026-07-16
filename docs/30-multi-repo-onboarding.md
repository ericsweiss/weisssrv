# Multi-Repo Onboarding Guide

How to attach an external Git repository to the weisssrv k3s cluster so Flux reconciles its workloads into a dedicated namespace. Covers two backends for secret management — 1Password (my own repos) and GitLab CI/CD variables (friends without 1Password) — plus namespace isolation rules, rate-limit considerations, and removal.

## Table of Contents

1. [Overview](#overview) (incl. the [Pre-Onboarding Checklist](#pre-onboarding-checklist))
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
| 1Password | You have a 1P Families account and want secrets in 1Password | Costs 1P read budget; multiple isolation options (see Path A) |
| GitLab variables | Friend without 1P; tenant already has a GitLab project | Secrets are visible to anyone with project Maintainer access; no 1P budget impact |

Prefer GitLab variables for friends — see [Rate Limits](#rate-limits-1password-families-plan).

### Pre-Onboarding Checklist

Before the **first** tenant reconciles anything, close these platform-side
gaps (each is an accepted risk only while every manifest is
operator-authored):

- **Guard Traefik's `allowCrossNamespace: true`.** The CRD provider currently
  lets any IngressRoute reference middlewares/Services in other namespaces
  (see the accepted-risk comment in
  `kubernetes/infrastructure/controllers/traefik/release.yaml` and the
  matching security note in `kubernetes/clusters/weisssrv/tenants/README.md`).
  A tenant-authored IngressRoute could pull platform middlewares or another
  namespace's Service. Options: scope the provider per-tenant, add a
  validating policy pinning `@namespace` refs, or revert to
  `allowCrossNamespace: false`.
- **Scope the GitLab agent's RBAC below cluster-admin.** The agent
  (`kubernetes/apps/gitlab-agent/release.yaml`) currently gets cluster-admin
  via the chart's default `rbac.create` — deliberate while it is the CI
  deploy path for the whole `kubernetes/` tree, but before tenant repos
  deploy through it, replace that with namespaced Roles per tenant (or a
  least-privilege ClusterRole) so a compromised agentk can't touch the whole
  cluster.
- **Use the tenant ServiceAccount pattern.** Every tenant Kustomization must
  set `serviceAccountName` (see the wiring templates below) — without it,
  kustomize-controller applies tenant manifests with its own cluster-admin
  credentials.

---

## Path A — 1Password Backend

For repos where secrets live in 1Password and ESO reads them via the 1Password Connect provider.

**Important architectural context**: The shared Connect server deployed in `external-secrets` namespace was bootstrapped with access to the `Homelab` vault only. A Connect server can only access vaults it was granted at creation time. This means tenant `ClusterSecretStore` resources that point at the shared `connectHost` can only read from vaults the shared server has access to. This constrains the available isolation models — see below.

### Isolation Options

Three approaches exist for tenant secrets with 1Password Connect. Each trades off isolation against operational complexity.

#### Option A — Recreate Shared Connect with Multi-Vault Access

Recreate the shared Connect server with access to all needed vaults (the main `Homelab` vault plus each tenant's dedicated vault). Tenant `ClusterSecretStore` resources point at the same shared `connectHost` but are scoped to their own vault.

**Setup**: Each time a new tenant is added, recreate the Connect server credentials with the expanded vault list, then replace the `op-credentials` bootstrap secret in the `external-secrets` namespace and restart the Connect pods.

```bash
# Recreate with expanded vault list
op connect server create weisssrv-connect --vaults Homelab,Homelab-TenantA,Homelab-TenantB

# Replace bootstrap secret
kubectl -n external-secrets delete secret op-credentials
kubectl -n external-secrets create secret generic op-credentials \
  --from-file=1password-credentials.json=./1password-credentials.json

# Restart Connect
kubectl -n external-secrets rollout restart deployment onepassword-connect
```

Per-tenant tokens are still scoped to a single vault, so tenant ExternalSecrets cannot read from other vaults.

| Pros | Cons |
|---|---|
| Strong vault-level isolation | Adding a tenant requires re-bootstrapping Connect (restart, brief downtime) |
| Tenants cannot read each other's secrets | Must coordinate Connect credential rotation across all tenants |
| Single Connect deployment (low resource cost) | Vault list in credentials grows with each tenant |

#### Option B — Separate Connect Server Per Tenant

Deploy a dedicated Connect server in each tenant's namespace. Each server has its own `op-credentials` and token, scoped to a single tenant vault. The tenant's `SecretStore` (namespace-scoped, not cluster-scoped) points at its own Connect instance.

**Setup**: Create a Connect server per tenant, deploy it as a separate pod in the tenant namespace, and create a namespace-scoped `SecretStore`.

```bash
# Create tenant-specific Connect server
op connect server create weisssrv-<repo-slug> --vaults Homelab-<repo-slug>
op connect token create weisssrv-<repo-slug>-eso --server <SERVER_ID> --vaults Homelab-<repo-slug>

# Bootstrap in tenant namespace
kubectl create namespace <repo-slug>
kubectl -n <repo-slug> create secret generic op-credentials \
  --from-file=1password-credentials.json=./1password-credentials.json
kubectl -n <repo-slug> create secret generic onepassword-connect-token \
  --from-literal=token=<CONNECT_TOKEN>

# Deploy Connect server in tenant namespace (separate Helm release or Deployment)
# Then create a SecretStore (not ClusterSecretStore) pointing at the local Connect
```

| Pros | Cons |
|---|---|
| Strongest isolation (dedicated server per tenant) | More pods (Connect API + Sync per tenant) |
| Adding/removing tenants is fully independent | Higher memory and CPU overhead |
| No shared infrastructure to coordinate | More bootstrap secrets to manage |

#### Option C — Shared Connect, Shared Vault (Recommended)

Use the existing shared Connect server and the existing `Homelab` vault for tenant secrets. Tenant items are stored in the same vault using a naming convention: prefix item titles with the tenant name (e.g. `example-app: API Secrets`, `example-app: Database`). The tenant's `ClusterSecretStore` points at the shared Connect and the shared vault. No vault isolation between tenants.

This is the **recommended default** for this homelab. The trust model is single-operator with invited friends — every tenant is either you or someone you trust. The operational simplicity outweighs the lack of vault isolation. No Connect re-bootstrapping, no extra pods, no per-tenant credential management.

**Setup**: Add items to the existing `Homelab` vault with a tenant prefix. Create a Connect token scoped to the `Homelab` vault for the tenant (or reuse the existing token). The tenant's `ClusterSecretStore` points at the shared Connect server.

```bash
# Create a scoped Connect token for the tenant (optional — can reuse existing token)
op connect token create weisssrv-<repo-slug>-eso --server <EXISTING_SERVER_ID> --vaults Homelab

# Bootstrap in tenant namespace (token only — no op-credentials needed,
# Connect server already runs in external-secrets namespace)
kubectl create namespace <repo-slug>
kubectl -n <repo-slug> create secret generic onepassword-connect-token \
  --from-literal=token=<CONNECT_TOKEN>
```

| Pros | Cons |
|---|---|
| Zero operational overhead — no Connect changes when adding tenants | No vault isolation (all tenants share `Homelab` vault) |
| No extra pods, no re-bootstrapping | Tenant ExternalSecrets could theoretically read any item in the vault |
| Works with existing Connect deployment as-is | Requires naming discipline to avoid item collisions |
| Simplest to set up and maintain | Not suitable if tenants are untrusted |

**Item naming convention**: Prefix all tenant items with `<repo-slug>:` to avoid collisions with existing items. Examples:
- `example-app: API Secrets` (fields: `api-key`, `db-password`)
- `example-app: Database` (fields: `host`, `port`, `password`)

### Steps (Option C — Shared Connect, Shared Vault)

The steps below use Option C. For Options A or B, substitute the vault/Connect setup from the relevant option above and adjust the `ClusterSecretStore` accordingly.

### 1. Add Tenant Secrets to the Homelab Vault

In 1Password, create items in the `Homelab` vault using the naming convention `<repo-slug>: <Item Name>`. Example: `example-app: API Secrets` with fields `api-key` and `db-password`.

### 2. Create a Connect Token (Optional)

You can reuse the existing Connect token or create a new one scoped to the `Homelab` vault:

```bash
op connect token create weisssrv-<repo-slug>-eso --server <EXISTING_SERVER_ID> --vaults Homelab
```

Creating per-tenant tokens is recommended so revoking one tenant's access doesn't affect others.

### 3. Seed the Bootstrap Secret in the Tenant Namespace

```bash
kubectl create namespace <repo-slug>

kubectl -n <repo-slug> create secret generic onepassword-connect-token \
  --from-literal=token=<CONNECT_TOKEN>
```

Only the token is needed. The Connect server already runs in `external-secrets` and has the `op-credentials` secret.

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
    onepassword:
      connectHost: http://onepassword-connect.external-secrets.svc.cluster.local:8080
      vaults:
        Homelab: 1
      auth:
        secretRef:
          connectTokenSecretRef:
            name: onepassword-connect-token
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
# Tenant reconciliation runs under a namespace-scoped ServiceAccount —
# without serviceAccountName, kustomize-controller applies tenant manifests
# with its own cluster-admin credentials. The SA must live in the
# Kustomization's OWN namespace (flux-system); the RoleBinding in the tenant
# namespace grants it admin there and nowhere else.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <repo-slug>-flux
  namespace: flux-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: <repo-slug>-flux-admin
  namespace: <repo-slug>
subjects:
  - kind: ServiceAccount
    name: <repo-slug>-flux
    namespace: flux-system
roleRef:
  kind: ClusterRole
  name: admin
  apiGroup: rbac.authorization.k8s.io
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
  serviceAccountName: <repo-slug>-flux
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

ExternalSecret example for a tenant workload (note the prefixed item title):

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
        key: "<repo-slug>: App Secrets"
        property: api-key
```

Use the full prefixed item title in `remoteRef.key` and field names in `remoteRef.property` — same format as this repo's ExternalSecrets. See `docs/29-flux-operations.md` for the format rules.

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
# ServiceAccount + RoleBinding: identical pattern to Path A step 4 —
# the Kustomization must NOT reconcile with kustomize-controller's
# cluster-admin credentials.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <repo-slug>-flux
  namespace: flux-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: <repo-slug>-flux-admin
  namespace: <repo-slug>
subjects:
  - kind: ServiceAccount
    name: <repo-slug>-flux
    namespace: flux-system
roleRef:
  kind: ClusterRole
  name: admin
  apiGroup: rbac.authorization.k8s.io
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
  serviceAccountName: <repo-slug>-flux
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

- Platform namespaces: `flux-system`, `external-secrets`, `metallb-system`, `cert-manager`, `traefik`, `external-dns`, `authentik`, `observability`.
- Other tenants' namespaces.

Why: Flux's server-side apply with `prune: true` will fight any resources that appear in a namespace that isn't part of the tenant's Kustomization. The result is reconcile loops and random deletions.

**Enforcement**: the Flux apply path is RBAC-scoped — each tenant Kustomization sets `serviceAccountName`, and the SA's RoleBinding grants `admin` only in the tenant's namespace, so a tenant manifest targeting another namespace fails to apply. What remains cooperative is everything outside that path (e.g. which ClusterSecretStore a namespace references, cross-namespace Traefik refs — see the Pre-Onboarding Checklist). A future admission controller (Kyverno or OPA Gatekeeper) could close those. Tracked in `docs/16-next-steps.md`.

If a tenant needs to consume a platform service (Traefik ingress, cert-manager certificate, Authentik OIDC), they do so via CRs in *their own* namespace — an IngressRoute in the tenant namespace, a Certificate in the tenant namespace, etc. The platform controllers act on those CRs without the tenant needing to touch platform namespaces.

---

## Rate Limits (1Password Families Plan)

The 1Password Families plan shares **1,000 reads per day across the entire account**, not per service account. Every 1P-backed ExternalSecret refresh consumes reads from this shared pool, regardless of which service account performs the read.

### Current Budget

- This repo's current ExternalSecret footprint and its rate-limit accounting live
  in [docs/29-flux-operations.md](29-flux-operations.md) § Rate Limits (the
  authoritative copy) — refer to it rather than duplicating the counts here. In
  short: the Connect provider syncs the vault to a local cache, so per-field reads
  hit the cache, not the cloud API, and the effective rate-limit cost is low. Run
  `kubectl get externalsecrets -A` for current counts.

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

- The `onepassword-connect-token` or `gitlab-api-token` bootstrap secrets in the tenant namespace. But the namespace is gone anyway, so these are gone with it.
- The 1Password Connect token (Path A) or GitLab project access token (Path B). Revoke these in 1P or GitLab immediately — no reason to leave a dangling credential. For Path A Option C, also delete/archive the tenant's prefixed items in the `Homelab` vault if they are no longer needed.

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
- Backend: 1Password (Option C — shared Connect, shared `Homelab` vault)
- Two secrets needed: an API key and a database password.

### Step 1 — 1Password Setup

In 1P, add items to the existing `Homelab` vault using the tenant prefix:

1. Create item `example-app: App Secrets` with fields `api-key` and `db-password`.
2. Create a Connect token: `op connect token create weisssrv-example-app-eso --server <EXISTING_SERVER_ID> --vaults Homelab`

### Step 2 — Bootstrap Secret

```bash
kubectl create namespace example-app

kubectl -n example-app create secret generic onepassword-connect-token \
  --from-literal=token=<CONNECT_TOKEN>

# Verify
kubectl get secret onepassword-connect-token -n example-app
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
    onepassword:
      connectHost: http://onepassword-connect.external-secrets.svc.cluster.local:8080
      vaults:
        Homelab: 1
      auth:
        secretRef:
          connectTokenSecretRef:
            name: onepassword-connect-token
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
# SA + RoleBinding: see Path A step 4 — the Kustomization reconciles as
# this namespace-scoped SA, not as kustomize-controller's cluster-admin.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: example-app-flux
  namespace: flux-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: example-app-flux-admin
  namespace: example-app
subjects:
  - kind: ServiceAccount
    name: example-app-flux
    namespace: flux-system
roleRef:
  kind: ClusterRole
  name: admin
  apiGroup: rbac.authorization.k8s.io
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
  serviceAccountName: example-app-flux
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
        key: "example-app: App Secrets"
        property: api-key
    - secretKey: db-password
      remoteRef:
        key: "example-app: App Secrets"
        property: db-password
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
# Also remove the entry from kustomization.yaml
vim kubernetes/clusters/weisssrv/tenants/kustomization.yaml
# Delete the `- example-app.yaml` line from resources:
git add kubernetes/clusters/weisssrv/tenants/kustomization.yaml
git commit -m "Remove example-app tenant"
git push

# Wait for Flux to prune
kubectl get ns example-app  # eventually NotFound
```

Then in 1Password:

- Revoke the Connect token for `weisssrv-example-app-eso`.
- Delete or archive the `example-app: *` prefixed items in the `Homelab` vault.

The tenant is fully gone.
