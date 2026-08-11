# Post-Base Cluster Plan (SUPERSEDED — historical)

> **Status: superseded.** Every phase this document planned has shipped. Its
> content has been removed rather than left to rot, because a stale plan reads
> like an active one. Git history holds the original text.

The plan covered bringing up the k3s platform on top of the base Proxmox
infrastructure — the cluster itself, GitOps, observability, and the first
application tier. All of that is now live and documented in current form:

| Topic | Current doc |
|---|---|
| k3s cluster deployment and node layout | [docs/19](19-k3s-deployment.md) |
| Flux GitOps day-2 operations | [docs/29](29-flux-operations.md) |
| Tenant / multi-repo onboarding | [docs/30](30-multi-repo-onboarding.md) |
| Observability stack | [docs/31](31-observability.md) |
| Autoscaling and resource tiers | [docs/33](33-autoscaling.md) |
| Remaining work and roadmap | [docs/16](16-next-steps.md) |

Per-application deployment docs are listed in the README documentation index.
