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
- **metrics-server is the dependency this whole subsystem rests on**, and it is
  Flux-managed here (`kubernetes/infrastructure/controllers/metrics-server/`),
  not k3s-packaged. It supplies the metrics every HPA (Traefik,
  authentik-server, Connect, salt-rim, the CoreDNS pin) and the VPA recommender
  read, so a single pod that OOMs or cannot schedule leaves every HPA stale
  (holding its last replica count) and stops the recommender's input. k3s ships
  it as a **static AddOn** whose raw manifests a `HelmChartConfig` cannot patch —
  fixed at one replica with no memory limit — so the servers run
  `--disable=metrics-server` (`group_vars/k3s.yml`) and the HelmRelease owns it:
  2 replicas, a PDB (`minAvailable: 1`), hard anti-affinity across nodes,
  `system-cluster-critical`, and a 96Mi/192Mi request/limit sized off the 30d
  peak. It keeps a **recommend-only VPA** (`updateMode: Off`,
  `configs/vpa/platform.yaml`, `maxAllowed` tracking that limit) — an eviction
  would blind the autoscaling stack, so the recommendation is data for a manual
  bump.
  **The cutover has landed.** The packaged AddOn owned the
  `v1beta1.metrics.k8s.io` APIService and the kube-system objects under Rancher
  objectset annotations that Helm cannot adopt, so the HelmRelease could not
  install until `--disable=metrics-server` was deployed. Two properties made the
  handover safe without ordering discipline, and both still apply to a rebuild:
  - `install.remediation.retries: -1` on the HelmRelease — unlimited retries, so
    it installs itself on the first attempt after k3s deletes the AddOn's
    objects. `flux reconcile helmrelease metrics-server -n kube-system` only
    skips the wait; no failure-counter reset is ever needed.
  - metrics-server has **its own Flux Kustomization**
    (`clusters/weisssrv/infrastructure-metrics-server.yaml`) instead of sitting
    in the `wait: true` controllers stage, so the retry loop cannot make
    `infrastructure-controllers` not-Ready and freeze configs, observability and
    apps behind it. Nothing `dependsOn` it: HPAs and the recommender read
    metrics.k8s.io at runtime, never at apply time.
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
| metrics-server / kube-vip / kured | various | n/a | platform components, not application workloads. metrics-server already runs 2 replicas + a PDB from its own HelmRelease (the HPA/VPA dependency — see Components); it and kube-vip carry `Off` VPAs to record a right-sizing signal; kured has none |
| vpa-recommender / -updater / -admission-controller | vpa-system | n/a | the autoscaler itself — a VPA here would have the updater evicting the admission controller that mutates its replacement, so all three stay unmanaged and hand-sized in `controllers/vpa/release.yaml`. The admission controller runs 2 replicas + a PDB instead: it is on the admission path for every pod in the cluster |
| nvidia-device-plugin | nvidia-device-plugin | n/a | fixed-footprint DaemonSet on the one GPU node; carries an `Off` VPA to record a right-sizing signal |

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
the same as "rendered manifest" — see [Live drift](#live-drift-a-limit-git-does-not-declare)
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

There is **no** static gate for the mirror-image shape — a container setting
`requests.memory == limits.memory` while a mutating VPA controls its memory with
the default `controlledValues`, the ratio-preserving rewrite that produced the
prowlarr OOMKills and left authentik-server at request == limit == 878Mi. That
check belongs in `check-hpa-vpa-invariant.py`, which is **vendored** from
weisssrv-lib (`scripts/README.md` § Origin), so it lands upstream and re-vendors
here. Until it does, `VPARecommendationExceedsLimit` and
`ContainerMemoryNearLimit` are the only guard, not a backstop.

### Live drift: a limit git does not declare

The checks above prove the policy holds **in git**; they cannot prove it holds in
the cluster. Server-side apply makes that a real gap: if a retired field manager
still co-owns `f:resources.f:limits.f:cpu`, a controller dropping the field from
its *own* fieldset cannot delete it, so a removal patch renders correctly and
changes nothing live. A mutating VPA then compounds it — with
`RequestsAndLimits` it scales the surviving limit down in lockstep with each
request revision, which is how a control-plane pod ends up CFS-throttled against
a limit no manifest declares.

**Detector**: `scripts/check-live-cpu-limits.py`. `task flux:verify` runs it over
live pods and **warns** (that task is the post-deploy/DR gate and must be able to
go green on a healthy cluster; it also runs the secret-ownership check, and
stop-on-first-error would mask one behind the other). `task flux:verify-cpu-limits`
runs the same check standalone and exits non-zero — use that when confirming a fix.

### Live drift: a limit git declares but the pod never got

The mirror image, and the more common one. A mutating VPA rewrites resources at
**pod admission**, so a pod carries the request/limit pair it was admitted with
for its whole lifetime. Commit a new limit — or flip `controlledValues` — on an
`Initial`-tier (or low-churn `Auto`-tier) VPA and Flux applies it to the
*template* while every running pod keeps the old numbers. external-dns ran for
days at a 99Mi live ceiling against a declared 256Mi for exactly this reason: its
pods were admitted under the previous `RequestsAndLimits` shape, which had
ratio-scaled the limit down with the request, and nothing re-admitted them.

**So a commit that changes a memory limit or `controlledValues` needs a manual
rollout restart in the same change window**, e.g.
`kubectl -n external-dns rollout restart deploy/external-dns`. It is a restart,
not a patch: Flux's rendered state is already correct, the pods are simply older
than it.

**Detector**: the same `check-live-cpu-limits.py`, given the workload templates
alongside the pods — it compares each live container's memory limit with the
limit its owning Deployment/StatefulSet/DaemonSet declares and warns on
divergence (`kubectl get
pods,deployments,statefulsets,daemonsets,verticalpodautoscalers -A -o json`).
Pass the VPAs as shown: where an **active** VPA controls a container's limits
(`controlledValues: RequestsAndLimits`, which is also the API default when the
field is unset), the updater ratio-scales that limit at every admission, so
divergence there is the design rather than a finding — those containers are
excluded and counted in the summary line instead. Drift is a warning, never an
exit code: it self-clears on the next restart, and the restart is what the
operator has to schedule.

**Fix**: release the field once, per workload. Nothing in the reconcile loop can
do it.

```bash
# Identify the stale owner — it is the manager whose fieldset lists f:cpu:
kubectl -n <ns> get deploy <name> --show-managed-fields -o json \
  | jq '.metadata.managedFields[] | {manager, resources: (.fieldsV1 | .. | .["f:resources"]? // empty)}'

# Release it:
kubectl -n <ns> patch deploy <name> --type=json \
  -p '[{"op":"remove","path":"/spec/template/spec/containers/0/resources/limits/cpu"}]'

kubectl get pods -A -o json | python3 scripts/check-live-cpu-limits.py   # must exit 0
```

A `kubectl patch` on a Flux-managed object is normally forbidden here. This is
the documented exception: the field is not one Flux owns, and releasing it is
what lets Flux's rendered state become the effective state.

## Update-mode tiers

| Mode | Used for | Behavior |
|---|---|---|
| `Auto` | exporters (proxmox, blackbox, plex, redis, exportarr, zfs, adguard, unbound, dcgm), cert-manager (controller + cainjector), ESO, Connect, alloy, node-exporter, kube-state-metrics, kps operator | updater evicts to apply new requests (brief restart) |
| `Initial` | **Traefik** (moved from Auto — see below), the MetalLB controller and speaker + cert-manager-webhook (host-network / admission paths), every app VPA except the three `Off` ones below — downloads (incl. the gluetun sidecars caught by wildcard `*` policies), recipes, authentik server + worker, homarr, hermes, registry-cache, tailnet-dns, wg-easy, runners, agent, external-dns (single replica, no PDB), tailscale-operator, Flux controllers, Grafana | new requests apply only when the pod restarts naturally — no surprise evictions mid-download or mid-reconcile. The flip side: a workload that never restarts can sit under-sized for months, which is why `VPARecommendationExceedsLimit` covers this tier |
| `Off` | Prometheus, Alertmanager, Loki, both PostgreSQLs (the Prometheus/Alertmanager VPAs target the operator CRs, not the StatefulSets — see docs/31), **Hindsight**, coredns (k3s AddOn) / metrics-server / kube-vip | recommendation-only; requests stay hand-tuned in the HelmRelease/manifest (zvol-pinned, eviction-sensitive). Hindsight stays `Off` because its llama container is GPU-pinned (`nvidia.com/gpu`) and its memory is VRAM/model-dictated, not usage-history driven (docs/43) |

**Traefik is `Initial`, not `Auto`.** Traefik is the ingress data path for every
service, including the container registry. Under CI-burst load the `Auto` updater
evicts it to apply a new memory request, and each replacement pod's startup
readiness gap surfaces as transient 502s (`registry.git.ericsweiss.com`,
`git.esweiss.com`). A PDB does not help — the disruption *is* the replacement
pod's own readiness gap. `Initial` still right-sizes on natural restarts (chart
upgrades, node drains) without the updater ever evicting the data path.

