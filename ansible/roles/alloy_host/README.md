# Role: alloy_host

Installs Grafana Alloy as a host-side journald log shipper. Used on every
non-k8s host (Proxmox hosts, DNS LXCs, smtp-relay, GitLab VM, Plex LXC) and on
k3s VMs to ship kubelet/containerd/etcd/systemd journals to Loki at
`https://loki.esweiss.com/loki/api/v1/push` (Traefik IngressRoute, gated by
lan-tailscale-only). A plain-HTTP NodePort
(`http://<k3s-node>:31100`, `kubernetes/infrastructure/observability/loki/nodeport.yaml`)
exists as an emergency fallback but is **apply-on-demand** — it is deliberately
not reconciled by Flux (an always-on unauthenticated push path is a security
hole). To use it when Traefik is down: `kubectl apply -f` the nodeport manifest,
override `alloy_host_loki_url` per-host, then delete it once ingress recovers.

## Why a host-side Alloy

Container logs inside k3s are already collected by the in-cluster Alloy
DaemonSet. The host-side Alloy covers what the cluster cannot see:

- Proxmox host journald
- LXC and VM systemd units
- k3s server/agent service unit logs (kubelet/containerd/etcd themselves)

Both ingesters write to the same Loki backend; labels distinguish source.

## Configuration

The apt package is pinned via `alloy_host_version` in
`group_vars/all.yml` and held (`dpkg hold`) so a manual `apt upgrade`
can't move it — mirroring the tailscale/plex host-side pins. This is
distinct from the in-cluster `helm_chart_versions.alloy` chart pin.

The Loki push endpoint defaults to the Traefik-fronted internal
hostname (the cert is the wildcard `*.esweiss.com`, trusted via the
system CA bundle on every host):

```yaml
alloy_host_loki_url: "https://loki.esweiss.com/loki/api/v1/push"
```

Override per-host in `host_vars/<host>.yml` (e.g., to use the
apply-on-demand NodePort `http://<k3s-node>:31100/loki/api/v1/push` as a
fallback when Traefik is down — see note above). See `defaults/main.yml`
for the canonical list of tunables; `group_vars/all.yml` does not pin this
value.

## Deployment

```bash
ansible-playbook ansible/playbooks/site.yml --tags alloy_host
```

## Files

- `tasks/main.yml` — adds the Grafana apt repo (fingerprint-verified, via the
  shared `apt_signed_repo` role), installs Alloy, and manages `CUSTOM_ARGS` in
  `/etc/default/alloy` plus the config file; relies on the packaged systemd
  unit (the role does not template a `.service` unit)
- `templates/config.alloy.j2` — Alloy River config (journald → Loki)
- `defaults/main.yml` — tunables

## See also

- `docs/31-observability.md` — observability stack overview
- `kubernetes/infrastructure/observability/alloy/` — in-cluster counterpart
