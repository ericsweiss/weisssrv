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
10.0.10.102  # pve-nas-01
10.0.10.103  # pve-laptop-01
10.0.10.104  # pve-opt-01
10.0.10.105  # pve-opt-02
10.0.10.106  # pve-opt-03
10.0.10.107  # pve-prec-01

[IPSET k3s_nodes]
# K3s cluster VMs
10.0.10.222  # k3s-srv-nas-01
10.0.10.223  # k3s-srv-laptop-01
10.0.10.227  # k3s-srv-prec-01
10.0.10.202  # k3s-agt-nas-01
10.0.10.203  # k3s-agt-laptop-01
10.0.10.204  # k3s-agt-opt-01
10.0.10.205  # k3s-agt-opt-02
10.0.10.206  # k3s-agt-opt-03
10.0.10.207  # k3s-agt-prec-01

[IPSET nfs_clients]
# Hosts allowed NFS access — every inventory host carrying nfs_clients in its
# firewall_ipsets, as individual /32s (the six Proxmox hosts, the app VMs
# .153/.154/.156/.157, and all nine k3s nodes). Read the live set rather than
# trusting a copy:
#   ssh pve-nas-01 "sudo sed -n '/IPSET nfs_clients/,/^\[/p' /etc/pve/firewall/cluster.fw"

