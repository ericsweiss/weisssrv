# hindsight

[Hindsight](https://github.com/vectorize-io/hindsight) (vectorize-io) —
long-term agent memory (knowledge graph, entity resolution, observation
consolidation) serving as **Hermes' memory backend** (docs/37 §Memory backend).

- **Workload**: one Deployment, three containers, mirroring upstream's
  `vectorize-io/hindsight/docker/docker-compose/local-llm/` example:
  - `hindsight` — the published standalone image (API `:8888`), embedded pg0
    PostgreSQL on the NFS volume.
  - `llama` — the official llama.cpp server image as an OpenAI-compatible
    sidecar (Gemma 4 E2B Q4 GGUF, auto-downloaded once, cached on NFS).
    Fully local: no LLM API key, no per-turn spend. The published hindsight
    image deliberately omits `llama-cpp-python`, so the in-process
    `HINDSIGHT_API_LLM_PROVIDER=llamacpp` mode does NOT work with it — the
    sidecar is upstream's recommended shape.
  - `pg-dump` — nightly `pg_dump` of pg0 to NFS `/backups-apps/hindsight`.
    It is a sidecar because pg0 listens on pod-localhost only, so nothing
    outside this pod's network namespace can reach it.
- **Consumers**: only the `hermes` namespace (ClusterIP
  `hindsight.hindsight.svc.cluster.local:8888`) + the Prometheus scraper.
  No ingress route, no cert, no external DNS — this is cluster-internal.
- **Scheduling**: hard-pinned to `k3s-agt-prec-01` — `nodeAffinity` on that
  hostname plus `esweiss.com/gpu=nvidia`, `runtimeClassName: nvidia` and the
  llama container's `nvidia.com/gpu: 1` request, which only the passed-through
  GTX 1660 Ti satisfies (docs/43). That makes prec-01 a hard availability
  dependency: if it is down Hindsight is down and Hermes falls back to its
  built-in memory. Requests and limits for each container are in
  `deployment.yaml`, each with the measured peak that justifies it.
- **Storage**: NFS `/appdata/hindsight` (encrypted `ssd/appdata`,
  archive-backed) — `pg0/` (PostgreSQL data) + `models/` (GGUF cache).
  Postgres-on-NFS is a deliberate, documented deviation from the zvol
  convention: single-client RWO + `Recreate` + hard NFSv4.2 is the supported
  configuration and `pg0/` is small (~122 MB). The `pg-dump` sidecar writes a
  gzipped plain-SQL dump to NFS `/backups-apps/hindsight` at 03:15 local and
  keeps the newest 7, so the memory store has a logical backup as well as the
  crash-consistent file copy the archive replication takes. That export is
  created by `nas_storage`, so `task storage:deploy` must run before these
  manifests reconcile.
  Follow-up if the data dir on NFS ever bites: a zvol on a modern-CPU agent VM
  (`vm_additional_disks` + `zvol_mount` + a nodeAffinity pin, per docs/06).
- **Multi-user**: Hindsight segregates memory into **banks** (`bank_id` on
  every retain/recall). The Hermes plugin's `bank_id_template` (e.g.
  `hermes-{user}`) derives a bank per platform user — the hook for the future
  multi-user plan; no server-side change needed.
- **Observability**: native `/metrics` on `:8888` (`servicemonitor.yaml` in this
  directory, with `allow-hindsight-ingress` in `networkpolicy.yaml` admitting
  the Prometheus scrape) + the `HindsightDown` alert (kube-state deployment
  availability).

Hermes-side enablement (runtime config, deliberately not in git) and rollback:
**[`docs/37-hermes.md`](../../../docs/37-hermes.md)** §Memory backend.

## Runbook

- **Pod stuck `ContainerCreating` with an NFS mount error**: `/backups-apps/hindsight`
  is missing on pve-nas-01. The export is `nas_storage_exports` in
  `host_vars/pve-nas-01.yml`; run `task storage:deploy`. Deploy it before this
  app's manifests reconcile: the Deployment is `Recreate` on an RWO volume, so
  the serving pod is already gone and this is a full outage, not a degraded one.
  The mount is hard, so it recovers on its own once the export appears.
- **`BackupArtifactStale{app="hindsight"}` warning after a fresh deploy**: the
  alert carries an `absent()` arm for hindsight, so it is lit from the first
  reconcile until a dump lands. The sidecar takes one on startup when the
  landing zone is empty, retrying for ten minutes while pg0 comes up, so this
  should clear on its own. If it does not, the export or pg0 is the problem.
