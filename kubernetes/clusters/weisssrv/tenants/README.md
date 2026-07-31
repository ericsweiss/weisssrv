# Tenant wiring for the weisssrv k3s cluster

Each external repo that deploys workloads to this cluster gets a single YAML
file in this folder AND a matching entry in `kustomization.yaml` (the file
itself is not auto-discovered — Kustomize requires explicit resource
listings). The tenant file defines the tenant's `ClusterSecretStore` (1P)
or `SecretStore` (GitLab variables), Flux `GitRepository` source, and
top-level `Kustomization`. One file per tenant keeps ownership clear and
makes removal trivial (delete the file, remove the entry from
`kustomization.yaml`, push — Flux prunes the resources it created).

For the tenant-side repo scaffold (lint + kubeconform + secret-scan CI; deploys
happen cluster-side via Flux, not CI), fork the `weisssrv-app-template`
GitLab project (`git.ericsweiss.com/eric/weisssrv-app-template`) — see
`docs/16-next-steps.md`. Tenants can instead hand-author their `kubernetes/`
tree following the patterns in this repo.

See `docs/30-multi-repo-onboarding.md` for the full onboarding procedure.

`tenant-crd-editor.yaml` in this folder is a shared `ClusterRole` (always
applied, harmless while unbound) that every tenant wiring file binds alongside
the built-in `admin` ClusterRole — `admin` does not cover the `traefik.io`,
`monitoring.coreos.com`, or `autoscaling.k8s.io` CRD groups a tenant app uses.
See the RBAC comment in the example below.

## File naming

One file per tenant, named after the tenant repo: `<repo-slug>.yaml`.

Every tenant file requires a matching edit to `kustomization.yaml` in this
directory so Flux picks it up (Kustomize does not auto-discover files):

```yaml
# kubernetes/clusters/weisssrv/tenants/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - tenant-crd-editor.yaml  # shared ClusterRole (always present)
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
    # Pod Security Admission — baseline enforced, restricted advised. The
    # tenant template ships non-root / read-only-rootfs pods that satisfy
    # baseline; these labels make the platform actually enforce it.
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/audit: restricted
---
# One-time bootstrap (NOT managed by Flux):
#   kubectl -n example-app create secret generic onepassword-connect-token \
#     --from-literal=token=<CONNECT_TOKEN>
#
# The token should be scoped to the Homelab vault. Create one per tenant
# so revoking access is independent (find the server ID with
# `op connect server list`):
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
  # A ClusterSecretStore is cluster-scoped: without conditions, ANY namespace
  # (including another tenant's) can reference it by name and read this store's
  # vault. Scope every tenant store to its own namespace — same mechanism the
  # platform store uses (infrastructure/configs/cluster-secret-store.yaml).
  conditions:
    - namespaces:
        - example-app
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
# with its own cluster-admin credentials. The SA must live in the
# Kustomization's OWN namespace (flux-system): kustomize-controller
# impersonates system:serviceaccount:<Kustomization namespace>:<name>, so an
# SA created in the tenant namespace would never be used. The RoleBinding in
# the tenant namespace then grants that flux-system SA admin there and
# nowhere else.
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
# `admin` (bound above) does NOT aggregate the platform CRD groups a tenant
# uses — traefik.io (IngressRoute), monitoring.coreos.com
# (ServiceMonitor/PrometheusRule) and autoscaling.k8s.io (VerticalPodAutoscaler).
# Bind the shared `tenant-crd-editor` ClusterRole (tenant-crd-editor.yaml)
# alongside admin so those CRs apply; without it the tenant Kustomization goes
# NotReady on the first IngressRoute/ServiceMonitor/PrometheusRule/VPA.
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: example-app-flux-crd-editor
  namespace: example-app
subjects:
  - kind: ServiceAccount
    name: example-app-flux
    namespace: flux-system
roleRef:
  kind: ClusterRole
  name: tenant-crd-editor
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
  # Wait for the platform CRD chain (sources -> controllers -> configs) so a
  # fresh bootstrap doesn't apply this tenant's ExternalSecret/IngressRoute/etc.
  # before the ESO/cert-manager/Traefik CRDs exist. Mirrors the platform's own
  # apps.yaml (docs/29).
  dependsOn:
    - name: infrastructure-configs
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
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/audit: restricted
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
# GitRepository, both RoleBindings (admin + tenant-crd-editor), and the
# Kustomization (with its dependsOn) are identical to the 1P example above —
# the tenant's ExternalSecrets just point secretStoreRef at
# gitlab-friend-project. For a friend's own GitLab namespace, set the
# GitRepository url to https://git.ericsweiss.com/<group>/friend-project.
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
> Connect server and vault. Cross-store reference is no longer cooperative: both the
> platform store and the tenant example above declare `spec.conditions` listing the
> namespaces allowed to use them, so a tenant ExternalSecret pointed at
> `onepassword-homelab` is refused by ESO. `task flux:lint`
> (`scripts/check-secretstore-scope.py`) fails the build if a store loses its
> conditions or a consumer namespace is missing from them. What conditions do NOT
> give you is per-item scoping inside a vault — that still needs Options A/B in
> `docs/30-multi-repo-onboarding.md` (or a future admission controller).
>
> **Before onboarding the first tenant**: the Traefik CRD provider currently runs
> with `allowCrossNamespace: true` (see the accepted-risk comment in
> `kubernetes/infrastructure/controllers/traefik/release.yaml`), which lets any
> IngressRoute reference middlewares/services in other namespaces. Fine while
> every route is operator-authored; a tenant-authored IngressRoute could pull
> platform middlewares or another namespace's Service. Guard it first — scope the
> provider per-tenant, add a validating policy pinning `@namespace` refs, or
> revert to `allowCrossNamespace: false`.

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

Both of the tenant's RoleBindings live in the tenant namespace, so they are
pruned with it; the shared `tenant-crd-editor` ClusterRole is not per-tenant and
stays in place for the remaining tenants.

The `onepassword-connect-token` bootstrap secret is not Flux-managed; it is
deleted when the namespace is pruned. Revoke the Connect token in 1Password
and delete/archive the tenant's prefixed items in the `Homelab` vault.