**The tier table is representative, not exhaustive.** The live set is dozens of
VPAs across many namespaces. Canonical coverage lives in the policy files —
`kubernetes/infrastructure/configs/vpa/{platform,flux-system}.yaml`,
`kubernetes/infrastructure/observability/vpa.yaml`, and
`kubernetes/apps/<app>/vpa.yaml` — and `kubectl get vpa -A` is the only reliable
audit of what is live.

Every policy carries `minAllowed`/`maxAllowed` caps so a recommendation
can't starve or balloon a workload. A per-container `mode: "Off"` is the one
exception — it suppresses that container's recommendation entirely, so there is
nothing to cap (hermes/camofox, hermes/init-data, hindsight/llama).

### Limit oscillation (why memory VPAs are `controlledValues: RequestsOnly`)

On the default `controlledValues: RequestsAndLimits` the updater rescales the
memory **limit** with every recommendation, keeping the original
request:limit ratio. A quiet period therefore shrinks the ceiling, and the next
burst runs against a limit sized for the quiet period — measured over 7d before
the fix: external-dns fell to a 128Mi limit and peaked at 0.97 of it; pulsarr's
limit moved five times (down to 783Mi against a 903Mi 30d working-set peak) and
peaked at 0.96; external-secrets took eight limit revisions, and because that VPA
is `Auto`, each one is an eviction of the cluster's secrets reconciler.
onepassword-connect, cert-controller and the ESO webhook oscillated 9x/7x/7x in
the same window.

