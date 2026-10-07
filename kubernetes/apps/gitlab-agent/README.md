# gitlab-agent

The GitLab agent (`agentk`) release `weisssrv-k3s`. It gives GitLab the
Kubernetes dashboard for this cluster and push-triggers a Flux reconcile on
merge to `main`, ahead of Flux's own one-minute git poll.

- **Workload**: `release.yaml`, a HelmRelease pinned to
  `${gitlab_agent_helm_version}`. `kasAddress` uses the internal GitLab
  hostname, so the KAS long-poll stays on the LAN.
- **Secrets**: `externalsecret.yaml` renders the `gitlab-agent-token` Secret
  from the 1Password item `GitLab Agent Token`.
- **Agent config**: `.gitlab/agents/weisssrv-k3s/config.yaml`. It grants no
  `ci_access`, because CI jobs do not use this agent's kubeconfig today; they
  read a long-lived kubeconfig from 1Password. `remote_development` is off
  because agentk v19+ hard-fails on its synchronous apiserver probe under
  NetworkPolicy.
- **RBAC**: the chart default `rbac.create` gives the agent cluster-admin.
  Accepted for a single operator; scoping it is
  [docs/30-multi-repo-onboarding.md § Pre-Onboarding Checklist](../../../docs/30-multi-repo-onboarding.md).
- **Network**: ingress default-deny via `netpol-baseline`, and deliberately no
  egress policy — kube-router installs the per-pod egress chain after the pod is
  scheduled and agentk bails fatally in that gap.
- **Autoscaling**: `vpa.yaml`, `RequestsOnly` with `maxAllowed.memory` equal to
  the limit in `release.yaml`. Its `targetRef.name` is chart-generated, so
  re-check it after a chart bump.

Agent setup and GitLab-side wiring:
[docs/27-gitlab-deployment.md](../../../docs/27-gitlab-deployment.md).
