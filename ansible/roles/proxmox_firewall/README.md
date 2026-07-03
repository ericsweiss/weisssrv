# Proxmox Firewall Role

Manages Proxmox VE firewall at cluster, host, and guest levels. Configures IPSets for network groupings and Security Groups for reusable rule templates.

## What This Role Manages

### Cluster Firewall (/etc/pve/firewall/cluster.fw)
- Global firewall options
- IPSet definitions (admin_lan, admin_ts, core-cluster, k3s_nodes, pve_hosts, nfs_clients, smb_clients)
- Security Group definitions (sg-vm-admin, sg-dns, sg-smtp-relay, sg-plex, sg-k3s-*)
- Cluster-wide rules

### Host Firewall (/etc/pve/nodes/{node}/host.fw)
- Per-host firewall configuration
- Enable/disable host firewall
- Host-specific rules (SSH, web UI, clustering, storage)

### Guest Firewall (/etc/pve/firewall/{vmid}.fw)
- Per-VM/LXC firewall configuration
- IPSet assignments
- Security Group assignments
- Enable/disable per-guest firewall

## Configuration

### IPSets (Network Groups)

```yaml
# Defined in group_vars or host_vars
firewall_ipsets:
  admin_lan:
    - 192.168.0.0/24
  admin_ts:
    - 100.64.0.0/10  # Tailscale
  core-cluster:
    - 192.168.0.102-107  # Proxmox hosts
    - 192.168.0.150-155  # Services
    # ... (dynamically built from inventory)
  k3s_nodes:
    - 192.168.0.161  # API VIP
    # ... (dynamically built)
  pve_hosts:
    - 192.168.0.102-107
  nfs_clients:
    # Hosts allowed to mount NFS
  smb_clients:
    - 192.168.0.0/24
```

### Security Groups (Rule Templates)

```yaml
security_groups:
  sg-vm-admin:  # SSH and ping
  sg-dns:  # DNS, DoT, DoH, web UI
  sg-smtp-relay:  # SMTP submission
  sg-plex:  # Plex Media Server
  sg-k3s-core:  # Kubernetes API, kubelet
  sg-k3s-ingress-int:  # Internal ingress
  sg-k3s-ingress-pub:  # Public ingress
```

### Guest Configuration

```yaml
# In hosts.yml
hosts:
  dns-01:
    firewall_ipsets:
      - core-cluster
    guest_security_groups:
      - sg-vm-admin
      - sg-dns
```

## Deployment

```bash
# Deploy firewall to all hosts
ansible-playbook ansible/playbooks/site.yml --tags firewall

# Deploy to Proxmox hosts only
ansible-playbook ansible/playbooks/site.yml --limit proxmox

# Deploy guest firewall rules
ansible-playbook ansible/playbooks/site.yml --tags firewall --limit dns
```

## Architecture

```
Proxmox Cluster Firewall
├─ /etc/pve/firewall/cluster.fw (IPSets + Security Groups)
├─ /etc/pve/nodes/pve-nas-01/host.fw (Host rules)
├─ /etc/pve/nodes/pve-opt-03/host.fw (Host rules)
└─ Per-guest rules:
   ├─ /etc/pve/firewall/150.fw (dns-01)
   ├─ /etc/pve/firewall/160.fw (dns-02)
   └─ /etc/pve/firewall/222.fw (k3s-srv-nas-01)
```

## Files

- `tasks/main.yml` - Main orchestration
- `templates/cluster.fw.j2` - Cluster firewall with IPSets and Security Groups
- `templates/host.fw.j2` - Per-host firewall rules
- `templates/guest.fw.j2` - Per-guest firewall rules
- `templates/ipsets.j2` - IPSet generation

## Dependencies

None - foundational role

## Security

- Default deny with explicit allow rules
- Separate admin access (LAN + Tailscale)
- Service-specific Security Groups
- Per-guest isolation with opt-in networking

## Egress filtering (opt-in)

Inbound is default-deny; host-originated **egress** is `ACCEPT` by default.
`proxmox_firewall_egress_filtering` (default `false`) applies the
`sg-host-egress` allowlist (DNS/NTP/HTTP(S)/Tailscale/corosync/SSH/NFS/SMTP/
migration) and appends an explicit trailing `OUT DROP` rule in `host.fw`.
`pve-firewall` honours OUT *rules* in `host.fw` but **ignores** the host-level
`policy_out` option (that key is only effective in `cluster.fw`), so the trailing
`OUT DROP` rule — not a policy setting — is what enforces default-deny. Guest
traffic is unaffected.

Enable carefully — a missing allowlist entry can break a node or remote access:

1. Set `proxmox_firewall_egress_filtering: true` in **one** host's `host_vars`
   (start with a non-critical compute node), deploy, then validate with
   `pve-firewall compile` and confirm the node stays reachable, joins the cluster
   (`pvecm status`), and can reach apt/Tailscale.
2. The `OUT DROP` rule logs dropped OUT packets at `info` — review
   `journalctl -k | grep 'DROP'` (or the kernel log) and extend `sg-host-egress`
   in `cluster.fw.j2` for any legitimate egress that was missed (e.g. a service
   on a non-standard port).
3. Once stable, roll out to the remaining hosts (or set it in `group_vars`).

## Testing

```bash
# Test from external host
ping 192.168.0.150  # Should work if in admin_lan
ssh eric@192.168.0.150  # Should work if in admin IPSets

# View firewall status on Proxmox
pve-firewall status
pve-firewall simulate

# View IPSets
pvesh get /cluster/firewall/ipset

# View Security Groups
pvesh get /cluster/firewall/groups
```