hermes/dashboard is the case that stopped being theoretical: idle-heavy and
interactive, it held a flat ~138Mi for 26 hours, so the recommender sized the
request at 194Mi and the 4:1 manifest ratio pulled the limit down to 777Mi — a
23% cut to the declared 1Gi. The first active chat session drove it ~5x past
that and it was OOMKilled twice in five minutes, dropping every open chat and
event stream. The `Initial` tier makes this worse rather than safer: a
recommendation applies only at pod admission, but an OOM restarts the
*container* in place, so the shrunken limit is frozen for the pod's lifetime
and the workload cannot recover on its own. Any interactive workload whose
quiet baseline is a small fraction of its working peak belongs in the
`RequestsOnly` set for this reason, not just one whose limit is seen moving.

The fix is not a bigger cap — it is taking the limit away from the VPA. Where
`controlledValues: RequestsOnly` is set, the limit is hand-set in the manifest at
the **30d working-set peak +60%** and `maxAllowed.memory` **tracks that limit
exactly**. The cap only ever bounds the *request*, so at the ceiling the pod is
admitted against it yields request == limit — Guaranteed QoS, not an OOM risk,
because the limit itself no longer moves.

**The cap rule is scoped to what the policy controls:**

| Policy shape | `maxAllowed.memory` |
|---|---|
| `controlledValues: RequestsOnly`, mode `Auto`/`Initial` | **== the container's limit** (lower only with a measured reason) |
| limit-controlling (`RequestsAndLimits` or unset), mode `Auto`/`Initial` | **~0.8x the limit, never equal** — the updater rescales the limit with the request, so a cap at or above it makes the declared ceiling meaningless |
| `updateMode: Off` (or per-container `mode: "Off"`) | exempt — nothing applies the recommendation; `== the limit` records growth to the binding ceiling (hindsight) |