[IPSET smb_clients]
# Client subnets allowed SMB access (proxmox_firewall_smb_client_cidrs)
10.0.10.0/24
10.0.20.0/24
```

The `dc/` scope prefix appears only when rules *reference* an ipset
(`-source +dc/k3s_nodes`), never in the declaration header.

### Client scopes: `admin_lan` vs `lan_clients` vs `dns_clients`

With the LAN segmented into UniFi VLANs
([docs/46-unifi-network.md](46-unifi-network.md)) a single "the LAN" set no
longer describes anything useful: a phone on the Home VLAN should reach Home
Assistant's web UI and never the Proxmox API, and an IoT plug should reach a
resolver and nothing else. Three sets carry those scopes, defined in
`group_vars/all.yml` (`proxmox_firewall_admin_lan_cidrs` and the
`firewall_ipset_special_entries` keys `lan_clients` / `dns_clients` — a key no
inventory host references creates the set outright).

| Set | Members | Used by |
|---|---|---|
| `admin_lan` | `10.0.10.0/24`, `10.0.20.8/29` | The management plane: `:22`, `:8006`, `:6443`, `:3389`, `:22222`, the AdGuard `:3000`/`:443`/`:853` admin surfaces, GitLab `:22` |
| `lan_clients` | `10.0.10.0/24`, `10.0.20.0/24` | User-facing service ports: HAOS `:8123`, GitLab web `:80`/`:443`, Nextcloud/Immich `:443`, Plex + HAOS discovery, and (as `smb_clients`) SMB `:445` |
| `dns_clients` | `10.0.10.0/24`, `10.0.20.0/24`, `10.0.30.0/24`, `10.0.40.0/24`, `10.0.50.0/24` | Resolver `:53` only — every *client* VLAN uses the weisssrv resolvers. The Default/mgmt VLAN (`10.0.1.0/24`) is deliberately **not** a member: its DHCP hands out public resolvers and no zone policy admits Internal → homelab, so nothing from it can arrive here anyway |

Read them as three concentric scopes: `admin_lan` ⊂ `lan_clients` ⊂
`dns_clients`. A port that moves outward needs no second admin rule; a port
that stays admin-scoped is a deliberate statement that client VLANs have no
business there. The `10.0.20.8/29` block is the admin-device reservation range
on the Home VLAN — the workstation the estate is administered from — and three
other layers mirror it exactly: the sshd layer (`ssh_authorized_keys` `from=`,
`base_fail2ban_ignoreip`, `gitlab_ssh_allowed_users`), and in Kubernetes the
`lan-tailscale-strict` Traefik middleware, which admits
`${cluster_home_admin_cidr}` rather than the whole Home VLAN because it fronts
the routes with no forward-auth (1Password Connect, the router and AdGuard
appliance UIs) and Traefik proxies from a node that is itself inside
`admin_lan`. A Home-VLAN device outside the /29 is therefore refused at every
layer that matters.

The IoT VLAN (`10.0.30.0/24`) appears **inline** in two `sg-haos` rules rather
than in any set — udp `5353` and tcp `8123`. Everything else IoT might seem to
need is absent on purpose, and the reason is layering: the UniFi zone firewall
is the *first* gate for anything arriving from another VLAN, so a rule here for
a flow the gateway drops is dead code that reads like a granted permission. The
only iot → homelab allowances are `:53`, Plex `:32400` and HA `:8123`
([docs/46](46-unifi-network.md) § Zones and policies), and mDNS survives only
because UniFi's repeater re-transmits the query with the original source
address. `sg-plex` accordingly carries **no** IoT source at all: GDM and SSDP
are multicast and never leave the VLAN, and TVs on IoT still stream because
`:32400` is world-open and the Plex client finds the server through plex.tv.
Folding IoT into `lan_clients` would also silently grant the web UIs.

Two of those rule groups are written by the collection's `cluster.fw.j2`, not by
site data, so they are re-scoped through role variables rather than
`proxmox_firewall_security_groups`. `weisssrv.infra` v0.13.0 added both; each
name in the list renders as one `+dc/<name>` source in the rule shape the
template already used, and both default to `[admin_ts, admin_lan]` so an
unset site renders exactly what it rendered before.

| Variable | Rules it scopes | Value here |
|---|---|---|
| `proxmox_firewall_dns_client_sources` | `sg-dns` `:53` tcp+udp | `["admin_ts", "dns_clients"]` |
| `proxmox_firewall_k3s_ingress_int_sources` | all of `sg-k3s-ingress-int` (`:80`/`:443`) | `["admin_ts", "lan_clients"]` |

`admin_ts` stays on both — the tailnet reaches the resolvers and the internal
ingress exactly as before — and `admin_lan` drops off because `dns_clients` and
`lan_clients` both contain it.

**`admin_ts` is deliberately the full CGNAT range** (`100.64.0.0/10`), not
per-device 100.x pins. Accepted risk: this is a single-owner tailnet
(docs/05-tailscale.md), per-device pins are brittle (onboarding/DR lockout)
and partly moot — subnet-router SNAT means tailnet traffic to guests arrives
as `admin_lan` anyway. Tailnet-side ACL tightening is codified in
`terraform/tailscale/`. The VLAN migration re-opened and **closed** this
question: the tailnet ACL already enforces device/user granularity, so
narrowing the ipset would duplicate that layer while adding DR-lockout risk.
Revisit only if the tailnet ever gains non-admin members.

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
IN ACCEPT -source +dc/pve_hosts -p tcp -dport 22 -log nolog        # SSH
IN ACCEPT -source +dc/pve_hosts -p udp -dport 5406 -log nolog      # Corosync
IN ACCEPT -source +dc/pve_hosts -p udp -dport 5405 -log nolog      # Corosync
```

The cleartext live-migration range (TCP 60000-60050) is deliberately **not**
opened, in or out. `proxmox_ha` pins `migration: type=secure` in
`datacenter.cfg`, so migration rides the SSH tunnel; pre-authorising the range
would make a flip to `insecure` — guest RAM on the wire in the clear —
invisible at the packet filter. `proxmox_firewall_insecure_migration_ports:
true` renders the rules again, and should only ever be set alongside a
deliberate `proxmox_ha_migration_type: insecure`.

#### Service-Specific Security Groups

