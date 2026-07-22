# Proxmox Firewall

The Proxmox cluster uses the built-in firewall with centralized rules in `/etc/pve/firewall/`.

## Architecture

```
/etc/pve/firewall/
  cluster.fw          # Cluster-wide config (IPSets, Groups, Aliases)
  <vmid>.fw           # Per-VM/CT firewall rules

/etc/pve/nodes/<node>/
  host.fw             # Per-node host firewall
```

## Cluster Configuration

### IPSets

IPSets define groups of IPs for use in rules. IPSets are **dynamically generated from inventory** - hosts are automatically added to IPSets based on their `firewall_ipsets` metadata.

```ini
[IPSET core-cluster]
# Proxmox hosts
192.168.0.102  # pve-nas-01
192.168.0.103  # pve-laptop-01
192.168.0.104  # pve-opt-01
192.168.0.105  # pve-opt-02
192.168.0.106  # pve-opt-03
192.168.0.107  # pve-prec-01

[IPSET k3s_nodes]
# K3s cluster VMs
192.168.0.222  # k3s-srv-nas-01
192.168.0.223  # k3s-srv-laptop-01
192.168.0.227  # k3s-srv-prec-01
192.168.0.202  # k3s-agt-nas-01
192.168.0.203  # k3s-agt-laptop-01
192.168.0.204  # k3s-agt-opt-01
192.168.0.205  # k3s-agt-opt-02
192.168.0.206  # k3s-agt-opt-03
192.168.0.207  # k3s-agt-prec-01

[IPSET nfs_clients]
# Hosts allowed NFS access
192.168.0.102
192.168.0.103
192.168.0.104
192.168.0.105
192.168.0.106
192.168.0.107
192.168.0.154
192.168.0.200/29
192.168.0.220/29

[IPSET smb_clients]
# Hosts allowed SMB access
192.168.0.0/24
```

The `dc/` scope prefix appears only when rules *reference* an ipset
(`-source +dc/k3s_nodes`), never in the declaration header.

**`admin_ts` is deliberately the full CGNAT range** (`100.64.0.0/10`), not
per-device 100.x pins. Accepted risk: this is a single-owner tailnet
(docs/05-tailscale.md), per-device pins are brittle (onboarding/DR lockout)
and partly moot — subnet-router SNAT means tailnet traffic to guests arrives
as `admin_lan` anyway. Tailnet-side ACL tightening is codified in
`terraform/tailscale/`. Revisit if the tailnet ever gains non-admin members.

### Security Groups

Security groups are reusable rule sets. Each security group has a **single, clear purpose** - admin access is separated from service-specific rules.

#### Admin Access Security Groups

**sg-host-admin** - Proxmox hypervisor hosts only:
```ini
[group sg-host-admin]

IN ACCEPT -source +dc/admin_ts -p tcp -dport 8006 -log nolog # Proxmox Web UI
IN ACCEPT -source +dc/admin_lan -p tcp -dport 8006 -log nolog
IN ACCEPT -source +dc/admin_ts -p tcp -dport 22 -log nolog   # SSH
IN ACCEPT -source +dc/admin_lan -p tcp -dport 22 -log nolog
IN ACCEPT -source +dc/admin_ts -p icmp -log nolog            # Ping
IN ACCEPT -source +dc/admin_lan -p icmp -log nolog
```

**sg-vm-admin** - All VMs and LXC containers:
```ini
[group sg-vm-admin]

IN ACCEPT -source +dc/admin_ts -p tcp -dport 22 -log nolog   # SSH
IN ACCEPT -source +dc/admin_lan -p tcp -dport 22 -log nolog
IN ACCEPT -source +dc/admin_ts -p icmp -log nolog            # Ping
IN ACCEPT -source +dc/admin_lan -p icmp -log nolog
```