**Above the limit is always wrong**, in every shape. Raising a limit means
re-deriving its cap, in the same commit.

prowlarr is the one measured exception to the RequestsOnly row: its cap sits at
384Mi under a 512Mi limit because request == limit was the shape that OOMKilled
it on search/backup spikes, and 384Mi clears its 257Mi 30d peak. nzbget and
qbittorrent's non-`*` sidecars are the other below-limit caps, for the
page-cache reason in § VPARecommendationCapped.

Like the CPU-limit case above, this is a **targeted** setting rather than a
blanket one: a policy joins the set when its limit is measured oscillating (or
when a chart injects a limit it must stop re-imposing). alloy and homarr are the
most recent joiners: they were running on live limits ~4.8x and ~2.3x their
declared ones, so the ceiling a pod admitted *without* the mutating webhook
(which is `failurePolicy: Ignore`) would have received sat below their measured
peak, and their limits were re-derived in the same commit. Flipping one lowers
its effective ceiling from "whatever the updater rescaled it to" down to the
manifest limit, which is only safe once that limit has been re-measured — so
cert-manager, cert-manager-cainjector, cert-manager-webhook, reloader and
tailscale-operator stay on `RequestsAndLimits`, with their
`configs/vpa/platform.yaml` caps at 0.8x the limit their HelmRelease sets rather
than above it.

The rule is machine-checked: `scripts/check-hpa-vpa-invariant.py`
(`task flux:lint`) fails a `maxAllowed.memory` above any judged container's
limit, and one exactly at the limit under a limit-controlling policy. It reads
container limits from the rendered corpus and honours exact-name container
policies over `"*"`, so a sidecar-sized helper is judged against its own entry —
which is why the download clients' one-shot init containers carry
`mode: "Off"`. The script is **vendored from weisssrv-lib**, so changing the
gate is a library MR, not a change here;
`scripts/autoscaling-policy.yaml`'s `vpa_cap_allowlist` (empty by design) is the
only local escape hatch.

The *arrs, recipes and the small exporters were re-derived under the table
above. What the gate cannot see stays manual: policies whose target's limits
never enter the kustomize corpus — the flux-system controllers (upstream
`gotk-components.yaml` limits), both gitlab-runners and grafana — are still
carried on the roadmap in `docs/16-next-steps.md`.

Side effect worth knowing: `VPARecommendationExceedsLimit` is scoped to `Off`,
`Initial` and `RequestsOnly` shapes, so moving an `Auto` VPA to `RequestsOnly`
pulls it INTO that alert's scope. That is the point — an `Auto`
`RequestsAndLimits` VPA is excluded precisely because it papers over the
condition by moving the limit.

## Scheduling priority

`kubernetes/infrastructure/sources/priorityclasses.yaml` defines the two classes
this cluster has to separate:

| Class | Value | Preemption | Applied to |
|---|---|---|---|
| `platform` | 100000 | PreemptLowerPriority | Every platform controller — the table below is the canonical list. It is not inherited: each workload names it in its own pod spec, which for a HelmRelease means a `priorityClassName` value |
| `ci-jobs` | -10 | Never | GitLab CI job pods, via `[runners.kubernetes] priority_class_name` in both runner TOMLs |

### Where `platform` is applied

Each chart spells the key differently, and several have no global form, so the
value path is part of the record. All of them were confirmed against the
rendered chart output, not assumed — `task flux:lint` re-renders every release
in this table through `scripts/validate-helm-values.py`.

