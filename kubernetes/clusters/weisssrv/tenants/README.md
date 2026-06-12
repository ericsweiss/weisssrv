# Tenant wiring for the weisssrv k3s cluster

Each external repo that deploys workloads to this cluster gets a single YAML
file in this folder AND a matching entry in `kustomization.yaml` (the file
itself is not auto-discovered — Kustomize requires explicit resource
listings). The tenant file defines the tenant's `ClusterSecretStore` (1P)
or `SecretStore` (GitLab variables), Flux `GitRepository` source, and
top-level `Kustomization`. One file per tenant keeps ownership clear and
makes removal trivial (delete the file, remove the entry from
`kustomization.yaml`, push — Flux prunes the resources it created).

For the tenant-side repo scaffold (CI/lint/AI-review/flux-deploy stubs), use
the forthcoming `weisssrv-project-template` GitLab template project — see
`docs/16-next-steps.md`. Until that exists, tenants hand-author their
`kubernetes/` tree following the patterns in this repo.

See `docs/30-multi-repo-onboarding.md` for the full onboarding procedure.

## File naming

One file per tenant, named after the tenant repo: `<repo-slug>.yaml`.

Every tenant file requires a matching edit to `kustomization.yaml` in this
directory so Flux picks it up (Kustomize does not auto-discover files):

```yaml
# kubernetes/clusters/weisssrv/tenants/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - example-app.yaml        # <-- add this line alongside your new tenant file
  - friend-project.yaml
```

## Example: 1Password-backed tenant (Option C — shared Connect, shared vault)

This template uses the recommended Option C approach: tenant secrets live in the
shared `Homelab` vault with a naming convention prefix, and the tenant's
`ClusterSecretStore` points at the existing shared Connect server. No Connect
re-bootstrapping or extra pods required.

For alternative isolation models (per-tenant vaults or per-tenant Connect
servers), see `docs/30-multi-repo-onboarding.md` — Options A and B.

```yaml
# kubernetes/clusters/weisssrv/tenants/example-app.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: example-app
  labels:
    app.kubernetes.io/managed-by: flux
    fluxcd.io/tenant: example-app
---
# One-time bootstrap (NOT managed by Flux):
#   kubectl -n example-app create secret generic onepassword-connect-token \
#     --from-literal=token=<CONNECT_TOKEN>
#
# The token should be scoped to the Homelab vault. Create one per tenant
# so revoking access is independent:
#   op connect token create weisssrv-example-app-eso \
#     --server <EXISTING_SERVER_ID> --vaults Homelab
#
# Tenant 1P items use a naming convention: prefix with "<repo-slug>: "
# e.g. "example-app: App Secrets" with fields "api-key", "db-password".
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
  # Private repo: create a deploy token in GitLab → add its secret to
  # flux-system namespace → reference here via secretRef.name.
---
# Tenant reconciliation runs under a namespace-scoped ServiceAccount —
# without serviceAccountName, kustomize-controller applies tenant manifests
# with its own cluster-admin credentials.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: example-app-flux
  namespace: example-app
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: example-app-flux-admin
  namespace: example-app
subjects:
  - kind: ServiceAccount
    name: example-app-flux
    namespace: example-app
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

## Example: GitLab-variables-backed tenant (friends without 1P)

ESO's GitLab provider reads secrets from GitLab project CI/CD variables. Lower
friction for tenants that already have a GitLab project and don't use 1P.

```yaml
# kubernetes/clusters/weisssrv/tenants/friend-project.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: friend-project
  labels:
    app.kubernetes.io/managed-by: flux
    fluxcd.io/tenant: friend-project
---
# One-time bootstrap: the friend creates a Personal Access Token in GitLab
# with read_api scope, then:
#   kubectl -n friend-project create secret generic gitlab-api-token \
#     --from-literal=token=glpat-...
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: gitlab-friend-project
spec:
  provider:
    gitlab:
      url: https://git.ericsweiss.com
      projectID: "<NUMERIC_PROJECT_ID>"  # from GitLab > Settings > General
      auth:
        SecretRef:
          accessToken:
            name: gitlab-api-token
            namespace: friend-project
            key: token
      environment: "*"
---
# GitRepository + Kustomization blocks same as the 1P example above, with
# secretStoreRef pointing at gitlab-friend-project.
```

## Security considerations

> **Security note**: The default tenant model (Option C) uses the shared `Homelab`
> vault — tenant ExternalSecrets could theoretically read any item in that vault, not
> just their prefixed items. This is acceptable for this homelab's trust model
> (single-operator, invited friends only). For stronger isolation, see Options A
> (multi-vault shared Connect) and B (per-tenant Connect server) in
> `docs/30-multi-repo-onboarding.md`.
>
> The `onepassword-homelab` ClusterSecretStore used by the main repo's workloads is
> separate from per-tenant `ClusterSecretStore` resources, but both point at the same
> Connect server and vault. Namespace-level enforcement is cooperative today — a
> future admission controller (Kyverno/OPA) could restrict which stores a namespace
> may reference.

## Namespace ownership

Each tenant owns one dedicated namespace. Tenants MUST NOT create resources in
other tenants' namespaces or platform namespaces (`flux-system`,
`external-secrets`, `metallb-system`, `cert-manager`, `traefik`,
`external-dns`, `authentik`, `observability`). Flux pruning would fight them.

Enforcement is cooperative today (small group of trusted tenants). A future
admission controller (Kyverno/OPA) could block cross-namespace writes
automatically — tracked in `docs/16-next-steps.md`.

## Removal

Delete `tenants/<slug>.yaml` and the matching line in `kustomization.yaml`,
then commit. Flux prunes:
- The tenant's `Kustomization` (which cascades to everything it created,
  including the tenant's ExternalSecrets — and because those use
  `creationPolicy: Owner`, their rendered `Secret`s are deleted too)
- The tenant's `GitRepository` source
- The tenant's `ClusterSecretStore` (now has no consumers)
- The tenant namespace

The `onepassword-connect-token` bootstrap secret is not Flux-managed; it is
deleted when the namespace is pruned. Revoke the Connect token in 1Password
and delete/archive the tenant's prefixed items in the `Homelab` vault.