**sg-pve-cluster** - Proxmox cluster communication:
```ini
[group sg-pve-cluster]

IN ACCEPT -source +dc/pve_hosts -p tcp -dport 8006 -log nolog      # Web UI
IN ACCEPT -source +dc/pve_hosts -p tcp -dport 60000:60050 -log nolog # Migration
IN ACCEPT -source +dc/pve_hosts -p tcp -dport 22 -log nolog        # SSH
IN ACCEPT -source +dc/pve_hosts -p udp -dport 5406 -log nolog      # Corosync
IN ACCEPT -source +dc/pve_hosts -p udp -dport 5405 -log nolog      # Corosync
```

#### Service-Specific Security Groups

> The blocks below are reproduced from
> `ansible/roles/proxmox_firewall/templates/cluster.fw.j2`, which is the
> authoritative source; if they ever diverge, trust the template.

**sg-dns** - DNS service ports only (no SSH):
```ini
[group sg-dns]

# DoT (DNS over TLS)
IN ACCEPT -source +dc/admin_ts -p udp -dport 853 -log nolog
IN ACCEPT -source +dc/admin_lan -p udp -dport 853 -log nolog
IN ACCEPT -source +dc/admin_ts -p tcp -dport 853 -log nolog
IN ACCEPT -source +dc/admin_lan -p tcp -dport 853 -log nolog
# AdGuard Home plaintext admin API (:3000)
IN ACCEPT -source +dc/admin_ts -p tcp -dport 3000 -log nolog
IN ACCEPT -source +dc/admin_lan -p tcp -dport 3000 -log nolog
# AdGuard Home HTTPS admin UI (:443) — Traefik proxies dns-01/dns-02.esweiss.com
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 443 -log nolog
IN ACCEPT -source +dc/admin_ts -p tcp -dport 443 -log nolog
IN ACCEPT -source +dc/admin_lan -p tcp -dport 443 -log nolog
# Standard DNS
IN ACCEPT -source +dc/admin_ts -p tcp -dport 53 -log nolog
IN ACCEPT -source +dc/admin_ts -p udp -dport 53 -log nolog
IN ACCEPT -source +dc/admin_lan -p tcp -dport 53 -log nolog
IN ACCEPT -source +dc/admin_lan -p udp -dport 53 -log nolog
```

**sg-smtp-relay** - SMTP relay (no SSH):
```ini
[group sg-smtp-relay]

# SMTP submission from core cluster
IN ACCEPT -source +dc/core-cluster -p tcp -dport 587 -log nolog
# SMTP relay from core cluster
IN ACCEPT -source +dc/core-cluster -p tcp -dport 25 -log nolog
# Outbound egress allowlist — only ENFORCED because the smtp-relay guest sets
# policy_out: DROP (guest_firewall_policy_out in its inventory entry); with
# Proxmox's guest default (policy_out ACCEPT) these OUT ACCEPTs are no-ops.
# conntrack auto-allows replies to inbound 25/587.
# DNS and SMTP submission (upstream relay to Gmail)
OUT ACCEPT -p udp -dport 853 -log nolog
OUT ACCEPT -p tcp -dport 853 -log nolog
OUT ACCEPT -p udp -dport 53 -log nolog
OUT ACCEPT -p tcp -dport 53 -log nolog
OUT ACCEPT -p tcp -dport 587 -log nolog
# apt (the base role runs apt inside the LXC) + ICMP diagnostics
OUT ACCEPT -p tcp -dport 80 -log nolog
OUT ACCEPT -p tcp -dport 443 -log nolog
OUT ACCEPT -p tcp -dport 31100 -log nolog       # Loki push NodePort fallback (alloy_host_loki_url)
OUT ACCEPT -p icmp -log nolog
```

**sg-nfs-server** - NFS exports:
```ini
[group sg-nfs-server]

IN ACCEPT -source +dc/nfs_clients -p udp -dport 111 -log nolog
IN ACCEPT -source +dc/nfs_clients -p tcp -dport 111 -log nolog
IN ACCEPT -source +dc/nfs_clients -p tcp -dport 2049 -log nolog
```

