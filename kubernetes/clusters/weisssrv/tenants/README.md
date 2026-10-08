# Tenant wiring for the weisssrv k3s cluster

Each external repo that deploys workloads to this cluster gets a single YAML
file in this folder AND a matching entry in `kustomization.yaml` (the file
itself is not auto-discovered — Kustomize requires explicit resource
listings). The tenant file defines the tenant's `ClusterSecretStore` (1P or
GitLab variables — namespace-scoped by `spec.conditions`), Flux `GitRepository` source, and
top-level `Kustomization`. One file per tenant keeps ownership clear and
makes removal trivial (delete the file, remove the entry from
`kustomization.yaml`, push — Flux prunes the resources it created).

The tenant-side repo is generated from the **copier template**
`eric/weisssrv-app-template` (`copier copy https://git.ericsweiss.com/eric/weisssrv-app-template <dir>`)
— it ships the lint/kubeconform/secret-scan pipeline and, in its own
`docs/ONBOARDING.md`, a pre-rendered copy of the wiring file below with the
tenant's real slug and namespace. Deploys happen cluster-side via Flux, not CI.
Tenants can instead hand-author their `kubernetes/` tree following the patterns
in this repo. A hand-authored tree MUST add what the template ships for free:
a namespace-wide ingress default-deny NetworkPolicy plus a scrape-allow from
`observability`. It is mandatory in every namespace here, but
`scripts/check-default-deny-coverage.py` reads only this repo's rendered
corpus, so a tenant namespace is invisible to it — see
`docs/30-multi-repo-onboarding.md` § Pre-Onboarding Checklist.

Merge the wiring file only once the tenant repo carries a real image tag. The
app template ships `:REPLACE-ME`, which holds the Deployment at zero available
replicas, and the tenant's own `AppDown` rule is evaluated by this cluster's
Prometheus, so the critical page lands in this Alertmanager ten minutes later.

Cap the tenant namespace too. The wiring file below ships a `ResourceQuota` and
a `LimitRange` because `admin` puts no ceiling on requests, and the two runner
quotas already sum above cluster allocatable (docs/33 § Scheduling priority). Pick
the numbers per tenant.

A tenant volume is outside every backup set until the operator adds it, and the
operator owns its snapshot and offsite schedule (docs/42).

See `docs/30-multi-repo-onboarding.md` for the full onboarding procedure.

`tenant-crd-editor.yaml` in this folder is a shared `ClusterRole` (always
applied, harmless while unbound) that every tenant wiring file binds alongside
the built-in `admin` ClusterRole — `admin` does not cover the `traefik.io`,
`monitoring.coreos.com`, or `autoscaling.k8s.io` CRD groups a tenant app uses.
See the RBAC comment in the example below.

## Platform contract

What a tenant's manifests bind to on this cluster, beyond the CRDs the
`dependsOn` chain waits for:

- **The Traefik `websecure` entryPoint.** Every tenant IngressRoute must name
  it (`kubernetes/infrastructure/controllers/traefik/release.yaml`).
- **external-dns, pinned to the `external-dns.alpha.kubernetes.io/` annotation
  prefix** (`kubernetes/infrastructure/controllers/external-dns/release.yaml`).
  A tenant emitting `external-dns.alpha.kubernetes.io/target` gets no public
  record if that prefix ever moves, with its pods ready and no alert firing.
- **Prometheus discovers rules and monitors in every namespace.** The
  kube-prometheus-stack release sets `ruleSelectorNilUsesHelmValues: false`,
  `serviceMonitorSelectorNilUsesHelmValues: false` and the matching
  `*NamespaceSelector: {}`
  (`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`),
  so a tenant PrometheusRule or ServiceMonitor is selected without a release
  label. On the chart defaults both are ignored silently.
- **A public route has no path condition.** It matches `Host(...)` alone, so a
  tenant whose ServiceMonitor scrapes the same Service port its IngressRoute
  backends publishes `/metrics` to the internet. Expose metrics on a second
  containerPort and a second Service port, point the ServiceMonitor endpoint
  and the scrape-allow NetworkPolicy at that port, or gate the path at the
  route.
- **blackbox-exporter probes a static target list**
  (`kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml`).
  A tenant's own PrometheusRule covers its replicas and its certificates, so to
  catch a broken IngressRoute or DNS record while replicas stay ready, add the
  tenant hostname to that list at onboarding.

