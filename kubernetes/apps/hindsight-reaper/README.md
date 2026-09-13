# hindsight-reaper

A 6-hourly CronJob that deletes admission-rejected (`phase=Failed`) Hindsight
pods left behind by GPU-node reboots.

## Why this exists

Hindsight's `llama` sidecar requests `nvidia.com/gpu`, satisfiable only on the
single GPU agent (`pve-prec-01`). When that node reboots — kured coordinated
reboots, driver reloads — the kubelet starts admitting pods **before** the NVIDIA
device plugin has re-registered healthy GPUs. A replacement pod scheduled in that
window is rejected at admission:

```
Status:  Failed
Reason:  UnexpectedAdmissionError
Message: Pod was rejected: Allocate failed due to no healthy devices present;
         cannot allocate unhealthy devices nvidia.com/gpu, which is unexpected
```

The Deployment **recovers correctly** — once the GPU is healthy a fresh pod is
admitted and runs. The problem is only cleanup: the ReplicaSet controller never
deletes pods it owns, and cluster pod-GC fires only past a high cluster-wide
threshold (`--terminated-pod-gc-threshold`, default 12500), so the rejected pods
accumulate in `Failed` across reboots until swept by hand. This reaper is that
sweep, automated.

## What it does

Lists `Failed`-phase pods carrying `app.kubernetes.io/name=hindsight` in the
`hindsight` namespace and deletes those older than `MIN_AGE_MINUTES` (default 30
— a margin so a pod the controllers are still reconciling this instant is never
touched). It **never** selects a `Running`/`Pending` pod, so the live replica is
structurally safe, and it **keeps** Failed pods whose reason is `Evicted` or
`OOMKilled` — a resource-pressure eviction on this memory-constrained GPU node is
evidence to investigate, not a corpse to sweep. The program is
`hindsight-reaper.py`; unit tests are `scripts/test_hindsight_reaper.py`.

## Design

- **Dedicated namespace** so the `netpol-egress-{dns,apiserver}` components — the
  reaper's whole egress — select it alone, without loosening hindsight's own
  tight per-pod NetworkPolicy.
- **Namespaced Role** (`pods: list, delete`) in the `hindsight` namespace bound
  to the reaper's ServiceAccount: pod-delete is confined to `hindsight` at the
  RBAC layer, no cluster-wide pod access anywhere. No `get` — the script lists
  and deletes by name.
- **Digest-pinned `python:3.14-slim`** (the image the runner reaper and
  cloudflare-ddns already run) — stdlib only, no new image or version-pin.
- **uid precondition** on every delete, so a stale list cannot delete a newer pod
  that reused the name.
- The Job **exits non-zero on any non-race list/delete error**, so a broken RBAC
  or API outage surfaces as a failed Job (`KubeJobFailed`) instead of a silent
  no-op.

## Alternatives considered

- **Prevent the race** (rather than clean up after it) would need the node to
  withhold GPU pods until the device plugin reports healthy — i.e. the full
  NVIDIA **GPU Operator** with its startup-taint machinery, or a custom
  taint controller. That is a large dependency for one homelab GPU; the recovery
  already works, so reaping the corpses is the right-sized fix.
- **Lower `--terminated-pod-gc-threshold`** cluster-wide would auto-GC Failed
  pods everywhere, but it is a global k3s control-plane change that also makes
  terminated pods disappear faster in every namespace (worse for debugging). The
  scoped reaper is more surgical.

See `docs/43-gpu-passthrough.md` § Reaping admission-rejected pods.