**sg-smb-server** - Samba shares:
```ini
[group sg-smb-server]

IN ACCEPT -source +dc/smb_clients -p tcp -dport 445 -log nolog
```

**sg-k3s-core** - K3s cluster communication:
```ini
[group sg-k3s-core]

IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 2379:2380 -log nolog  # etcd
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 2381 -log nolog       # etcd metrics (kubeEtcd scrape)
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 10250 -log nolog      # kubelet
IN ACCEPT -source +dc/k3s_nodes -p udp -dport 51820 -log nolog      # Flannel WireGuard (active CNI backend; VXLAN/8472 retired 2026-06-11)
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 7946 -log nolog       # MetalLB memberlist
IN ACCEPT -source +dc/k3s_nodes -p udp -dport 7946 -log nolog       # MetalLB memberlist
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 9345 -log nolog       # k3s supervisor
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 6443 -log nolog       # Kubernetes API
IN ACCEPT -source +dc/admin_ts -p tcp -dport 6443 -log nolog        # kubectl from Tailscale
IN ACCEPT -source +dc/admin_lan -p tcp -dport 6443 -log nolog       # kubectl from LAN
```

**sg-plex** - Plex Media Server:
```ini
[group sg-plex]

IN ACCEPT -source +dc/admin_lan -p tcp -dport 32469 -log nolog      # DLNA
IN ACCEPT -source +dc/admin_lan -p udp -dport 32410:32414 -log nolog # GDM
IN ACCEPT -source +dc/admin_lan -p udp -dport 1900 -log nolog       # SSDP
IN ACCEPT -p tcp -dport 32400 -log nolog                             # Plex Web (public)
```

**sg-host-egress** - Host-originated egress allowlist (hosts only, paired
with a trailing `OUT DROP` in host.fw — see "Host egress filtering" below):
```ini
[group sg-host-egress]

OUT ACCEPT -p udp -dport 53 -log nolog          # DNS
OUT ACCEPT -p tcp -dport 53 -log nolog          # DNS over TCP
OUT ACCEPT -p udp -dport 853 -log nolog         # DNS over TLS
OUT ACCEPT -p tcp -dport 853 -log nolog         # DNS over TLS
OUT ACCEPT -p udp -dport 123 -log nolog         # NTP
OUT ACCEPT -p tcp -dport 80 -log nolog          # apt / HTTP
OUT ACCEPT -p tcp -dport 443 -log nolog         # apt, Proxmox repos, Tailscale control/DERP, 1Password Connect
OUT ACCEPT -p udp -dport 41641 -log nolog       # Tailscale direct
OUT ACCEPT -p udp -dport 3478 -log nolog        # Tailscale STUN
OUT ACCEPT -p tcp -dport 22 -log nolog          # SSH (migration, host-to-host, cert distribution)
OUT ACCEPT -p tcp -dport 2222 -log nolog        # GitLab SSH
OUT ACCEPT -p tcp -dport 587 -log nolog         # SMTP submission to relay
OUT ACCEPT -p tcp -dport 25 -log nolog          # SMTP relay
OUT ACCEPT -p tcp -dport 2049 -log nolog        # NFS (backup target / shares)
OUT ACCEPT -p tcp -dport 111 -log nolog         # rpcbind (NFS)
OUT ACCEPT -p udp -dport 111 -log nolog         # rpcbind (NFS)
OUT ACCEPT -p tcp -dport 31100 -log nolog       # Loki push NodePort fallback (alloy_host_loki_url)
OUT ACCEPT -p udp -dport 5404:5412 -log nolog   # corosync cluster membership
OUT ACCEPT -p tcp -dport 8006 -log nolog        # Proxmox API (cluster/migration)
OUT ACCEPT -p tcp -dport 60000:60050 -log nolog # Proxmox live migration (insecure channel)
OUT ACCEPT -p icmp -log nolog                   # ping/diagnostics
```