## Private tenant repositories

Flux authenticates per `GitRepository`, through a Secret named by
`spec.secretRef.name`. That Secret must live in the same namespace as the
GitRepository, `flux-system` here, because `secretRef` is namespace-local. It is
bootstrap state, not Flux-managed: it is what Flux needs in order to read git,
so it cannot itself come from git.

A deploy key is scoped to one repository and revocable there:

```bash
flux create secret git example-app-git-auth \
  --namespace=flux-system \
  --url=ssh://git@git.ericsweiss.com/<group>/example-app \
  --ssh-key-algorithm=ecdsa --ssh-ecdsa-curve=p521
```

Add the printed public key to the tenant repo as a read-only deploy key. Flux
never pushes to a tenant repo. Then set `url: ssh://...` with the `.git` suffix
and `secretRef.name` on the GitRepository. A project access token works the same
way with `--username=git --password=<TOKEN>` and an `https://` url. Rotating the
credential is re-running the command; nothing in git changes.

The Secret carries no ownership marker, so add `flux-system/<name>` to the
`ALLOWLIST` in `scripts/check-unmanaged-secrets.py` in the same change that
creates it.

A tenant repository need not live on this cluster's forge. Flux clones over
HTTPS or SSH, so a tenant can sit on GitHub while this cluster sits on
`git.ericsweiss.com`. Write the tenant's own URL rather than copying the
example's.

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
# Cap the namespace: `admin` places no ceiling on what a tenant may request.
# `pods:` must exceed the replica count, which surges by one on rollout, and
# `limits.memory` must cover that many per-pod limits, at the VPA maxAllowed
# where the tenant ships one.
apiVersion: v1
kind: ResourceQuota
metadata:
  name: example-app-quota
  namespace: example-app
spec:
  hard:
    pods: "10"
    requests.cpu: "2"
    requests.memory: 4Gi
    limits.memory: 8Gi
---
# Defaults and per-container ceilings, so a pod with no resources block cannot
# land unbounded inside the quota. A `max:` key with no matching `default:` key
# becomes the default limit, so CPU is left to the quota's requests.cpu.
apiVersion: v1
kind: LimitRange
metadata:
  name: example-app-limits
  namespace: example-app
spec:
  limits:
    - type: Container
      default:
        memory: 512Mi
      defaultRequest:
        cpu: 50m
        memory: 128Mi
      max:
        memory: 2Gi
---
# One-time bootstrap (NOT managed by Flux), re-runnable for a rotated token:
#   kubectl -n example-app create secret generic onepassword-connect-token \
#     --from-literal=token=<CONNECT_TOKEN> \
#     --dry-run=client -o yaml | kubectl apply -f -
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
# CRITICAL: `admin` does not aggregate the platform CRD groups, so without the
# shared `tenant-crd-editor` ClusterRole bound here the tenant Kustomization
# goes NotReady on its first IngressRoute/ServiceMonitor/PrometheusRule/VPA.
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
  # Optional, and only if the tenant's manifests use `${...}` placeholders:
  # substituting from cluster-config lets a tenant spell this cluster's domains
  # and VIPs without hard-coding them, exactly as the platform Kustomizations do.
  # cluster-versions is deliberately NOT listed: a tenant pins its own image
  # versions rather than tracking this cluster's.
  postBuild:
    substituteFrom:
      - kind: ConfigMap
        name: cluster-config
        optional: false