> Two sources own these blocks, and which one applies depends on the group.
> `sg-dns`, `sg-host-admin`, `sg-vm-admin`, `sg-k3s-*`, `sg-nfs-server`,
> `sg-metrics`, `sg-pve-cluster`, `sg-smb-server`, `sg-smtp-relay` and
> `sg-host-egress` are **library built-ins**, reproduced from
> weisssrv-lib `ansible_collections/weisssrv/infra/roles/proxmox_firewall/templates/cluster.fw.j2` —
> if they ever diverge, trust the template. The per-application groups
> (`sg-plex`, `sg-gitlab`, `sg-nextcloud`, `sg-immich`, `sg-immich-ml`,
> `sg-haos`, `sg-windows`) are **site data**: entries of
> `proxmox_firewall_security_groups` in
> `ansible/inventories/prod/group_vars/all.yml`, which the template renders
> through a generic loop. For those, that file is the authoritative source.

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
# Standard DNS — sources from proxmox_firewall_dns_client_sources, one
# tcp+udp pair per name, in list order
IN ACCEPT -source +dc/admin_ts -p tcp -dport 53 -log nolog
IN ACCEPT -source +dc/admin_ts -p udp -dport 53 -log nolog
IN ACCEPT -source +dc/dns_clients -p tcp -dport 53 -log nolog
IN ACCEPT -source +dc/dns_clients -p udp -dport 53 -log nolog
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
IN ACCEPT -source +dc/k3s_nodes -p udp -dport 51820 -log nolog      # Flannel WireGuard (the CNI backend; VXLAN/8472 is not used)
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 7946 -log nolog       # MetalLB memberlist
IN ACCEPT -source +dc/k3s_nodes -p udp -dport 7946 -log nolog       # MetalLB memberlist
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 9345 -log nolog       # k3s supervisor
IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 6443 -log nolog       # Kubernetes API
IN ACCEPT -source +dc/admin_ts -p tcp -dport 6443 -log nolog        # kubectl from Tailscale
IN ACCEPT -source +dc/admin_lan -p tcp -dport 6443 -log nolog       # kubectl from LAN
```

**sg-plex** - Plex Media Server (site data — `proxmox_firewall_security_groups`
in `group_vars/all.yml`):
```ini
[group sg-plex]

IN ACCEPT -source +dc/lan_clients -p tcp -dport 32469 -log nolog      # DLNA
IN ACCEPT -source +dc/lan_clients -p udp -dport 32410:32414 -log nolog # GDM
IN ACCEPT -source +dc/lan_clients -p udp -dport 1900 -log nolog       # SSDP
IN ACCEPT -p tcp -dport 32400 -log nolog                              # Plex Web (public)
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

Host-level inbound drop logging is tunable via `proxmox_firewall_log_level_in`
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
in ~9 policies and "allow apiserver (`10.0.10.222/223/227:6443`)" in ~5. This
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

## Ansible Role

Deploy with: the `weisssrv.infra.proxmox_firewall` role (`ansible/playbooks/site.yml --tags proxmox_firewall`)

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
      ansible_host: 10.0.10.208
      ansible_connection: local  # If not yet managed by Ansible
      firewall_ipsets:
        - k3s_nodes
        - core-cluster
        - nfs_clients
```

**Available IPSets** (`cluster.fw.j2` + the inventory-generated `ipsets.j2` are
the source of truth):
- `pve_hosts` - Proxmox VE hypervisor hosts (generated from the inventory)
- `core-cluster` - All core infrastructure (Proxmox, DNS, SMTP, services, k3s) (generated)
- `k3s_nodes` - Kubernetes nodes (generated)
- `nfs_clients` - Hosts allowed NFS access (generated)
- `admin_lan` - Admin/management sources: the homelab LAN + the Home-VLAN admin
  block (in-template, members from `proxmox_firewall_admin_lan_cidrs`)
- `admin_ts` - The full Tailscale CGNAT range 100.64.0.0/10 (in-template)
- `smb_clients` - Client subnets allowed SMB access
  (`proxmox_firewall_smb_client_cidrs`, in-template)
- `lan_clients` - Service-port scope: homelab LAN + Home VLAN (special entries)
- `dns_clients` - Resolver scope: every VLAN (special entries)

The last two carry no inventory hosts at all — they exist purely as
`firewall_ipset_special_entries` keys, which is how a site declares a named set
of arbitrary CIDRs. See § Client scopes above for what each one is for.

**Special Entries (VIPs, non-host IPs):**

For IPs that aren't inventory hosts (VIPs, floating IPs), add them to `group_vars/all.yml`:

```yaml
firewall_ipset_special_entries:
  k3s_nodes:
    - ip: 10.0.10.161
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
      ansible_host: 10.0.10.150
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
      ansible_host: 10.0.10.206
      vmid: 206
      guest_security_groups:
        - sg-vm-admin         # SSH + ICMP for admin access
        - sg-k3s-core         # K3s cluster communication
        - sg-k3s-ingress-int  # Internal ingress (tailnet + lan_clients)
        - sg-k3s-ingress-pub  # Public ingress (all sources)
        - sg-metrics          # Prometheus exporter scraping
