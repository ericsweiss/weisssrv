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

## Example: 1Password-backed tenant

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
#   TOKEN="$(op read 'op://Homelab-Example/SA-token/credential')"
#   kubectl -n example-app create secret generic onepassword-sdk-token \
#     --from-literal=token="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
#
# The 1P service account must be scoped to a dedicated vault for this tenant
# (e.g. Homelab-Example) so ExternalSecrets cannot reach the main Homelab
# vault's contents.
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
  # Private repo: create a deploy token in GitLab → add its secret to
  # flux-system namespace → reference here via secretRef.name.
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

> **Security note**: The `onepassword-homelab` ClusterSecretStore is cluster-wide — any namespace
> can create ExternalSecrets referencing it. For multi-tenant isolation, tenants should use
> per-namespace `SecretStore` resources with scoped 1Password service accounts rather than the
> shared ClusterSecretStore. The current model trusts all cluster workloads (appropriate for a
> single-operator homelab).

## Namespace ownership

Each tenant owns one dedicated namespace. Tenants MUST NOT create resources in
other tenants' namespaces or platform namespaces (`flux-system`,
`external-secrets`, `metallb-system`, `cert-manager`, `traefik`,
`external-dns`, `authentik`). Flux pruning would fight them.

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

The `onepassword-sdk-token` bootstrap secret is not Flux-managed; delete
separately: `kubectl delete secret onepassword-sdk-token -n <ns>`.
