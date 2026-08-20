# ci-cache — Garage S3 backend for the GitLab runner cache

Single-node [Garage](https://garagehq.deuxfleurs.fr) serving one bucket
(`runner-cache`) to both GitLab runners, so the `cache:` blocks in every
pipeline stop being inert (docs/13 § Runner cache backend). Garage over MinIO
because MinIO's upstream was archived in 2026-04.

Design decisions, and why:

- **emptyDir storage, deliberately** (registry-cache precedent): the content
  is a cache the runners re-derive; node-local storage also avoids
  sqlite-on-NFS locking entirely. A pod reschedule costs one cold pipeline.
  `sizeLimit: 25Gi` evicts a runaway cache instead of filling the node.
- **Self-initializing**: v2.3.0's `--single-node --default-bucket` creates
  the bucket and imports the key pair from `GARAGE_DEFAULT_*` env on first
  boot — no init Job, and the 1Password item **CI Cache Garage** stays the
  single source (server + both runner namespaces read the same item).
- **No egress at all** — the policy declares `Egress` with no rules. RPC is
  loopback; a single-node garage talks to nobody.
- **HTTP in-cluster**: the S3 port is plaintext, fenced to exactly the two
  runner namespaces by NetworkPolicy. Content is public build artifacts
  (pip wheels, galaxy collections); poisoning would require a foothold in a
  runner namespace, which already implies CI compromise.
- **Metrics** are token-gated on the admin port (`metrics-token`), scraped by
  `observability/service-monitors/ci-cache.yaml`; `CiCacheDown` alerts on a
  dead scrape target. `/health` stays unauthenticated for the probes.

Runner side: both HelmReleases mount an `s3access` Secret (ESO, same 1P item,
chart-mandated `accesskey`/`secretkey` names) and point `[runners.cache]` at
`ci-cache.ci-cache.svc.cluster.local:3900`, `Shared = true` so the two
runners share one cache namespace per cache key.

Losing this app entirely degrades pipelines to the pre-cache behaviour
(reinstall everything per job) — nothing fails.
