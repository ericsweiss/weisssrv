# hindsight

[Hindsight](https://github.com/vectorize-io/hindsight) (vectorize-io) —
long-term agent memory (knowledge graph, entity resolution, observation
consolidation) serving as **Hermes' memory backend** (docs/37 §Memory backend).

- **Workload**: one Deployment, two containers, mirroring upstream's
  `docker/docker-compose/local-llm/` example:
  - `hindsight` — the published standalone image (API `:8888`), embedded pg0
    PostgreSQL on the NFS volume.
  - `llama` — the official llama.cpp server image as an OpenAI-compatible
    sidecar (Gemma 4 E2B Q4 GGUF, auto-downloaded once, cached on NFS).
    Fully local: no LLM API key, no per-turn spend. The published hindsight
    image deliberately omits `llama-cpp-python`, so the in-process
    `HINDSIGHT_API_LLM_PROVIDER=llamacpp` mode does NOT work with it — the
    sidecar is upstream's recommended shape.
- **Consumers**: only the `hermes` namespace (ClusterIP
  `hindsight.hindsight.svc.cluster.local:8888`) + the Prometheus scraper.
  No ingress route, no cert, no external DNS — this is cluster-internal.
- **Scheduling**: modern-CPU general nodes, NAS-avoid preferred. The llama
  container's 4Gi memory request means it lands on `k3s-agt-prec-01` in
  practice (the only non-NAS modern agent with the headroom).
- **Storage**: NFS `/appdata/hindsight` (encrypted `ssd/appdata`,
  archive-backed) — `pg0/` (PostgreSQL data) + `models/` (GGUF cache).
  Postgres-on-NFS is a deliberate, documented deviation from the zvol
  convention: single-client RWO + `Recreate` + hard NFSv4.2 is the supported
  configuration, the dataset is tiny, and NFS buys automatic backup. Follow-up
  if it ever bites: a zvol on a modern-CPU agent VM
  (`vm_additional_disks` + `zvol_mount` + a nodeAffinity pin, per docs/06).
- **Multi-user**: Hindsight segregates memory into **banks** (`bank_id` on
  every retain/recall). The Hermes plugin's `bank_id_template` (e.g.
  `hermes-{user}`) derives a bank per platform user — the hook for the future
  multi-user plan; no server-side change needed.
- **Observability**: native `/metrics` on `:8888`
  (`observability/service-monitors/hindsight.yaml`) + the `HindsightDown`
  alert (kube-state deployment availability).

Hermes-side enablement (runtime config, deliberately not in git) and rollback:
**[`docs/37-hermes.md`](../../../docs/37-hermes.md)** §Memory backend.
