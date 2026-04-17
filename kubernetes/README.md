# Kubernetes (Flux GitOps)

All Kubernetes state for the weisssrv k3s cluster lives in this tree and is
reconciled by Flux on every push. No `kubectl apply` / `helm upgrade` for
ongoing operations — edit YAML, commit, push, Flux reconciles within ~1m.

## Layout

```
kubernetes/
├── clusters/weisssrv/             # Flux entrypoint (written by `flux bootstrap`)
│   ├── flux-system/               # gotk-components.yaml + gotk-sync.yaml are managed by `flux bootstrap` — manual edits risk breaking reconciliation
│   ├── infrastructure-sources.yaml       # Flux Kustomization → ../../infrastructure/sources (no deps)
│   ├── infrastructure-controllers.yaml   # Flux Kustomization → ../../infrastructure/controllers (dependsOn: sources)
│   ├── infrastructure-configs.yaml       # Flux Kustomization → ../../infrastructure/configs (dependsOn: controllers)
│   ├── apps.yaml                  # Flux Kustomization → ../../apps (dependsOn: infrastructure-configs)
│   ├── kustomization.yaml         # Lists flux-system + the 4 top-level Kustomizations + tenants/
│   └── tenants/                   # One-file-per-tenant wiring (GitRepository + Kustomization + ClusterSecretStore)
│       └── README.md              # Onboarding examples
├── infrastructure/                # Platform (reconciled before apps)
│   ├── sources/                   # HelmRepository CRs + versions-configmap.yaml (needed by controllers)
│   ├── controllers/               # HelmReleases: external-secrets, metallb, cert-manager, traefik, external-dns
│   └── configs/                   # Cluster-wide CRs that require controllers' CRDs to exist first
│       ├── cluster-secret-store.yaml          # ESO CR
│       ├── cluster-issuer.yaml                # Let's Encrypt + Cloudflare DNS-01
│       ├── metallb-ip-pools.yaml              # .100 (public), .101 (internal)
│       ├── wildcard-certificates.yaml         # *.esweiss.com / *.ericsweiss.com (default ns)
│       ├── coredns/                           # HelmChartConfig override + PDB
│       ├── cloudflare-ddns/                   # CronJob + namespace
│       └── shared-cloudflare-secrets/         # ExternalSecrets for the Cloudflare token in 3 namespaces
└── apps/                          # Workloads
    ├── authentik/                 # SSO — HelmRelease + ExternalSecret + IngressRoutes + PG zvol PVC
    ├── download-clients/          # Media stack — Gluetun + *arr (Kustomize only)
    ├── recipes/                   # Mealie + Bar Assistant + Salt Rim (Kustomize only)
    ├── gitlab-runner/             # Shared HelmRelease
    ├── gitlab-runner-privileged/  # Infrastructure HelmRelease (shares `gitlab-runner` namespace)
    ├── gitlab-agent/              # GitLab Agent for Kubernetes (`weisssrv-k3s` release)
    └── vm-ingress/                # IngressRoutes for non-k8s VMs (plex, HA, adguard, router, GitLab VM)
```

## How it reconciles

1. `flux bootstrap` (one-time) installs the Flux controllers and commits
   `clusters/weisssrv/flux-system/` to `main`.
2. Flux's `source-controller` polls this repo (1m interval).
3. Flux's `kustomize-controller` reconciles `clusters/weisssrv/` →
   `infrastructure/` → `apps/` in dependency order.
4. `postBuild.substituteFrom: cluster-versions` substitutes `${var}`
   placeholders (e.g., `${authentik_version}`) from the `cluster-versions`
   ConfigMap, generated from `all.yml` via `task flux:sync-versions`.
5. Flux's `helm-controller` reconciles `HelmRelease` CRs (adopted or fresh).
6. External Secrets Operator syncs `ExternalSecret` → k8s `Secret` from
   1Password via the `onepassword-homelab` `ClusterSecretStore`.

## Operational commands

Core day-2 commands (full reference in `docs/29-flux-operations.md`):

```bash
task flux:status            # Concise health summary
task flux:verify            # flux check + full resource listing
task flux:reconcile         # Force source refresh + full reconcile
task flux:sync-versions     # Regenerate versions-configmap.yaml from all.yml
task flux:rotate-secret -- <app>    # Refresh ExternalSecret + restart consumers
task flux:refresh-secret -- <ns>/<name>   # Force one ExternalSecret to re-fetch
task flux:suspend -- <ns>/<kind>/<name>   # Emergency pause
task flux:resume -- <ns>/<kind>/<name>    # Un-pause
task flux:dev-apply -- <path>       # Local iteration (Flux reverts within 1 cycle)
task flux:lint              # kustomize build + kubeconform for infra/ and apps/
```

## Important IPs

- **API VIP**: 192.168.0.161 (kube-vip)
- **Public LoadBalancer**: 192.168.0.100 (MetalLB)
- **Internal LoadBalancer**: 192.168.0.101 (MetalLB)

## Cluster nodes

- **Servers (etcd quorum)**: k3s-srv-nas-01 (.222), k3s-srv-laptop-01 (.223), k3s-srv-prec-01 (.227)
- **Agents**: k3s-agt-nas-01 (.202), k3s-agt-laptop-01 (.203), k3s-agt-opt-01 (.204), k3s-agt-opt-02 (.205), k3s-agt-opt-03 (.206), k3s-agt-prec-01 (.207)

## Namespaces (by owner)

| Namespace | Owner | Purpose |
|---|---|---|
| `flux-system` | Flux | Controllers + GitRepository + Kustomizations |
| `external-secrets` | Flux (HelmRelease) | ESO controllers |
| `metallb-system` | Flux (HelmRelease) | MetalLB (privileged PSS — speaker needs CAP_NET_RAW) |
| `cert-manager` | Flux (HelmRelease) | cert-manager |
| `traefik` | Flux (HelmRelease) | Traefik ingress controller |
| `external-dns` | Flux (HelmRelease) | external-dns |
| `cloudflare-ddns` | Flux (Kustomize) | DDNS CronJob |
| `authentik` | Flux (HelmRelease) | Authentik SSO + bundled PostgreSQL |
| `downloads` | Flux (Kustomize) | Gluetun + *arr (privileged PSS — Gluetun needs CAP_NET_ADMIN) |
| `recipes` | Flux (Kustomize) | Mealie + Bar Assistant + Salt Rim + postgres + meilisearch + redis |
| `gitlab-runner` | Flux (HelmRelease) | Both shared and privileged runners live here |
| `gitlab-agent` | Flux (HelmRelease) | `weisssrv-k3s` agent for Kubernetes |
| `default` | Flux (Kustomize) | IngressRoutes for non-k8s VMs (via `apps/vm-ingress/`) |
| `gitlab` | Flux (Kustomize) | IngressRoutes for the GitLab VM (web + registry + pages) |

## Documentation

- **Flux operations**: `docs/29-flux-operations.md`
- **Multi-repo onboarding**: `docs/30-multi-repo-onboarding.md`
- **K3s deployment (underlying cluster)**: `docs/19-k3s-deployment.md`
- **Runbooks**: `docs/12-runbooks.md`
