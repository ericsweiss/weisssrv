# hindsight-reaper

A 6-hourly CronJob that deletes admission-rejected (`phase=Failed`) Hindsight
pods left behind by GPU-node reboots.

## Why this exists

After a GPU-node reboot the kubelet admits pods before the NVIDIA device plugin
has re-registered healthy GPUs, so the replacement pod is rejected at admission
and left in `phase=Failed`. The Deployment self-heals; nothing garbage-collects
the corpses, so they accumulate across reboots. This reaper is that sweep,
automated. Mechanism and the rejected alternatives:
`docs/43-gpu-passthrough.md` § Reaping admission-rejected pods.

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

See `docs/43-gpu-passthrough.md` § Reaping admission-rejected pods.