```

`cluster-config` lives in `flux-system`, the same namespace a tenant
Kustomization is created in, so the reference resolves. kustomize-controller
reads them with its own client — `serviceAccountName` governs what the tenant may
APPLY, not what it may substitute from — so a tenant can read this cluster's
identity even though its SA is confined to its own namespace. Exposing that is a
deliberate choice: the values are hostnames and VIPs the tenant's own routes
already carry.

kustomize-controller runs with `StrictPostBuildSubstitutions=true`, so every
`${...}` a tenant manifest carries must resolve from `cluster-config` or the
tenant's whole reconcile fails. Escape a literal as `$${...}`.

The `infrastructure-configs` stage name in `dependsOn` is a contract every
tenant depends on. Rename or remove that Kustomization and each tenant stalls
quietly: kustomize-controller retries "dependency not found", `wait: true`
applies nothing, and the only signal is `FluxResourceNotReady`, fifteen minutes
later. `scripts/check-tenant-wiring.py` holds every tenant file to that name.

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
# One-time bootstrap, re-runnable for a rotated token: the friend creates a
# Personal Access Token in GitLab with read_api scope, then:
#   kubectl -n friend-project create secret generic gitlab-api-token \
#     --from-literal=token=glpat-... \
#     --dry-run=client -o yaml | kubectl apply -f -
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: gitlab-friend-project
spec:
  # Same cluster-scoped exposure as the 1P store above: without conditions any
  # namespace can name this store and read the friend's CI/CD variables.
  conditions:
    - namespaces:
        - friend-project
  provider:
    gitlab:
      # Optional, and DEFAULTS TO https://gitlab.com — omit it and a
      # self-hosted project token is sent to the wrong instance.
      url: https://git.ericsweiss.com
      projectID: "<NUMERIC_PROJECT_ID>"  # from GitLab > Settings > General
      auth:
        # Capital S is the real CRD field; a lower-cased `secretRef` is
        # pruned as unknown, leaving `auth` with no credential.
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

**Never add a tenant namespace to the platform store.**
`kubernetes/infrastructure/configs/cluster-secret-store.yaml` admits only the
platform namespaces it names, and every one of them can mint any item in the
`Homelab` vault. Adding a tenant namespace there is a one-line handover of the
runner tokens, the Authentik keys and the SMTP credentials; the tenant's
`admin` RoleBinding then lets it create ExternalSecrets at will, and RBAC in
its own namespace cannot take the boundary back. Give the tenant its own store,
as the examples above do.

Three things every wiring file above carries, and every new one must:

- **The namespace caps** — a `ResourceQuota` and a `LimitRange`, because
  `admin` places no ceiling on what a tenant may request. The LimitRange sets
  no CPU key: the apiserver copies a `max` entry with no matching `default`
  into `default`, which would CFS-throttle every tenant container.
- **PSA labels on the tenant Namespace** — `enforce: baseline` plus `warn` and
  `audit: restricted`, so the platform enforces the non-root, read-only-rootfs
  posture the tenant template ships.
- **`spec.conditions` on the tenant `ClusterSecretStore`** — a
  ClusterSecretStore is cluster-scoped, so without conditions any namespace can
  name it and read its vault. Scope it to the tenant namespace alone.
  `scripts/check-tenant-wiring.py` (`task lint`) fails the build when a tenant
  store loses its conditions or reaches past its namespace. The corpus gates
  under `task flux:lint`, `scripts/check-secretstore-scope.py` included, cover
  the platform stores only: they read this repo's rendered corpus, which does
  not include this folder.

Conditions scope the store, not the vault: in the default Option C model every
tenant shares the `Homelab` vault, so a tenant ExternalSecret can mint any item
in it. Stronger isolation (per-tenant vault, per-tenant Connect server) and the
Traefik `allowCrossNamespace` guard that must land before the first tenant
reconciles are both in `docs/30-multi-repo-onboarding.md` § Pre-Onboarding
Checklist and § Isolation Options.

## Namespace ownership

Each tenant owns exactly one namespace, and enforcement is RBAC rather than
convention: the tenant Kustomization sets `serviceAccountName`, and that SA's
RoleBindings (`admin` + `tenant-crd-editor`) are namespace-scoped, so a
manifest targeting another namespace fails to apply. See
`docs/30-multi-repo-onboarding.md` § Namespace Isolation for the platform
namespace list and for what stays cooperative outside the apply path.

**A tenant PrometheusRule must scope its own expressions.** Prometheus runs
without `enforcedNamespaceLabel` — deliberately, because the platform's own rules
span namespaces — so a tenant `expr` selecting only on `deployment="x"` evaluates
cluster-wide. That mis-fires on any like-named workload, and it silently DISABLES
an `absent()` arm the moment any other namespace has a match. Every tenant rule
carries `namespace="<slug>"` in the selector, not only in the alert labels.

## Removal

Delete `tenants/<slug>.yaml` and the matching line in `kustomization.yaml`,
then commit. Flux prunes the Kustomization, the GitRepository, the
`ClusterSecretStore` and the namespace. Prune order, what survives, and the
manual token revocation are in `docs/30-multi-repo-onboarding.md` § Removal.