| Release | Value path | Covers |
|---|---|---|
| cert-manager | `global.priorityClassName` | controller, webhook, cainjector |
| external-dns | `priorityClassName` | the controller |
| external-secrets | `priorityClassName`, `webhook.priorityClassName`, `certController.priorityClassName` | all three components (no global key) |
| onepassword-connect | `connect.priorityClassName` | the Connect api+sync pod (`operator.create: false`, so the operator key is moot) |
| traefik | `priorityClassName` | the ingress Deployment |
| metallb | `controller.priorityClassName`, `speaker.priorityClassName` | both (no global key) |
| vpa | `priorityClassName` | recommender, updater, admission-controller; the certgen Jobs stay unclassed |
| reloader | `reloader.deployment.priorityClassName` | the controller |
| alloy | `controller.priorityClassName` | the DaemonSet |
| loki | `global.priorityClassName` | the singleBinary StatefulSet (and the gateway, if ever enabled) |
| kube-prometheus-stack | `prometheusOperator.priorityClassName`, `prometheus.prometheusSpec.priorityClassName`, `alertmanager.alertmanagerSpec.priorityClassName` | operator, Prometheus, Alertmanager. `prometheusOperator.priorityClassName` is templated by the chart but absent from its `values.yaml` — verified by render. Grafana, kube-state-metrics and node-exporter are subcharts and stay unclassed |

Deliberately **not** given `platform`, because they already carry a higher
built-in class and setting it would be a downgrade: **metrics-server**
(`system-cluster-critical`), **kured** (`system-node-critical`), **tailnet-dns**
(`system-cluster-critical`) and the gotk controllers (`system-cluster-critical`,
shipped in the upstream manifest). Also excluded by design: the GitLab runners
(they keep `ci-jobs`), one-off Jobs, and everything under `kubernetes/apps/`.

The negative value is the load-bearing half: every unclassed pod sits at 0 and so
outranks a CI job without needing a class of its own, and `preemptionPolicy:
Never` makes a CI burst queue rather than displace anything.

It treats a symptom. The **ceiling** is the two runner ResourceQuotas, which
between them admit 38 + 8 = 46 cores of CPU requests against 31 allocatable —
measured peak requests reached 38.9 cores with 19 pods Pending over 30d, and
`KubeCPUOvercommit` deliberately excludes the runner namespaces
(`kubernetes-resources.yaml`), so nothing pages on it. Lowering a quota is the
change that removes the overcommit; the priority classes only decide who waits.

### VPA blind spots (sized by hand, on purpose)

Two classes of workload no VPA can target, so their numbers are hand-set and
must be re-measured when the workload changes:

- **Operator-generated StatefulSets** — the tailscale operator names its proxies
  `ts-<svc>-<hash>` and the hash changes whenever the exposure Service is
  recreated, so no static `targetRef` survives. Sizing lever is the ProxyClass
  (`controllers/tailscale-operator/proxyclass.yaml`), which applies to every
  proxy it creates. Size it against the observed **peak**, not the steady state:
  the tsnet process resides at ~50Mi but peaks at ~124Mi.
- **Ansible-rendered cluster add-ons** — kube-vip's DaemonSet comes from the
  `weisssrv.infra` k3s role's `kube-vip-manifest.yaml.j2` template
  (weisssrv-lib), not from `kubernetes/`. It carries an `Off`-mode VPA in
  `configs/vpa/platform.yaml` purely to record the signal; applying it means a
  library MR against that template, a pin bump here, and re-running the k3s
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
capped and uncapped recommendation per container/resource. The alerts that
consume them live under `kubernetes/infrastructure/observability/rules/`
(`VPARecommendation*` in `infrastructure.yaml`, `ContainerOOMKilled` in
`kubernetes-resources.yaml`). The
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

### Applying an Off-tier recommendation

Apply an `Off`-tier recommendation by editing the workload's resources in
git (the recommendation is the data, the HelmRelease stays the source of
truth). Where the workload's VPA has a `maxAllowed` tracking its memory limit,
raise both in the same commit — a raised limit with an unchanged cap just moves
the clamp.

That loop is manual, so it can silently stop being run: authentik-postgresql's
recorded target sat at 684Mi against a 512Mi limit for weeks and then OOMKilled
the SSO database. Three alerts now close it:

