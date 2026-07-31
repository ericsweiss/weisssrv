# Kubernetes (Flux GitOps)

All Kubernetes state for the weisssrv k3s cluster lives in this tree and is
reconciled by Flux on every push. No `kubectl apply` / `helm upgrade` for
ongoing operations — edit YAML, commit, push; the GitLab agent's Flux module
triggers reconciliation on push (the ~1m git poll is the fallback).

## Layout

```
kubernetes/
├── clusters/weisssrv/             # Flux entrypoint (written by `flux bootstrap`)
│   ├── flux-system/               # gotk-components.yaml + gotk-sync.yaml are managed by `flux bootstrap` — manual edits risk breaking reconciliation
│   ├── infrastructure-sources.yaml       # Flux Kustomization → ../../infrastructure/sources (no deps)
│   ├── infrastructure-crds.yaml          # Flux Kustomization → ../../infrastructure/crds (dependsOn: sources, wait: true)
│   ├── infrastructure-controllers.yaml   # Flux Kustomization → ../../infrastructure/controllers (dependsOn: sources + crds)
│   ├── infrastructure-configs.yaml       # Flux Kustomization → ../../infrastructure/configs (dependsOn: controllers)
│   ├── infrastructure-observability.yaml # Flux Kustomization → ../../infrastructure/observability (dependsOn: configs)
│   ├── apps.yaml                  # Flux Kustomization → ../../apps (dependsOn: infrastructure-configs)
│   ├── kustomization.yaml         # Lists flux-system + the 6 top-level Kustomizations + tenants/
│   └── tenants/                   # One-file-per-tenant wiring (GitRepository + Kustomization + ClusterSecretStore)
│       └── README.md              # Onboarding examples
├── infrastructure/                # Platform (reconciled before apps)
│   ├── sources/                   # HelmRepository CRs + versions-configmap.yaml (needed by controllers)
│   ├── crds/                      # prometheus-operator CRDs, installed before any controller renders a ServiceMonitor
│   ├── controllers/               # Platform HelmReleases — see controllers/kustomization.yaml for the current set
│   ├── configs/                   # Cluster-wide CRs requiring the controllers' CRDs — see configs/kustomization.yaml
│   └── observability/             # kube-prometheus-stack, Loki, Alloy, exporters, ServiceMonitors, dashboards, ingress
├── components/                    # Reusable Kustomize components pulled in via `components:` in an app's kustomization.yaml
│   ├── netpol-baseline/           # default-deny-ingress — MANDATORY for every app (docs/29 § Adding a New App)
│   └── gitlab-runner-common/      # Shared runner values/objects for the two runner releases
└── apps/                          # Workloads — one dir per app; see apps/kustomization.yaml for the current set
```

## How it reconciles

1. `flux bootstrap` (one-time) installs the Flux controllers and commits
   `clusters/weisssrv/flux-system/` to `main`.
2. Flux's `source-controller` fetches this repo — push-triggered via the
   GitLab agent's Flux module, with a 1m poll as fallback.
3. Flux's `kustomize-controller` reconciles in dependency order: `sources` →
   `crds` → `controllers` → `configs`, then `observability` and `apps` in
   parallel (apps deliberately do not gate on observability health).
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

## Cluster topology

Node-by-node list (3 servers forming the etcd quorum + 6 agents) and the VIPs
(API .161 via kube-vip; MetalLB .100 public / .101 internal / .99 wg-easy) live in
[docs/01-overview.md](../docs/01-overview.md) (canonical).

## Namespaces (by owner)

| Namespace | Owner | Purpose |
|---|---|---|
| `flux-system` | Flux | Controllers + GitRepository + Kustomizations |
| `external-secrets` | Flux (HelmRelease) | ESO controllers |
| `metallb-system` | Flux (HelmRelease) | MetalLB (privileged PSS — speaker needs CAP_NET_RAW) |
| `cert-manager` | Flux (HelmRelease) | cert-manager |
| `traefik` | Flux (HelmRelease) | Traefik ingress controller |
| `external-dns` | Flux (HelmRelease) | external-dns |
| `vpa-system` | Flux (HelmRelease) | Vertical Pod Autoscaler (docs/33) |
| `reloader` | Flux (HelmRelease) | Reloader — rolls workloads on ConfigMap changes only (Secrets excluded via `ignoreSecrets: true`) |
| `kube-system` | k3s (+ Flux HelmRelease for kured) | k3s built-ins + kured reboot coordinator |
| `kube-node-lease`, `kube-public` | k3s | Cluster built-ins, nothing deployed into them |
| `cloudflare-ddns` | Flux (Kustomize) | DDNS CronJob |
| `authentik` | Flux (HelmRelease) | Authentik SSO + bundled PostgreSQL |
| `downloads` | Flux (Kustomize) | Gluetun + *arr (privileged PSS — Gluetun needs CAP_NET_ADMIN) |
| `recipes` | Flux (Kustomize) | Mealie + Bar Assistant + Salt Rim + postgres + meilisearch + redis |
| `gitlab-runner` | Flux (HelmRelease) | Shared (unprivileged, tag `k8s-deploy`) runner |
| `gitlab-runner-privileged` | Flux (HelmRelease) | Infrastructure (privileged, tag `infrastructure`) runner |
| `gitlab-runner-reaper` | Flux (Kustomize) | CronJob that GCs leaked runner pods + dockercfg Secrets |
| `gitlab-agent` | Flux (HelmRelease) | `weisssrv-k3s` agent for Kubernetes |
| `observability` | Flux (HelmRelease + Kustomize) | kube-prometheus-stack, Loki, Alloy, exporters, dashboards |
| `prometheus-operator-crds` | Flux (HelmRelease) | The `monitoring.coreos.com` CRDs (`infrastructure-crds` stage) |
| `hermes` | Flux (Kustomize) | Hermes agent + dashboard + camofox (docs/37) |
| `hindsight` | Flux (Kustomize) | Hermes' memory backend + llama.cpp GPU sidecar, no ingress (docs/37) |
| `homarr` | Flux (Kustomize) | Homarr dashboard (docs/41) |
| `registry-cache` | Flux (Kustomize) | Pull-through registry cache for CI (docs/27) |
| `wg-easy` | Flux (Kustomize) | wg-easy WireGuard VPN (docs/38) |
| `tailnet-dns` | Flux (Kustomize) | Tailnet-facing DNS forwarder |
| `tailscale` | Flux (HelmRelease) | tailscale-operator |
| `nvidia-device-plugin` | Flux (HelmRelease) | Time-sliced GPU device plugin (docs/43) |
| `default` | Flux (Kustomize) | IngressRoutes for non-k8s VMs (via `apps/vm-ingress/`) |
| `gitlab` | Flux (Kustomize) | IngressRoutes for the GitLab VM (web + registry + pages) |

## Documentation

- **Flux operations**: `docs/29-flux-operations.md`
- **Multi-repo onboarding**: `docs/30-multi-repo-onboarding.md`
- **K3s deployment (underlying cluster)**: `docs/19-k3s-deployment.md`
- **Observability**: `docs/31-observability.md`
- **Runbooks**: `docs/12-runbooks.md`
