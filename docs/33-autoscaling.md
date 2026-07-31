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
- **metrics-server is the dependency this whole subsystem rests on.** The
  k3s-bundled metrics-server (kube-system, single replica) supplies the metrics
  every HPA (Traefik, authentik-server, Connect, salt-rim, the CoreDNS pin) and
  the VPA recommender read. It is therefore a single point of failure: if its one
  pod OOMs or can't schedule, every HPA goes stale (holds its last replica count,
  can't scale) and the recommender stops getting data. Like CoreDNS it is a k3s
  AddOn (k3s reverts Auto-applied changes on reconcile), so it carries a
  **recommend-only VPA** (`updateMode: Off`, `configs/vpa/platform.yaml`) rather
  than an Auto one. The durability fix — raise it to 2 replicas and add a memory
  limit via a k3s `HelmChartConfig` override — is a k3s/Ansible change tracked in
  [docs/16-next-steps.md](./16-next-steps.md) (Deferred from the 2026 comprehensive
  review).
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
  - Traefik (`controllers/traefik/release.yaml`): min 2 / max 6 @ ~70% CPU.
    Its CPU request was raised 100m → 250m per this doc's "raise the request,
    not the threshold" rule: at 100m the 70% target sat at 70m (noise level),
    so every traffic burst pinned the HPA at maxReplicas and fired
    KubeHpaMaxedOut; at 250m idle reads ~3% and scaling tracks real load. The
    request bump removed idle-noise triggering but not genuine CI image-pull
    spikes, so a scaleUp/scaleDown `behavior` block (stabilization windows + a
    1-pod step policy, like authentik-server) was added as the second lever.
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
| grafana | observability | reject | single-writer SQLite on an NFS-backed RWX PV; not horizontally safe (carries an Initial VPA) |
| coredns | kube-system | n/a | min==max==2 HPA pin (replica anchor, not an autoscaler) |
| metrics-server / kube-vip / kured | various | n/a | k3s/infra add-ons, not application workloads (metrics-server is the HPA/VPA dependency — see Components). metrics-server and kube-vip still carry `Off` VPAs to record a right-sizing signal; kured has none |

## CPU limits (intentionally unset)

Workloads set CPU **requests** and **memory** limits but no **CPU limit**. CPU is
compressible — under contention the scheduler shares it by request weight, so a
limit adds nothing but CFS throttling, which hurts tail latency and, worse,
inflates the CPU% a CPU-based HPA reads (it was firing `CPUThrottlingHigh`
cluster-wide and pushing the Traefik HPA toward `maxReplicas` on load that wasn't
real). Memory stays limited because it is incompressible — its failure mode is
OOM, not throttling.