### Options

```ini
[OPTIONS]
enable: 1
policy_in: DROP
policy_out: ACCEPT
log_level_in: nolog
log_level_out: nolog
```

Host-level inbound drop logging is tunable via `pve_firewall_log_level_in`
(role default `nolog`; rendered into each `host.fw`). Flip it to `info` — per
host_vars or globally — to make dropped-inbound packets visible in the kernel
log for triage; pve-firewall rate-limits its own logging, so `info` is safe
to leave on during an incident.

## Host Firewall

Each Proxmox host has a host.fw that references security groups. All Proxmox hosts get `sg-pve-cluster` and `sg-host-admin` automatically.

**pve-nas-01 host.fw** (NAS role):
```ini
[OPTIONS]
enable: 1
log_level_in: nolog
log_level_out: nolog

[RULES]
# All Proxmox hosts need cluster communication, admin access, and exporter scraping
GROUP sg-pve-cluster
GROUP sg-host-admin
GROUP sg-metrics
# Host egress default-deny (proxmox_firewall_egress_filtering)
GROUP sg-host-egress
OUT DROP -log info

# NAS-specific rules
GROUP sg-nfs-server
GROUP sg-smb-server
```

### Host egress filtering

Host-originated egress default-deny is **enabled on all six Proxmox hosts**
(`proxmox_firewall_egress_filtering: true` in `group_vars/proxmox.yml`; role
default `false`). When enabled, `host.fw` references the `sg-host-egress`
allowlist and appends an explicit trailing `OUT DROP -log info` rule —
pve-firewall honors OUT *rules* in host.fw but **ignores** the host-level
`policy_out` option, so the trailing DROP rule (not a policy setting) is what
enforces default-deny. Drops log at `info` for allowlist tuning; conntrack
auto-allows RELATED/ESTABLISHED replies, so the allowlist covers only NEW
outbound connections the node initiates. When enabling on a new host, verify
with `pve-firewall compile` first — a missing allowlist entry breaks the host
(and possibly remote access).

## Guest Firewall (VMs and LXC Containers)

VMs and LXC containers have per-guest firewall rules (e.g., `/etc/pve/firewall/150.fw` for VMID 150). All guests get `sg-vm-admin` for SSH + ICMP, plus service-specific groups.

**dns-01 (VMID 150) firewall:**
```ini
[OPTIONS]
enable: 1

[RULES]
GROUP sg-vm-admin
GROUP sg-dns
GROUP sg-metrics
```

**smtp-relay (VMID 151) firewall** — the one guest that enforces
default-deny egress (`guest_firewall_policy_out: "DROP"` in its inventory
entry). Unlike host.fw, guest firewalls honor `policy_out`, which turns
sg-smtp-relay's OUT ACCEPT rules into an enforced egress allowlist:
```ini
[OPTIONS]
enable: 1
policy_out: DROP

[RULES]
GROUP sg-vm-admin
GROUP sg-smtp-relay
GROUP sg-metrics
```

**k3s-agt-opt-03 (VMID 206) firewall:**
```ini
[OPTIONS]
enable: 1

[RULES]
GROUP sg-vm-admin
GROUP sg-k3s-core
GROUP sg-k3s-ingress-int
GROUP sg-k3s-ingress-pub
GROUP sg-metrics
```

## Kubernetes NetworkPolicies (in-cluster pod egress)

The Proxmox firewall above governs host/VM/LXC traffic. *Inside* the k3s cluster,
pod-to-pod and pod-to-external traffic is governed by Kubernetes NetworkPolicies
(Flux-managed under `kubernetes/apps/*/networkpolicy.yaml` and the controller
namespaces). Each app namespace runs **default-deny** ingress + egress, and every
pod is then granted exactly the egress it needs by a **scoped, per-pod
NetworkPolicy** (selected by `app.kubernetes.io/name`).

