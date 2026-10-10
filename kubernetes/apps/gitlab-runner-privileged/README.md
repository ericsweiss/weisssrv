# gitlab-runner-privileged

The infrastructure GitLab Runner: tag `infrastructure`, privileged root+DinD,
`concurrent: 12`. weisssrv's own pipeline runs here.

- **Isolation**: the separate namespace is the boundary, not the tag. Tags are
  cooperative and any project can claim one, so the registration scope is what
  confines this runner — its token must be a project runner locked to weisssrv,
  which the live registration is not yet (docs/27 Step 8 carries the gap). PSS
  enforce is `privileged` because DinD job pods need it.
- **Workload**: `release.yaml`, a HelmRelease sharing the common fields with the
  unprivileged runner via `kubernetes/components/gitlab-runner-common`.
- **Job pods**: run as the token-less `gitlab-runner-privileged-jobs`
  ServiceAccount, soft anti-affinity across agents (simultaneous same-node DinD
  starts race cgroup-v2 setup), and `priority_class_name = "ci-jobs"`.
- **Capacity**: `resourcequota.yaml` derives every dimension from `concurrent`;
  change them in the same commit.
- **Secrets**: the runner token (`externalsecret.yaml`) and the shared cache
  credential (`cache-externalsecret.yaml`).
- **Network**: `networkpolicy.yaml` denies egress by default; infrastructure job
  pods keep unrestricted egress because they deploy the homelab itself.

Runner model and boundaries: [docs/13-ci-cd.md § Runner Architecture and §
Runner Network Boundaries](../../../docs/13-ci-cd.md).

## Disable

Drop `- gitlab-runner-privileged` from `kubernetes/apps/kustomization.yaml`;
Flux prunes the namespace. This repo's own pipeline runs here, so every
`infrastructure`-tagged job sits pending until another runner claims the tag.
Unregister the runner in GitLab as well, and drop `- gitlab-runner-reaper` if
neither runner namespace is left.
