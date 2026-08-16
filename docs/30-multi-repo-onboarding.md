# Multi-Repo Onboarding Guide

How to attach an external Git repository to the weisssrv k3s cluster so Flux reconciles its workloads into a dedicated namespace. Covers two backends for secret management — 1Password (my own repos) and GitLab CI/CD variables (friends without 1Password) — plus namespace isolation rules, rate-limit considerations, and removal.

## Overview

Each external repo that deploys to this cluster gets **one wiring file** in `kubernetes/clusters/weisssrv/tenants/<repo-slug>.yaml`. That file defines three things:

1. **Secret backend** — a `ClusterSecretStore` (1Password or GitLab variables) scoped to the tenant's namespace with `spec.conditions`. Omit that block and any namespace in the cluster can read the store.
2. **Git source** — a Flux `GitRepository` pointing at the tenant's repo and branch.
3. **Top-level Kustomization** — reconciles the tenant's `kubernetes/flux/` path into a dedicated namespace.

The tenant's repo contains its own `kubernetes/flux/` tree with workloads, ExternalSecrets (referencing their own store), etc. The wiring file stays in this repo.

See `kubernetes/clusters/weisssrv/tenants/README.md` for the canonical templates.

The tenant repo itself is generated with `copier copy` from
`eric/weisssrv-app-template` — see
[weisssrv-app-template Repo](#weisssrv-app-template-repo) below. That generated
repo ships a `docs/ONBOARDING.md` rendered with its own slug and namespace,
which is the fastest way to get the wiring file right; the sections here are the
cluster-side reference and cover hand-authored tenants too.

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
  `allowCrossNamespace: false`. `scripts/check-tenant-traefik-isolation.py`
  (run by `task lint`) fails the commit that adds the first tenant wiring file
  while the flag is still true, so this line is a gate rather than a re-read.
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
  credentials. Bind the SA to **both** the built-in `admin` ClusterRole **and**
  the shared `tenant-crd-editor` ClusterRole
  (`kubernetes/clusters/weisssrv/tenants/tenant-crd-editor.yaml`): `admin` does
  not cover the `traefik.io`, `monitoring.coreos.com`, or `autoscaling.k8s.io`
  CRD groups, so a tenant's IngressRoute / ServiceMonitor / PrometheusRule / VPA
  would otherwise fail to apply with Forbidden.
- **Label the tenant Namespace for Pod Security Admission.** The wiring
  templates below carry `pod-security.kubernetes.io/enforce: baseline` (+ `warn`
  / `audit: restricted`) so the platform actually enforces the non-root /
  read-only-rootfs posture the tenant template ships.
- **Require a namespace-wide ingress default-deny in the tenant repo.** It is
  mandatory in every namespace on this cluster, but
  `scripts/check-default-deny-coverage.py` reads only *this* repo's rendered
  corpus — a tenant repo's namespace is invisible to it, so this is a **review
  responsibility at onboarding**, not a gate. A copier-generated repo ships
  `template/kubernetes/flux/networkpolicy.yaml.jinja` already; a hand-authored
  one must add the deny plus a scrape-allow from `observability`.

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
# Find the Connect server ID with `op connect server list`, then create a
# scoped token for the tenant (optional — you can reuse the existing token):
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

You can reuse the existing Connect token or create a new one scoped to the
`Homelab` vault (find the server ID with `op connect server list`):

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

Create `kubernetes/clusters/weisssrv/tenants/<repo-slug>.yaml`.
`kubernetes/clusters/weisssrv/tenants/README.md` § *1Password-backed tenant*
holds the canonical template — copy it and substitute `<repo-slug>` throughout.
It wires, in one file: the Namespace with its PSA labels, a `ClusterSecretStore`
scoped to that namespace by `conditions` (a cluster-scoped store without them is
readable from every namespace), the tenant's `GitRepository`, a namespace-scoped
`ServiceAccount` in **flux-system** (without `serviceAccountName` the
Kustomization reconciles with kustomize-controller's cluster-admin credentials),
and two RoleBindings in the tenant namespace — `admin` **plus** the shared
`tenant-crd-editor` ClusterRole, because `admin` does not aggregate traefik.io /
monitoring.coreos.com / autoscaling.k8s.io and the Kustomization goes NotReady on
the first IngressRoute, ServiceMonitor, PrometheusRule or VPA without it.

Then register the file in the tenants Kustomize aggregate
(`kubernetes/clusters/weisssrv/tenants/kustomization.yaml`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - tenant-crd-editor.yaml   # shared ClusterRole (already present)
  - <repo-slug>.yaml         # <-- add this line
```

Commit + push both files. Flux picks up the new tenant on the next
reconcile.

### 5. Build Out the Tenant Repo

A repo generated from the app template already has all of this — the tenant's
work is replacing the placeholder image and hostnames. For a hand-authored
tenant, create `kubernetes/flux/` with:

- Workload manifests (Deployments, HelmReleases, etc.).
- A **namespace-wide ingress default-deny NetworkPolicy** plus a scrape-allow
  from the `observability` namespace. Mandatory on this cluster, and no platform
  gate can see it from here — see the Pre-Onboarding Checklist.
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

Copy the **GitLab-variables template** from
`kubernetes/clusters/weisssrv/tenants/README.md` — it is the canonical copy —
and substitute `<repo-slug>` throughout. The only deltas from Path A are:

- the `ClusterSecretStore` uses the `gitlab` provider (`projectID`,
  `environment: "*"`, `auth.SecretRef.accessToken` → the `gitlab-api-token`
  secret seeded in step 2) instead of the `onepassword` provider;
- the bootstrap secret is `gitlab-api-token`, not
  `onepassword-connect-token`.

Everything else — the Namespace and its PSA labels, the GitRepository, the
namespace-scoped ServiceAccount, the `admin` **and** `tenant-crd-editor`
RoleBindings, and the Kustomization — is identical to Path A.

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

**Enforcement**: the Flux apply path is RBAC-scoped. Each tenant Kustomization
sets `serviceAccountName`, and the SA's RoleBindings grant `admin` **and**
`tenant-crd-editor` only in the tenant's namespace, so a tenant manifest
targeting another namespace fails to apply. (`tenant-crd-editor` is a shared
ClusterRole covering the CRD groups `admin` misses — `traefik.io`,
`monitoring.coreos.com`, `autoscaling.k8s.io` — but each RoleBinding is
namespace-scoped, so it grants nothing cluster-wide.)

What remains cooperative is everything outside that path: which
ClusterSecretStore a namespace references, cross-namespace Traefik refs — see the
Pre-Onboarding Checklist. An admission controller (Kyverno or OPA Gatekeeper)
would close those; tracked in `docs/16-next-steps.md`.

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

## weisssrv-app-template Repo

The tenant-side repo is **generated**, not forked:
`eric/weisssrv-app-template` is a [copier](https://copier.readthedocs.io/)
template.

```bash
pipx install copier
copier copy https://git.ericsweiss.com/eric/weisssrv-app-template my-service
```

Copier asks for the app's identity (slug, namespace, port, replicas), the
cluster it targets (external/internal domain, node-label domain, internal VIP,
registry hosts, runbook URL), the pipeline shape, the secret backend, and which
optional components to render. It writes the answers to `.copier-answers.yml`,
so a later `copier update` replays them against a newer template tag and lands
as a reviewable diff — the reason there is no fork and no rename script.

**The template's own README and `docs/` are canonical for its contents** — this
page deliberately does not restate the file list, because that duplication is
what goes stale. `docs/CONSUMING.md` is the answer reference, `docs/CI-SHAPES.md`
the pipeline table, `docs/ARCHITECTURE.md` the construction, `docs/VERSIONING.md`
the update contract.

What matters from the cluster side:

- **The generated repo carries its own `docs/ONBOARDING.md`, already rendered
  with the tenant's real slug and namespace** — including a copy of the wiring
  file this page describes. Ask the tenant for that page rather than
  transcribing values out of a chat.
- **Pipelines vary; the deploy path does not.** `ci_shape` picks self-hosted
  GitLab (jobs `include:`d from `eric/weisssrv-lib` at a pinned release tag,
  the same library this repo consumes — docs/13 § Shared CI library), GitHub
  Actions (vendored workflows), or no pipeline at all. In every shape CI only
  checks and optionally builds an image: Flux does the deploying, from the
  wiring file in `kubernetes/clusters/weisssrv/tenants/`.
- **A private image needs a pull credential in the tenant namespace.** The
  template renders the `imagePullSecret` wiring when
  `enable_registry_pull_secret` is on; the credential itself (a `read_registry`
  deploy token plus its generated username) is an operator/tenant step, covered
  in the generated ONBOARDING.
- There is **no** Renovate anywhere in the family; image tags are bumped by hand.

Onboarding is therefore:

1. Tenant runs `copier copy` and answers for this cluster.
2. Tenant fills in their workloads and follows the generated tenant checklist.
3. Operator does the cluster-side steps on this page — the tenant's rendered
   ONBOARDING lists exactly which ones that repo needs.
4. Push — running.

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
2. Create a Connect token (find the server ID with `op connect server list`):
   `op connect token create weisssrv-example-app-eso --server <EXISTING_SERVER_ID> --vaults Homelab`

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

Copy the **1Password template** from
`kubernetes/clusters/weisssrv/tenants/README.md` and substitute `example-app`
for `<repo-slug>` throughout (namespace, ClusterSecretStore `conditions`,
GitRepository URL, ServiceAccount, both RoleBindings, Kustomization
`targetNamespace`). Nothing else in the template changes.

Register the file in the tenants Kustomization aggregate:

```yaml
# kubernetes/clusters/weisssrv/tenants/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - tenant-crd-editor.yaml   # shared ClusterRole (already present)
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

---

## Related documentation

- [docs/29-flux-operations.md](29-flux-operations.md) — Flux day-2 operations
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the 1Password model
- [`kubernetes/clusters/weisssrv/tenants/README.md`](../kubernetes/clusters/weisssrv/tenants/README.md) — the canonical wiring templates
- [docs/13-ci-cd.md](13-ci-cd.md) — the shared CI library tenants consume