### Design decision: per-pod egress is deliberately granular (not deduplicated)

The same egress *entries* recur across policies — "allow DNS to kube-dns" appears
in ~9 policies and "allow apiserver (`192.168.0.222/223/227:6443`)" in ~5. This
duplication is **intentional and is kept as-is**:

- Egress is scoped per workload, so each pod gets the minimum it needs. For
  example, the Authentik Postgres pod has `allow-egress-postgres-dns-only` (DNS
  only, **no** apiserver), while `allow-egress-authentik` grants the server pods
  DNS + apiserver + their specific destinations.
- The only way to remove the duplication in Kustomize is a namespace-wide
  baseline policy granting DNS + apiserver to **all** pods. Because
  NetworkPolicies are additive (a pod's egress is the union of all policies
  selecting it), that baseline can only *loosen* the posture — Postgres (and any
  DNS-only pod) would gain apiserver egress it deliberately lacks. That is a real
  reduction in defense-in-depth (a lateral-movement path from a compromised data
  pod to the API server) that offline CI cannot catch.
- The duplication is therefore the price of keeping each pod's egress minimal and
  explicitly auditable. We keep the granular per-pod policies; re-IPing a server
  node is the only maintenance cost, and that is rare and caught at deploy time.

(Reviewed as DUP-7 / k8s-infra-03 / RV-SIMP-5; decision: keep granular.)

## Ansible Role

Deploy with: `ansible/roles/proxmox_firewall`

The role manages:
- `/etc/pve/firewall/cluster.fw` - Cluster-wide IPSets and Security Groups
- `/etc/pve/nodes/<node>/host.fw` - Per-host firewall rules
- `/etc/pve/firewall/<vmid>.fw` - Per-guest firewall rules (VMs and LXC containers)

### Managing IPSets via Inventory

IPSets are dynamically generated from inventory metadata. To add a host to an IPSet, add the `firewall_ipsets` list to the host definition:

**Example - Adding a new k3s node:**

```yaml
# inventories/prod/hosts.yml
k3s_servers:
  hosts:
    k3s-new-node:
      ansible_host: 192.168.0.208
      ansible_connection: local  # If not yet managed by Ansible
      firewall_ipsets:
        - k3s_nodes
        - core-cluster
        - nfs_clients
```

**Available IPSets:**
- `pve_hosts` - Proxmox VE hypervisor hosts
- `core-cluster` - All core infrastructure (Proxmox, DNS, SMTP, services, k3s)
- `k3s_nodes` - Kubernetes nodes
- `nfs_clients` - Hosts allowed NFS access

**Special Entries (VIPs, non-host IPs):**

For IPs that aren't inventory hosts (VIPs, floating IPs), add them to `group_vars/all.yml`:

```yaml
firewall_ipset_special_entries:
  k3s_nodes:
    - ip: 192.168.0.161
      comment: k3s API VIP (kube-vip)
```

**Benefits:**
- Single source of truth in inventory
- Automatic IPSet updates when hosts added/removed
- No duplication between firewall rules and NFS exports
- Git history tracks which hosts are in security groups

### Managing Guest Security Groups

Guest firewalls (VMs and LXC containers) are configured via inventory metadata. Add `vmid` and `guest_security_groups` to the host definition:

**Example - DNS container:**
```yaml
# inventories/prod/hosts.yml
dns:
  hosts:
    dns-01:
      ansible_host: 192.168.0.150
      vmid: 150
      guest_security_groups:
        - sg-vm-admin     # SSH + ICMP for admin access
        - sg-dns          # DNS service ports
        - sg-metrics      # Prometheus exporter scraping
```

**Example - K3s ingress node:**
```yaml
k3s_agents:
  hosts:
    k3s-agt-opt-03:
      ansible_host: 192.168.0.206
      vmid: 206
      guest_security_groups:
        - sg-vm-admin         # SSH + ICMP for admin access
        - sg-k3s-core         # K3s cluster communication
        - sg-k3s-ingress-int  # Internal ingress (admin networks)
        - sg-k3s-ingress-pub  # Public ingress (all sources)
        - sg-metrics          # Prometheus exporter scraping
```