- **`VPARecommendationExceedsLimit`** — the VPA's memory target has been above
  the container's configured memory limit for 6h. Scoped to `Off`- and
  `Initial`-tier VPAs and to `controlledValues: RequestsOnly`
  (kube-state-metrics exports both through the `update_mode` /
  `controlled_values` labels). `Auto` is excluded because a mutating VPA on the
  default `RequestsAndLimits` re-scales its own limit at the next admission and
  would otherwise page for hours on a condition it fixes itself. `Initial` is
  **included**: "the next admission fixes it" can be months away on a workload
  that never restarts. Response: apply the recommendation in git.
  (Distinct from `VPARecommendationCapped`, which is about the *policy's*
  `maxAllowed` clamping the recommendation, not the *container's* limit.)
- **`ContainerMemoryNearLimit`** — the leading indicator the other two lack:
  live working set above 90% of the container's own memory limit for 15m,
  independent of any VPA (the recommender lags a burst by hours). Three
  containers are excluded because their steady state legitimately sits near the
  ceiling: `hindsight/llama` (GPU/model-pinned, deliberate `Off` VPA — docs/43)
  and observability's `prometheus` and `loki`, whose working set counts
  reclaimable page cache.
- **`ContainerOOMKilled`** — the kill itself. Upstream's rules only catch a
  container that stays down or crash-loops, so a clean OOM-and-restart was
  invisible.

All three are unit-tested in `scripts/prometheus-rule-tests/memory-sizing.test.yaml`.

## Hand-tuned request baselines

Set from observed working sets. The `Off`-tier (recommendation-only)
workloads keep these hand-tuned numbers permanently: Prometheus 4608Mi request /
6Gi limit (retention is bounded by `retentionSize: 110GB`, with 365d as the outer
bound); Loki 768Mi/1Gi; authentik-postgresql 640Mi/1Gi (raised
from a 512Mi limit that OOMKilled it — the worked example of applying an
`Off`-tier recommendation). The `Initial`-tier workloads start from
these baselines but let the VPA right-size them on the next natural restart:
Grafana 512Mi/1Gi; Flux controllers 256Mi requests (patched in
`kubernetes/clusters/weisssrv/flux-system/kustomization.yaml`).

A `RequestsOnly` policy keeps its **limit** hand-tuned permanently even though
its request is still VPA-managed, with `maxAllowed.memory` tracking that limit
(§ Limit oscillation): hermes/dashboard 768Mi request / 2560Mi limit (cap
2560Mi) and hermes/gateway 512Mi/2Gi (cap 2Gi), both floored by `minAllowed`
at the interactive working set rather than the idle baseline, alongside the
VPA-excluded hermes/camofox at 1Gi/3Gi.

Page-cache-dominated workloads are capped, not recommended. nzbget writes
through a cgroup that accounts reclaimable file-backed cache (1.4Gi working set
against a 30Mi RSS on qbittorrent), so an uncapped recommendation walks to the
ceiling and reserves GiBs the process never touches: its `maxAllowed.memory`
(1Gi) sits far under the 4Gi container limit and it is expected to sit in
`VPARecommendationCapped`.

## Proxmox-level scaling (manual by design)

- VM allocations are inventory-pinned (`hosts.yml`); there is no API-driven
  node autoscaler and adding one isn't worth it for 6 fixed hosts.
- **The three opt nodes and pve-laptop-01 are the tight hosts** — the laptop
  carries two k3s VMs (a server and an agent, 5 GiB each) on a ~15 GiB host, so
  it runs at least as close to the edge as the opt hosts. Grow agent VMs on pve-nas-01 or
  pve-prec-01 first if k8s requests start failing to schedule. Absolute headroom
  numbers are deliberately not recorded here — they move by several GiB whenever
  a host leaves or rejoins the fold. Measure before acting:
  `node_memory_MemAvailable_bytes` per host, with every host in the fold.
- An agent memory wedge is almost always unbounded pod memory rather than VM
  sizing; VPA plus request coverage is the fix, not more RAM.

## Related documentation

- [docs/31-observability.md](31-observability.md) — metrics, alerts and the VPA/HPA dashboards
- [docs/29-flux-operations.md](29-flux-operations.md) — how these manifests reconcile
- [docs/43-gpu-passthrough.md](43-gpu-passthrough.md) — why the GPU workloads are VPA-exempt
- [docs/06-zfs.md](06-zfs.md) — the NAS memory budget these sizings live inside
