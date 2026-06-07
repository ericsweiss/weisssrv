# Role: alloy_host

Installs Grafana Alloy as a host-side journald log shipper. Used on every
non-k8s host (Proxmox hosts, DNS LXCs, smtp-relay, GitLab VM, Plex LXC) and on
k3s VMs to ship kubelet/containerd/etcd/systemd journals to Loki at
`https://loki.esweiss.com/loki/api/v1/push` (Traefik IngressRoute, gated by
lan-tailscale-only). The plain-HTTP NodePort at
`http://192.168.0.161:31100` defined in
`kubernetes/infrastructure/observability/loki/nodeport.yaml` remains as a
fallback for emergencies; override `alloy_host_loki_url` per-host to use it.

## Why a host-side Alloy DaemonSet

Container logs inside k3s are already collected by the in-cluster Alloy
DaemonSet. The host-side Alloy covers what the cluster cannot see:

- Proxmox host journald
- LXC and VM systemd units
- k3s server/agent service unit logs (kubelet/containerd/etcd themselves)

Both ingesters write to the same Loki backend; labels distinguish source.

## Configuration

The Loki push endpoint defaults to the Traefik-fronted internal
hostname (the cert is the wildcard `*.esweiss.com`, trusted via the
system CA bundle on every host):

```yaml
alloy_host_loki_url: "https://loki.esweiss.com/loki/api/v1/push"
```

Override per-host in `host_vars/<host>.yml` (e.g., to use the plain
NodePort `http://192.168.0.161:31100/loki/api/v1/push` as a fallback
when Traefik is down). See `defaults/main.yml` for the canonical list
of tunables; `group_vars/all.yml` does not pin this value.

## Deployment

```bash
ansible-playbook ansible/playbooks/site.yml --tags alloy_host
```

## Files

- `tasks/main.yml` — install + systemd unit + Loki client config
- `templates/config.alloy.j2` — Alloy River config (journald → Loki)
- `templates/alloy.service.j2` — systemd unit
- `defaults/main.yml` — tunables

## See also

- `docs/31-observability.md` — observability stack overview
- `kubernetes/infrastructure/observability/alloy/` — in-cluster counterpart
