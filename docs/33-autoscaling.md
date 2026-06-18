# Autoscaling

Vertical Pod Autoscaler (VPA) plus targeted HPA pins, layered on top of
hand-tuned requests for the stateful tier. Cluster-level (Proxmox) scaling
stays manual — see the last section for why.

## Components

- **VPA** (Fairwinds chart, `kubernetes/infrastructure/controllers/vpa/`):
  recommender + updater + admission controller in `vpa-system`. All three are
  pinned to `esweiss.com/cpu: modern` nodes (generic Go images SIGILL on the
  Core 2 Quad opt nodes — same constraint as ESO). The updater runs with
  `--min-replicas=1` because nearly every workload here is single-replica;
  without it `updateMode: Auto` never evicts anything.
- **Per-workload `VerticalPodAutoscaler` policies**, co-located with their
  workloads:
  - `kubernetes/infrastructure/configs/vpa/` — platform controllers + Flux
  - `kubernetes/infrastructure/observability/vpa.yaml` — observability stack
  - `kubernetes/apps/<app>/vpa.yaml` — applications
- **CoreDNS HPA pin** (`kubernetes/infrastructure/configs/coredns/hpa.yaml`):
  min=max=2. k3s manages CoreDNS as a wrangler AddOn and resets replicas on
  server restarts; the HPA scales it back within seconds.
- **Horizontal autoscaling for the stateless, HA-fronting tiers.** Prefer each
  chart's own autoscaling toggle over a standalone HPA — so the chart omits
  static `.spec.replicas` and nothing re-asserts a replica count against the HPA
  on a helm upgrade. For raw manifests and charts without a native HPA, use a
  standalone HPA and pin the chart/manifest replica count to the HPA's
  `minReplicas` (the static count equals the HPA floor, so it never flaps).
  Every workload below carries a **memory-only VPA** so CPU is owned solely by
  the HPA (the two must never drive the same resource — see the lint invariant
  in Operations).

  Chart-native HPA:
  - Traefik (`controllers/traefik/release.yaml`): min 2 / max 4 @ ~70% CPU.
  - authentik-server (`apps/authentik/release.yaml`): min 2 / max 4 @ ~75% CPU;
    the worker stays single-replica.
  - onepassword-connect (`controllers/onepassword-connect/release.yaml`,
    `connect.hpa`): min 2 / max 3 @ 80% CPU. The chart's HPA emits a memory
    metric by default; `avgMemoryUtilization: 0` disables it so it scales on CPU
    only (the Connect VPA owns memory). Stateless API proxy on the secrets
    fan-out path; max held at 3 for the tight-RAM hosts.

  Standalone HPA (chart/manifest lacks a native toggle; replica count pinned to
  the HPA floor):
  - salt-rim (`apps/recipes/hpa.yaml`): min 2 / max 4 @ 80% CPU. Static nginx
    frontend, configMap mount only, no PVC, DNS-only egress. Raw Deployment with
    no chart, so its `.spec.replicas` is dropped and a PDB (`minAvailable: 1`)
    plus soft pod anti-affinity were added so the replicas spread across hosts.

  **Not the external-secrets controller.** ESO does not shard ExternalSecrets
  across replicas — every replica reconciles the full set — so an HPA would not
  distribute load, only duplicate reconciles (and race on status writes). It runs
  a static 2 replicas (`controllers/external-secrets/release.yaml`
  `replicaCount: 2`) with `leaderElect: true`: one active reconciler plus a warm
  standby for instant failover, no HPA. A PDB (`minAvailable: 1`,
  `controllers/external-secrets/pdb.yaml`) keeps the reconcile path up during
  drains. With no HPA on the CPU axis, its VPA controls cpu+memory.

### HPA candidate classification

Workloads evaluated for an HPA and the verdict — so the analysis isn't
re-litigated. Rejections are about correctness (single-writer state, per-pod
sidecars, leader-elected singletons), not effort.

| Workload | NS | Verdict | Reason |
|---|---|---|---|
| traefik | traefik | HPA (chart) | stateless ingress, already done |
| authentik-server | authentik | HPA (chart) | stateless web tier, already done |
| onepassword-connect | external-secrets | **HPA (chart)** | stateless API proxy, fan-out bursts |
| external-secrets controller | external-secrets | reject | doesn't shard ExternalSecrets — extra replicas only duplicate reconciles; 2 static + leader election for HA |
| salt-rim | recipes | **HPA (standalone)** | stateless nginx, configMap only, no PVC |
| mealie / bar-assistant | recipes | reject | RWO PVC + single-writer DB (NFS app-data / SQLite) |
| meilisearch / bar-redis / postgres | recipes | reject | stateful (RWO data / cache) |
| *arr stack, nzbget, qbittorrent | downloads | reject | RWO config + per-pod gluetun VPN killswitch sidecar |
| authentik-worker | authentik | reject | Celery-style task worker, deliberately single-replica |
| gitlab-runner(-privileged) | gitlab-runner | reject | runner *manager*; concurrency is spawned job pods |
| gitlab-agent | gitlab-agent | reject | idle KAS long-poll, already 2 replicas, no CPU pressure |
| cert-manager / external-dns / metallb-controller | various | reject | leader-elected singletons, no concurrency gain |
| ESO webhook / cert-controller | external-secrets | reject | admission/cert paths, already 2 replicas, no benefit |
| exporters / kube-state-metrics | observability | reject | 1:1 scrape mapping; a second replica breaks dedup |
| Prometheus / Loki / Alertmanager / Postgres | various | reject | stateful (StatefulSet / zvol) |
| coredns | kube-system | n/a | min==max==2 HPA pin (replica anchor, not an autoscaler) |