```

**Available Security Groups** (`lib` = rendered by the collection's
`cluster.fw.j2`; `site` = an entry of `proxmox_firewall_security_groups` in
`group_vars/all.yml`):

| Security Group | Source | Purpose | Use For |
|---------------|--------|---------|---------|
| `sg-vm-admin` | lib | SSH + ICMP admin access | **All VMs/LXCs** |
| `sg-dns` | lib | DNS service (DoT, UDP/TCP 53, AdGuard UI) | DNS containers |
| `sg-smtp-relay` | lib | SMTP submission/relay + outbound | smtp-relay container |
| `sg-plex` | site | Plex Media Server ports | Plex container |
| `sg-k3s-core` | lib | K3s cluster communication | All K3s nodes |
| `sg-k3s-ingress-int` | lib | HTTP/HTTPS from `proxmox_firewall_k3s_ingress_int_sources` — here the tailnet + `lan_clients` (Home VLAN), not the admin sets | K3s ingress nodes (internal apps) |
| `sg-k3s-ingress-pub` | lib | HTTP/HTTPS from all sources | K3s ingress nodes (public apps) |
| `sg-gitlab` | site | GitLab HTTP/HTTPS + Git SSH | GitLab VM |
| `sg-nextcloud` | site | HTTPS 443 (Traefik + admin) + nextcloud-exporter 9205 | Nextcloud VM (.156) |
| `sg-immich` | site | HTTPS 443 (Traefik + admin) + Immich telemetry 8081/8082 | Immich VM (.157) |
| `sg-immich-ml` | site | ML inference 3003 from the Immich VM **only** (the API is authless — this rule is the security boundary) | immich-ml LXC (.158) |
| `sg-haos` | site | Home Assistant Web UI (+ `:8123` from IoT) + mDNS | Home Assistant VM |
| `sg-windows` | site | Windows RDP | Windows VMs |
| `sg-metrics` | lib | Prometheus exporter scrape ports from k3s_nodes: the collection's built-ins (9100/9101/9134/9167) plus whatever `proxmox_firewall_metrics_scrape_ports` in `group_vars/all.yml` declares (today 8123/32400/7472/7473, and the Loki push NodePort 31100 from core-cluster) | **All hosts and guests** |

Five more groups are rendered by `cluster.fw.j2` but are **host-only**: they
attach to Proxmox hosts via `host.fw`, never to a guest's
`guest_security_groups`.

| Security group | Purpose |
|---|---|
| `sg-host-admin` | SSH + ICMP + Proxmox UI 8006 from the admin sources |
| `sg-pve-cluster` | corosync + Proxmox cluster traffic between `pve_hosts` |
| `sg-nfs-server` | NFS/RPC from `nfs_clients` (pve-nas-01) |
| `sg-smb-server` | SMB from `smb_clients` (pve-nas-01) |
| `sg-host-egress` | Host-originated egress allowlist, paired with the trailing `OUT DROP` in `host.fw` (via `proxmox_firewall_egress_filtering`) |

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

---

## Related documentation

- [docs/01-overview.md](01-overview.md) — network topology and IP allocation
- [docs/46-unifi-network.md](46-unifi-network.md) — the VLANs and the zone firewall these sets mirror
- [docs/05-tailscale.md](05-tailscale.md) — the tailnet ACL, the other access layer
- [docs/13-ci-cd.md](13-ci-cd.md) — runner network boundaries
- [docs/12-runbooks.md](12-runbooks.md) — connectivity troubleshooting