**Available Security Groups:**

| Security Group | Purpose | Use For |
|---------------|---------|---------|
| `sg-vm-admin` | SSH + ICMP admin access | **All VMs/LXCs** |
| `sg-dns` | DNS service (DoT, UDP/TCP 53, AdGuard UI) | DNS containers |
| `sg-smtp-relay` | SMTP submission/relay + outbound | smtp-relay container |
| `sg-plex` | Plex Media Server ports | Plex container |
| `sg-k3s-core` | K3s cluster communication | All K3s nodes |
| `sg-k3s-ingress-int` | HTTP/HTTPS from admin networks | K3s ingress nodes (internal apps) |
| `sg-k3s-ingress-pub` | HTTP/HTTPS from all sources | K3s ingress nodes (public apps) |
| `sg-gitlab` | GitLab HTTP/HTTPS + Git SSH | GitLab VM |
| `sg-haos` | Home Assistant Web UI + mDNS | Home Assistant VM |
| `sg-windows` | Windows RDP | Windows VMs |
| `sg-metrics` | Prometheus exporter scrape ports from k3s_nodes (9100/9101/9134/9167/8123/32400/3000/7472/7473) + Loki NodePort 31100 from core-cluster | **All hosts and guests** |
| `sg-host-egress` | Host-originated egress allowlist (paired with trailing `OUT DROP` in host.fw) | Proxmox hosts (via `proxmox_firewall_egress_filtering`) |

To enforce a guest egress allowlist, set `guest_firewall_policy_out: "DROP"`
on the guest's inventory entry — its security groups' OUT ACCEPT rules then
become the allowlist (currently enabled on smtp-relay).

**Deployment:**

Guest firewalls are automatically deployed when:
1. Running the `proxmox_firewall` role on a host with `vmid` and `guest_security_groups` defined
2. Provisioning a new VM/LXC (the provisioning roles call the firewall role)

**HA-Ready Design:**

Guest firewall configs are stored in `/etc/pve/firewall/` which is **cluster-shared storage**. This means:
- Firewall rules are accessible from ANY Proxmox node in the cluster
- Cluster-wide firewall (`cluster.fw`) and `pveum` tasks delegate to the first
  **reachable** Proxmox host (resilient to a down first node); host firewalls
  (`host.fw`) run on each node itself. Per-guest rules (`<vmid>.fw`) delegate to
  `groups['proxmox'][0]` unless `firewall_deploy_host` is set
- No need to track which host is running each container
- Works with Proxmox HA and live migration

You can override the deployment target with:
```yaml
# group_vars/all.yml
firewall_deploy_host: pve-nas-01  # Optional: specific host for firewall deployment
```

To update guest firewall rules, modify `guest_security_groups` in inventory and re-run:
```bash
# Update all firewalls
task infra:deploy

# Update specific host
ansible-playbook ansible/playbooks/site.yml --limit dns-01
```

## Troubleshooting

### Check Firewall Status

```bash
# Cluster level
pve-firewall status

# Compile and show rules
pve-firewall compile

# Show active iptables rules
iptables -L -n -v
```

### Logs

```bash
# Firewall logs (if logging enabled)
journalctl -u pve-firewall

# Dropped packets
dmesg | grep -i drop
```

### Common Issues

1. **Locked out of SSH**: Access via Proxmox console, disable firewall temporarily
2. **NFS mounts failing**: Verify NFS IPSet includes client
3. **VM cannot reach network**: Check VM-level firewall is disabled or has proper rules

### Emergency Disable

```bash
# Disable cluster firewall
pvesh set /cluster/firewall/options --enable 0

# Or edit directly
nano /etc/pve/firewall/cluster.fw
# Set enable: 0
```