## Update-mode tiers

| Mode | Used for | Behavior |
|---|---|---|
| `Auto` | exporters (proxmox, blackbox, plex, redis, exportarr, zfs, adguard, unbound), MetalLB, cert-manager, ESO, Connect, Traefik, alloy, node-exporter, kube-state-metrics, kps operator | updater evicts to apply new requests (brief restart) |
| `Initial` | apps (downloads incl. the gluetun sidecars caught by wildcard `*` policies, recipes incl. bar-assistant redis/meilisearch/salt-rim, authentik server/worker, runners, agent), external-dns (single replica, no PDB), Flux controllers, Grafana | new requests apply only when the pod restarts naturally — no surprise evictions mid-download or mid-reconcile |
| `Off` | Prometheus, Alertmanager, Loki, both PostgreSQLs | recommendation-only; requests stay hand-tuned in the HelmRelease/manifest (zvol-pinned, eviction-sensitive) |

The rows above are representative, not exhaustive — the canonical coverage
lives in the VPA policy files under `kubernetes/infrastructure/configs/vpa/`
(platform + Flux; `platform.yaml`, `flux-system.yaml`),
`kubernetes/infrastructure/observability/vpa.yaml`, and
`kubernetes/apps/<app>/vpa.yaml`. Audit live coverage with
`kubectl get vpa -A`.

Every policy carries `minAllowed`/`maxAllowed` caps so a recommendation
can't starve or balloon a workload.

The update mode above is independent of which resources a policy controls. Any
workload that also has an HPA carries a **memory-only** VPA
(`controlledResources: [memory]`) regardless of its update-mode tier — Traefik
and Connect (`Auto`), authentik-server and salt-rim (`Initial`) all follow this.
The lint invariant below enforces it. ESO has no HPA (it doesn't horizontally
scale — see "Not the external-secrets controller" above), so its VPA controls
cpu+memory.

## Operations

```bash
kubectl get vpa -A                          # recommendations + targets
kubectl describe vpa <name> -n <ns>         # full recommendation detail
kubectl -n vpa-system logs deploy/vpa-updater | grep -i evict
kubectl get hpa -A                          # HPA min/max + current target/replicas
```

Under normal load every HPA should sit at its `minReplicas` (matches how
traefik/authentik idle at 2). If a new HPA pins to max at idle, its CPU request
is too small relative to the threshold — raise the request, not the threshold
(the authentik fix pattern), so utilization tracks real load instead of noise.

`task flux:lint` runs `scripts/check-hpa-vpa-invariant.py --require-chart-native-vpas`
over the full rendered manifest set: it fails if any workload has an HPA and a
(mutating) VPA controlling the same resource. This guards the central failure
mode — a VPA evicting pods to resize CPU while an HPA scales on CPU thrashes. The
generic join sees standalone HPAs (salt-rim, the coredns pin) directly. The
chart-native HPAs (Traefik, authentik-server, Connect) live inside HelmReleases
the lint doesn't expand, but their paired VPAs *are* in the corpus, so
`--require-chart-native-vpas` statically asserts each of those workloads has a
VPA that excludes cpu (its `CHART_NATIVE_HPA_TARGETS` list is kept in sync with
the HelmReleases that enable chart-native HPAs).

Apply an `Off`-tier recommendation by editing the workload's resources in
git (the recommendation is the data, the HelmRelease stays the source of
truth).

## Hand-tuned baselines (VPA Off tier)

Set from observed working sets (2026-06): Prometheus 2Gi request / 4Gi
limit at 365d retention; Grafana 512Mi/1Gi; Loki 512Mi/1Gi; Flux
controllers 256Mi requests (patched in
`kubernetes/clusters/weisssrv/flux-system/kustomization.yaml`).

## Proxmox-level scaling (manual by design)

- VM allocations are inventory-pinned (`hosts.yml`); there is no API-driven
  node autoscaler and adding one isn't worth it for 6 fixed hosts.
- Headroom (2026-06): pve-nas-01 ~20G free, pve-opt-01/02 ~5-9G, pve-prec-01
  ~9G. pve-laptop-01 (15G, two 6G k3s VMs + dns-01 + smtp-relay) and
  pve-opt-03 (14G, HAOS + agent + dns-02) are the tight hosts — grow agent
  VMs on the roomy hosts first if k8s requests start failing to schedule.
- The 2026-06-11 laptop agent memory-wedge was unbounded pod memory, not VM
  sizing; VPA + request coverage is the fix, not more RAM.