VPAs keep their default `controlledValues: RequestsAndLimits`. A VPA scales an
*existing* limit — one present in the **live pod spec** — to preserve its
request:limit ratio, but never *adds* a limit that is not there (verified on the
runner managers: no CPU limit declared, none imposed live), so memory limits keep
tracking the recommendation while CPU stays limit-free. "Live pod spec" is not
the same as "rendered manifest" — see [Live drift](#live-drift-git-says-no-cpu-limit-the-cluster-disagrees)
below. The subtlety:
"rendered" includes **chart-default limits a Helm chart injects even when the
values file sets none** — node-exporter's chart still rendered a ~110m CPU limit,
so its `RequestsAndLimits` VPA kept *re-imposing* that CPU limit (and
`CPUThrottlingHigh`) until it was switched to `RequestsOnly`
(`observability/vpa.yaml`, the worked example). This is also why a values-only
lint can miss a CPU limit (it never sees the chart default). `RequestsOnly` is the
targeted fix for such charts; it is deliberately **not** a blanket setting — on a
`[cpu,memory]` VPA it would freeze the memory limit too (an OOM risk).

Removing the CPU limit moves these pods from Guaranteed to Burstable QoS; with
memory requests sized to the working set they remain eviction candidates only
when exceeding that request, which the sizing avoids.

`scripts/check-hpa-vpa-invariant.py --require-chart-native-vpas` (run by
`task flux:lint` and CI) fails if a CPU limit appears in a rendered pod spec, in
a HelmRelease values block, or inside a config-file block string carried in those
values (the gitlab-runner `runners.config` TOML, where every CI **job pod's**
limits are declared — those pods exist in no manifest). Intentional exceptions go
in that script's `CPU_LIMIT_ALLOWLIST` (currently empty).
`scripts/validate-helm-values.py` reuses the same scanner over `helm template`
output for the value-heavy releases, which is the only way to see a CPU limit a
chart *default* injects.

The same script also fails a container that sets `requests.memory ==
limits.memory` while a mutating VPA controls its memory with the default
`controlledValues` — the ratio-preserving rewrite that produced the prowlarr
OOMKills and left authentik-server at request == limit == 878Mi. It sees only
pod specs in the kustomize corpus (a chart-rendered spec is invisible to it), so
`VPARecommendationExceedsLimit` is the runtime backstop for the rest.

### Live drift: git says no CPU limit, the cluster disagrees

Those checks prove the policy holds **in git**. They cannot prove it holds in the
cluster, and for months it did not: all four Flux controllers and the gitlab-agent
Deployment ran with a `limits.cpu` that no manifest declares. Cause in both cases
is server-side apply — the pre-migration bootstrap field manager (`flux` for
flux-system, `helm` for gitlab-agent, both dated 2026-04-16) still **co-owns**
`f:resources.f:limits.f:cpu`, and a controller dropping a field from its *own*
fieldset cannot delete a field another manager owns. The removal patch at
`kubernetes/clusters/weisssrv/flux-system/kustomization.yaml` therefore rendered
correctly and changed nothing live. The VPA then made it worse rather than
better: with `RequestsAndLimits` it scaled the *surviving* limit down in lockstep
with each request revision, so helm-controller ended up at a 250m limit against a
197m peak (79%) with measurable CFS throttling on the GitOps engine itself.

`task flux:verify` now runs `scripts/check-live-cpu-limits.py` over live pods to
catch exactly this. It **warns** there rather than failing (`flux:verify` is the
post-deploy/DR gate and must be able to go green on a healthy cluster; it also
runs the secret-ownership check alongside, and a stop-on-first-error would have
masked one behind the other). `task flux:verify-cpu-limits` runs the same check
standalone and exits non-zero — that is the one to use when confirming the fix.

Remediation is a one-time field release per workload (it must be done once;
nothing in the reconcile loop can do it):

```bash
# Inspect the owners first — the retired manager is the one listing f:cpu:
kubectl -n flux-system get deploy helm-controller --show-managed-fields -o json \
  | jq '.metadata.managedFields[] | {manager, resources: (.fieldsV1 | .. | .["f:resources"]? // empty)}'

# Release it (repeat for kustomize-, source-, notification-controller):
kubectl -n flux-system patch deploy helm-controller --type=json \
  -p '[{"op":"remove","path":"/spec/template/spec/containers/0/resources/limits/cpu"}]'

# gitlab-agent carries the same drift from the pre-Flux `helm install`:
kubectl -n gitlab-agent patch deploy weisssrv-k3s-gitlab-agent-v2 --type=json \
  -p '[{"op":"remove","path":"/spec/template/spec/containers/0/resources/limits/cpu"}]'

kubectl get pods -A -o json | python3 scripts/check-live-cpu-limits.py   # must exit 0
```

A `kubectl patch` on a Flux-managed object is normally forbidden here; this is
the documented exception, because the field is not one Flux owns — releasing it
is what lets Flux's rendered state become the effective state.

## Update-mode tiers

| Mode | Used for | Behavior |
|---|---|---|
| `Auto` | exporters (proxmox, blackbox, plex, redis, exportarr, zfs, adguard, unbound, dcgm), metallb-controller, cert-manager (controller + cainjector), ESO, Connect, alloy, node-exporter, kube-state-metrics, kps operator | updater evicts to apply new requests (brief restart) |
| `Initial` | **Traefik** (moved from Auto — see below), metallb-speaker + cert-manager-webhook (host-network / admission paths), apps (downloads incl. the gluetun sidecars caught by wildcard `*` policies, recipes incl. bar-assistant redis/meilisearch/salt-rim, authentik server/worker, runners, agent), external-dns (single replica, no PDB), tailscale-operator, Flux controllers, Grafana | new requests apply only when the pod restarts naturally — no surprise evictions mid-download or mid-reconcile |
| `Off` | Prometheus, Alertmanager, Loki, both PostgreSQLs (the Prometheus/Alertmanager VPAs target the operator CRs, not the StatefulSets — see docs/31), **Hindsight**, coredns/metrics-server/kube-vip (k3s add-ons) | recommendation-only; requests stay hand-tuned in the HelmRelease/manifest (zvol-pinned, eviction-sensitive). Hindsight stays `Off` because its llama container is GPU-pinned (`nvidia.com/gpu`) and its memory is VRAM/model-dictated, not usage-history driven (docs/43) |

**Traefik is `Initial`, not `Auto`** (changed after the ingress-churn
incident): Traefik is the ingress data path for every service, including the
container registry. Under CI-burst load the Auto updater evicted Traefik to
apply a new memory request, and each replacement pod's startup readiness gap
surfaced as transient 502s on `registry.git.ericsweiss.com` (ImagePullBackOff
in CI), `git.esweiss.com` hangs, and Unhealthy events — a PDB doesn't help
because the disruption is the replacement pod's own readiness gap. `Initial`
still right-sizes on natural restarts (chart upgrades, node drains) without
the updater ever evicting the data path. Rationale comment lives in
`configs/vpa/platform.yaml`.

The rows above are representative, not exhaustive — the canonical coverage
lives in the VPA policy files under `kubernetes/infrastructure/configs/vpa/`
(platform + Flux; `platform.yaml`, `flux-system.yaml`),
`kubernetes/infrastructure/observability/vpa.yaml`, and
`kubernetes/apps/<app>/vpa.yaml`. Audit live coverage with
`kubectl get vpa -A`.

Every policy carries `minAllowed`/`maxAllowed` caps so a recommendation
can't starve or balloon a workload. A per-container `mode: "Off"` is the one
exception — it suppresses that container's recommendation entirely, so there is
nothing to cap (hermes/camofox, hermes/init-data, hindsight/llama).

### VPA blind spots (sized by hand, on purpose)

Two classes of workload no VPA can target, so their numbers are hand-set and
must be re-measured when the workload changes:

- **Operator-generated StatefulSets** — the tailscale operator names its proxies
  `ts-<svc>-<hash>` and the hash changes whenever the exposure Service is
  recreated, so no static `targetRef` survives. Sizing lever is the ProxyClass
  (`controllers/tailscale-operator/proxyclass.yaml`), which applies to every
  proxy it creates. Size it against the observed **peak**, not the steady state:
  the tsnet process resides at ~50Mi but peaks at ~124Mi.
- **Ansible-rendered cluster add-ons** — kube-vip's DaemonSet comes from
  `roles/k3s/templates/kube-vip-manifest.yaml.j2`, not from `kubernetes/`. It
  carries an `Off`-mode VPA in `configs/vpa/platform.yaml` purely to record the
  signal; applying it means editing the Jinja template and re-running the k3s
  play.

The update mode above is independent of which resources a policy controls. Any
workload that also has an HPA carries a **memory-only** VPA
(`controlledResources: [memory]`) regardless of its update-mode tier — Connect
(`Auto`), Traefik, authentik-server, and salt-rim (`Initial`) all follow this.
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
(the authentik and Traefik fix pattern), so utilization tracks real load
instead of noise. Request-sizing stops idle-noise triggering but not genuine
transient spikes: if a bursty HPA still flaps to max, add an HPA `behavior`
block (`scaleUp`/`scaleDown` `stabilizationWindowSeconds` + a Pods step policy
of 1) as the second lever — both authentik-server and Traefik carry one — so a
short burst needs sustained pressure before a pod is added.

### Recommendation metrics + ceiling alert

kube-state-metrics exports the VPA recommendations via its
`customResourceState` config (kube-prometheus-stack `release.yaml`) as
`vpa_recommendation_target` and `vpa_recommendation_uncappedtarget` — the
capped and uncapped recommendation per container/resource. The
**`VPARecommendationCapped`** alert fires when `uncappedtarget > target` for
24h: the recommendation has been clamped by `maxAllowed` for a full day, i.e.
the workload has outgrown its ceiling (a brief clamp during a burst is
expected and does not fire). Response: raise the `maxAllowed` cap in the
policy file (`infrastructure/configs/vpa/` etc.) or investigate the growth.
For a `RequestsOnly` VPA whose `maxAllowed.memory` equals the container's
memory limit (e.g. the gluetun-exporter sidecar, both 48Mi), raising
`maxAllowed` alone clears the alert — it reads the VPA CR status target, which
is capped only by the policy — but the admission controller still holds the
applied request at the container limit, so the real memory ceiling is
unchanged. If the growth is genuine, raise the container's memory limit in
lockstep with `maxAllowed`.

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

That loop is manual, so it can silently stop being run: authentik-postgresql's
recorded target sat at 684Mi against a 512Mi limit for weeks and then OOMKilled
the SSO database. Two alerts now close it:

- **`VPARecommendationExceedsLimit`** — the VPA's memory target has been above
  the container's configured memory limit for 6h. Scoped to `Off`-tier and
  `controlledValues: RequestsOnly` VPAs (kube-state-metrics exports both through
  the `update_mode` / `controlled_values` labels), because those are the ones
  where nothing else ever moves the limit; a mutating VPA on the default
  `RequestsAndLimits` re-scales its own limit at the next admission and would
  otherwise page for hours on a condition it fixes itself. Response: apply the
  recommendation in git.
  (Distinct from `VPARecommendationCapped`, which is about the *policy's*
  `maxAllowed` clamping the recommendation, not the *container's* limit.)
- **`ContainerOOMKilled`** — the kill itself. Upstream's rules only catch a
  container that stays down or crash-loops, so a clean OOM-and-restart was
  invisible.

Both are unit-tested in `scripts/prometheus-rule-tests/memory-sizing.test.yaml`.

## Hand-tuned request baselines

Set from observed working sets (2026-06). The `Off`-tier (recommendation-only)
workloads keep these hand-tuned numbers permanently: Prometheus 2Gi request / 4Gi
limit at 365d retention; Loki 512Mi/1Gi; authentik-postgresql 640Mi/1Gi (raised
2026-07 from the recorded 684Mi target after the 512Mi limit OOMKilled it —
the worked example of applying an `Off`-tier recommendation). The `Initial`-tier workloads start from
these baselines but let the VPA right-size them on the next natural restart:
Grafana 512Mi/1Gi; Flux controllers 256Mi requests (patched in
`kubernetes/clusters/weisssrv/flux-system/kustomization.yaml`).

## Proxmox-level scaling (manual by design)

- VM allocations are inventory-pinned (`hosts.yml`); there is no API-driven
  node autoscaler and adding one isn't worth it for 6 fixed hosts.
- Headroom (`node_memory_MemAvailable_bytes`, 2026-07 with pve-laptop-01 out of
  the fold for hardware work, so its guests are running elsewhere): pve-nas-01
  12.4G, pve-prec-01 13.9G, pve-opt-01 5.1G, pve-opt-02 5.6G, pve-opt-03 5.8G.
  The three opt nodes are the tight hosts — grow agent VMs on pve-nas-01 or
  pve-prec-01 first if k8s requests start failing to schedule. Re-measure before
  acting: the split moves several GiB whenever a host leaves the fold.
- The 2026-06-11 laptop agent memory-wedge was unbounded pod memory, not VM
  sizing; VPA + request coverage is the fix, not more RAM.
