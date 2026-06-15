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
- **Horizontal autoscaling for the stateless, HA-fronting tiers**, via each
  chart's own `autoscaling.enabled` rather than a standalone HPA — so the chart
  omits static `.spec.replicas` and nothing re-asserts a replica count against
  the HPA on a helm upgrade:
  - Traefik (`controllers/traefik/release.yaml`): min 2 / max 4 @ ~70% CPU.
  - authentik-server (`apps/authentik/release.yaml`): min 2 / max 4 @ ~75% CPU;
    the worker stays single-replica. Both carry memory-only VPAs so CPU is
    owned solely by the HPA.

## Update-mode tiers

| Mode | Used for | Behavior |
|---|---|---|
| `Auto` | exporters (proxmox, blackbox, plex, redis, exportarr, zfs, adguard, unbound), MetalLB, cert-manager, external-dns, ESO, Connect, Traefik, alloy, node-exporter, kube-state-metrics, kps operator | updater evicts to apply new requests (brief restart) |
| `Initial` | apps (downloads incl. the gluetun sidecars caught by wildcard `*` policies, recipes incl. bar-assistant redis/meilisearch/salt-rim, authentik server/worker, runners, agent), Flux controllers, Grafana | new requests apply only when the pod restarts naturally — no surprise evictions mid-download or mid-reconcile |
| `Off` | Prometheus, Alertmanager, Loki, both PostgreSQLs | recommendation-only; requests stay hand-tuned in the HelmRelease/manifest (zvol-pinned, eviction-sensitive) |

The rows above are representative, not exhaustive — the canonical coverage
lives in the VPA policy files under `kubernetes/infrastructure/configs/vpa/`
(platform + Flux; `platform.yaml`, `flux-system.yaml`),
`kubernetes/infrastructure/observability/vpa.yaml`, and
`kubernetes/apps/<app>/vpa.yaml`. Audit live coverage with
`kubectl get vpa -A`.

Every policy carries `minAllowed`/`maxAllowed` caps so a recommendation
can't starve or balloon a workload.

## Operations

```bash
kubectl get vpa -A                          # recommendations + targets
kubectl describe vpa <name> -n <ns>         # full recommendation detail
kubectl -n vpa-system logs deploy/vpa-updater | grep -i evict
```

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
