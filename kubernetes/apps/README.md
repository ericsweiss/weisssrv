# kubernetes/apps

One directory per module, all reconciled by the `apps` Flux Kustomization
(`kubernetes/clusters/weisssrv/apps.yaml`). Each module holds its complete
resource set — namespace, workload, NetworkPolicy, VPA, ingress — and a README.

`kustomization.yaml` here is the enabled set. Removing a module's line makes
Flux **prune** that module and its namespace, which is the documented way to
disable one. Per-module notes below say what else has to be un-wired.

## Modules

| Module | What it is | Disable by |
|---|---|---|
| `authentik` | SSO/OIDC identity provider; every user-facing app sits behind it | removing the entry — then un-wire Grafana OIDC and every `authentik-auth` middleware reference |
| `ci-cache` | single-node Garage S3 serving the `runner-cache` bucket to both runners | removing the entry — then drop `[runners.cache]` from both runner releases |
| `download-clients` | VPN-protected download clients and media management (Gluetun sidecar) | removing the entry |
| `gitlab-agent` | `agentk` for the GitLab Kubernetes dashboard and push-triggered Flux reconciles | removing the entry — the one-minute git poll stays the only trigger |
| `gitlab-runner` | shared, unprivileged runner (tag `k8s-deploy`, also untagged jobs) | removing the entry |
| `gitlab-runner-privileged` | infrastructure runner (root + DinD) that runs this repo's pipeline | removing the entry — CI deploy jobs then have no runner |
| `gitlab-runner-reaper` | CronJob GCing leaked executor pods and their per-job Secrets | removing the entry |
| `hermes` | Hermes Agent and its web dashboard | removing the entry |
| `hindsight` | long-term agent memory, Hermes' memory backend | removing the entry — then drop Hermes' memory-backend config |
| `hindsight-reaper` | CronJob deleting admission-rejected Hindsight pods | removing the entry |
| `homarr` | homelab dashboard and launcher | removing the entry |
| `recipes` | Mealie and Bar Assistant, both behind Authentik OIDC | removing the entry |
| `registry-cache` | pull-through container registry cache for CI | removing the entry — molecule jobs then cold-pull from the GitLab registry |
| `tailnet-dns` | CoreDNS serving the tailnet's split-horizon view | removing the entry — then drop the Split-DNS nameserver from `terraform/tailscale` |
| `uptime-kuma` | endpoint monitoring and the public status page | removing the entry |
| `vm-ingress` | Traefik routing objects for the off-cluster guests (no pods) | removing the entry — the guests lose their ingress names |
| `wg-easy` | internet-exit WireGuard VPN for the user and family | removing the entry — then drop the `.99` MetalLB address |

The shape a new module follows, and the gates it has to pass, are in
`.claude/skills/weisssrv-development/references/add-k8s-app.md`.
