# gitlab-runner

The shared, **unprivileged** GitLab Runner: tag `k8s-deploy` with
`runUntagged: true`, so it also picks up other GitLab projects' untagged jobs.
weisssrv's own pipeline does not run here.

- **Workload**: `release.yaml`, a HelmRelease in its own namespace. Fields it
  shares with the privileged runner come from the
  `kubernetes/components/gitlab-runner-common` component; only deltas live in
  this file.
- **Job pods**: non-root, no DinD, `priority_class_name = "ci-jobs"`, and the
  token-less `gitlab-runner-jobs` ServiceAccount (`serviceaccount.yaml`).
- **Capacity**: `concurrent: 7`, sized against the namespace ResourceQuota in
  `resourcequota.yaml`. The two move together.
- **Secrets**: two ExternalSecrets — the runner authentication token
  (`externalsecret.yaml`) and the cache credential (`cache-externalsecret.yaml`,
  from the 1Password item `CI Cache Garage`).
- **Cache**: `[runners.cache]` points at `ci-cache.ci-cache.svc.cluster.local:3900`
  with `Shared = true`, so both runners share one cache namespace per key. See
  [../ci-cache/README.md](../ci-cache/README.md).
- **Network**: `networkpolicy.yaml` denies ingress and egress by default. Job
  pods carry `esweiss.com/runner-class=shared` and get internet-only egress.
  `managers-egress` assumes the GitLab host resolves to this cluster's internal
  ingress VIP, plus public :443 for the callbacks GitLab redirects to. A GitLab
  reached directly on the LAN needs its own `ipBlock` rule; without it the
  manager cannot register and fails silently.

Runner model and tiering:
[docs/13-ci-cd.md § Runner Architecture](../../../docs/13-ci-cd.md).

## Disable

Drop `- gitlab-runner` from `kubernetes/apps/kustomization.yaml`; Flux prunes
the namespace and the manager deregisters. Untagged jobs from every project then
have no runner and sit pending, so unregister the runner in GitLab too.
