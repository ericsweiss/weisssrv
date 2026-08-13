# registry-cache

In-cluster **pull-through Docker registry cache** (CNCF
[distribution](https://distribution.github.io/distribution/) `registry` image)
for CI — turns each molecule job's cold pull of the `molecule-test` image into a
warm LAN hit. Design + deploy-token runbook: **[`docs/27-gitlab-deployment.md`](../../../docs/27-gitlab-deployment.md)
§ Registry pull-through cache**.

- **Why**: every molecule CI job's fresh DinD daemon cold-pulls
  `registry.git.ericsweiss.com/eric/weisssrv/molecule-test` (~30s/job,
  ~30 jobs/pipeline). This cache proxies that upstream so the first pull warms it
  and the rest are served node-local.
- **Workload**: one Deployment, one `registry` container in proxy mode
  (`REGISTRY_PROXY_REMOTEURL=https://registry.git.ericsweiss.com`, authed with a
  read_registry GitLab deploy token). Registry API on `:5000`, Prometheus debug
  listener on `:5001`.
- **Consumers**: only the `gitlab-runner-privileged` namespace (the DinD job
  pods) reach `:5000` at
  `registry-cache.registry-cache.svc.cluster.local:5000`; only the Prometheus
  scraper reaches `:5001`. No ingress route, no cert, no external DNS — this is
  cluster-internal, CI-only.
  **The ingress allow is namespace-wide, and the cache holds a credential**: it
  proxies upstream with its own `read_registry` deploy token, so *every* pod in
  `gitlab-runner-privileged` — i.e. every CI job that runs there — can pull any
  image that token can see, without holding the token itself. Acceptable while
  that namespace runs only this repo's own trusted jobs; revisit (per-workload
  selector, or a token scoped to the one project) the moment it runs anything
  from a tenant or a fork.
- **Upstream reach**: `registry.git.ericsweiss.com` is pinned to the internal
  Traefik VIP `.101` at pod scope (`hostAliases`, mirroring the node-level
  `k3s_registry_host_pins`) so the fetch stays on the internal Traefik path
  instead of hairpinning the flaky external/Cloudflare DNS. Egress is DNS +
  `:443` to the `traefik` namespace only.
- **Storage**: a node-local `emptyDir` (10Gi cap) — the cache re-warms on the
  next pull after any restart, so there is no NFS/zvol dependency and nothing to
  back up. Bounded because CI only ever pulls the one `molecule-test` image.
- **Scheduling**: general nodes, NAS-avoid preferred (disposable node-local
  cache; no cpu-class pin — the registry is a static Go binary).
- **Observability**: native `/metrics` on `:5001`
  (`observability/service-monitors/registry-cache.yaml`) + the
  `RegistryCacheDown` alert (kube-state deployment availability). A cache outage
  is degraded (slower CI cold pulls), not an outage — CI's `before_script` falls
  back to a direct-registry pull — so the alert is `warning`, not `critical`.
- **Version**: `registry_cache_version` in
  `ansible/inventories/prod/group_vars/all.yml` (`${registry_cache_version}`
  placeholder, resolved by Flux from the `cluster-versions` ConfigMap).
