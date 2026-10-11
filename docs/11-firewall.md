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
| `dns_clients` | `10.0.10.0/24`, `10.0.20.0/24`, `10.0.30.0/24`, `10.0.40.0/24`, `10.0.50.0/24` | Resolver `:53` only — every *client* VLAN uses the weisssrv resolvers. The Default/mgmt VLAN (`10.0.1.0/24`) is deliberately **not** a member: its DHCP hands out public resolvers and the only `Internal → homelab` policy is an ICMP allow from the switch and AP (`10.0.1.2`/`.3`, the blackbox-probe replies) — which cannot carry `:53`, so nothing from that VLAN can reach a resolver here anyway |

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

Several rule groups the collection renders itself are re-scoped by site
variables rather than by `proxmox_firewall_security_groups`. Each name in a
source list renders as one `+dc/<name>` source in the rule shape the template
already uses, and the two source lists default to `[admin_ts, admin_lan]`, so an
unset site renders exactly what it rendered before.

| Variable | Rules it scopes | Value here |
|---|---|---|
| `proxmox_firewall_dns_client_sources` | `sg-dns` `:53` tcp+udp | `["admin_ts", "dns_clients"]` |
| `proxmox_firewall_k3s_ingress_int_sources` | all of `sg-k3s-ingress-int` (`:80`/`:443`) | `["admin_ts", "lan_clients"]` |
| `proxmox_firewall_dns_admin_ports` | the AdGuard admin surfaces in `sg-dns` (`{port, sources, comment}`) | `:443` from `k3s_nodes, admin_ts, admin_lan`; `:3000` from `admin_ts, admin_lan` |
| `proxmox_firewall_wan_wireguard_vips` | the `-dest`-scoped `:51820/udp` accept in `sg-k3s-ingress-pub` | `[10.0.10.99]`, the wg-easy VIP the router forwards from the WAN ([docs/38](38-wireguard-vpn.md)) |

`admin_ts` stays on both source lists — the tailnet reaches the resolvers and
the internal ingress exactly as before — and `admin_lan` drops off because
`dns_clients` and `lan_clients` both contain it. The two AdGuard admin surfaces
stay on the admin sets: no client VLAN reaches the UI or the plaintext API.

**`admin_ts` is deliberately the full CGNAT range** (`100.64.0.0/10`), not
per-device 100.x pins. Accepted risk: this is a single-owner tailnet
(docs/05-tailscale.md), per-device pins are brittle (onboarding/DR lockout)
and partly moot — subnet-router SNAT means tailnet traffic to guests arrives
as `admin_lan` anyway. Tailnet-side ACL tightening is codified in
`terraform/tailscale/`. The ruling: the tailnet ACL already enforces
device/user granularity, so narrowing the ipset would duplicate that layer
while adding DR-lockout risk. Revisit only if the tailnet ever gains non-admin members.

### Security Groups

Security groups are reusable rule sets, each with a single, clear purpose -
admin access is separated from service-specific rules. Two sources own them, and
neither one is reproduced here:

- **lib** groups are rendered by the collection's `cluster.fw.j2`
  (weisssrv-lib
  `ansible_collections/weisssrv/infra/roles/proxmox_firewall/templates/cluster.fw.j2`,
  plus that role's README). Several take their ports or source sets from site
  variables; the **To** column names the variable where one applies.
- **site** groups are entries of `proxmox_firewall_security_groups` in
  `ansible/inventories/prod/group_vars/all.yml`, whose `rules` are emitted
  verbatim. That file carries the per-rule reasoning.

Read the live result with `pve-firewall compile`, or
`sudo cat /etc/pve/firewall/cluster.fw` on any host.

| Group | Source | Opens | To | Attaches to |
|---|---|---|---|---|
| `sg-host-admin` | lib | Proxmox UI `:8006`, SSH `:22`, ICMP | `admin_ts`, `admin_lan` | Proxmox hosts |
| `sg-pve-cluster` | lib | Proxmox UI `:8006`, SSH `:22`, corosync `:5405`/`:5406` udp | `pve_hosts` | Proxmox hosts |
| `sg-host-egress` | lib | Egress allowlist: DNS/DoT, NTP, apt, Tailscale, SSH, GitLab SSH, SMTP, NFS, Loki push `:31100`, corosync, Proxmox API, ICMP | outbound, any destination | Proxmox hosts, paired with the trailing `OUT DROP` (see Host egress filtering) |
| `sg-nfs-server` | lib | RPC `:111` tcp+udp, NFS `:2049` | `nfs_clients` | pve-nas-01 |
| `sg-smb-server` | lib | SMB `:445` | `smb_clients` | pve-nas-01 |
| `sg-vm-admin` | lib | SSH `:22`, ICMP | `admin_ts`, `admin_lan` | **all VMs and LXCs** |
| `sg-dns` | lib | DoT `:853` tcp+udp; AdGuard admin `:443` and `:3000`; resolver `:53` tcp+udp | admin sets for DoT; `proxmox_firewall_dns_admin_ports` for the admin surfaces; `proxmox_firewall_dns_client_sources` for `:53` | dns-01, dns-02 |
| `sg-smtp-relay` | lib | SMTP `:25` and submission `:587`; an egress allowlist (DNS/DoT, `:587`, apt, Loki `:31100`, ICMP) | `core-cluster` inbound | smtp-relay |
| `sg-k3s-core` | lib | etcd `:2379:2380` + metrics `:2381`, kubelet `:10250`, flannel WireGuard `:51820/udp`, MetalLB memberlist `:7946` tcp+udp, k3s supervisor `:9345`, API `:6443` | `k3s_nodes`, plus `admin_ts`/`admin_lan` on `:6443` | all k3s nodes |
| `sg-k3s-ingress-int` | lib | `:443` then `:80` | `proxmox_firewall_k3s_ingress_int_sources` — here the tailnet + `lan_clients`, not the admin sets | K3s ingress agents (internal apps) |
| `sg-k3s-ingress-pub` | lib | `:443`/`:80`, plus `:51820/udp` scoped by `-dest` to each VIP in `proxmox_firewall_wan_wireguard_vips` (here the wg-easy VIP `10.0.10.99`, which the router forwards from the WAN — [docs/38](38-wireguard-vpn.md)) | any source | K3s ingress agents (public apps) |
| `sg-metrics` | lib | node-exporter-host `:9101` on bare metal; the in-cluster node-exporter DaemonSet `:9100`, which listens on the k3s nodes and on nothing else this group attaches to; zfs-exporter `:9134`, unbound-exporter `:9167`, plus every entry of `proxmox_firewall_metrics_scrape_ports` (today `:8123`, `:32400`, `:7472`, `:7473`, and Loki push `:31100` from `core-cluster`) | `k3s_nodes` | **all hosts and guests** |
| `sg-syslog-vip` | site | UniFi gateway syslog `:514/udp`, scoped `-source 10.0.10.1 -dest 10.0.10.162` (the alloy-syslog MetalLB VIP) | the gateway SVI only | K3s ingress agents |
| `sg-k3s-gitssh` | site | Git SSH `:2222` — Traefik's `gitssh` entrypoint on the internal VIP, forwarded to the GitLab VM's sshd | `lan_clients` | K3s ingress agents |
| `sg-plex` | site | DLNA `:32469`, GDM `:32410:32414`, SSDP `:1900`; media `:32400` | `lan_clients` for discovery; `:32400` from any source (WAN-forwarded, authenticated against plex.tv) | Plex container |
| `sg-gitlab` | site | web `:443`/`:80`, registry `:5050`, Pages `:8443`, Git SSH `:2222` and `:22`, postgres_exporter `:9187` | per rule: `k3s_nodes` for the proxied ports, `lan_clients` for web, admin sets for `:22`, any for `:2222` | GitLab VM (.153) |
| `sg-nextcloud` | site | `:443`, nextcloud-exporter `:9205`, postgres_exporter `:9187` | `k3s_nodes`, `admin_ts`, `lan_clients` | Nextcloud VM (.156) |
| `sg-immich` | site | `:443`, Immich telemetry `:8081`/`:8082`, postgres_exporter `:9187` | `k3s_nodes`, `admin_ts`, `lan_clients` | Immich VM (.157) |
| `sg-immich-ml` | site | ML inference `:3003` | the Immich VM `10.0.10.157` **only** — the API is authless, so this rule is the security boundary | immich-ml LXC (.158) |
| `sg-haos` | site | Home Assistant `:8123`, mDNS `:5353`, SSDP `:1900`, SSH add-on `:22222`, ICMP | `lan_clients`, plus `10.0.30.0/24` inline on `:5353` and `:8123`; admin sets on `:22222` | Home Assistant VM (.154) |
| `sg-windows` | site | RDP `:3389` | `admin_ts`, `admin_lan` | Windows VM (.155) |

A `proxmox_firewall_security_groups` entry must use a name pve-firewall accepts
(a leading letter, then letters, digits, `-` or `_`, 2 to 18 characters), it
must be unique within the list, and it must not reuse one of the `lib` names in
the table above. pve-firewall keys groups by name, so two `[group <name>]`
sections render and one of the two rule sets is silently discarded. No name in
use today collides. The role asserts all three before it renders anything
(`tasks/assert_port_lists.yml`, against `_proxmox_firewall_builtin_groups` and
`_proxmox_firewall_group_name_re` in its `vars/main.yml`), so a collision is a
failed play rather than a silently dropped rule set.

`sg-syslog-vip` has to be a **guest** group, not a `cluster.fw` rule: a frame
addressed to a MetalLB VIP is forwarded to the announcing node's VM and filtered
by that guest's firewall, never by `PVEFW-HOST-IN`. It attaches to every ingress
agent because MetalLB may announce `.162` from any of them.

The cleartext live-migration range (TCP 60000-60050) is deliberately **not**
opened, in or out. `proxmox_ha` pins `migration: type=secure` in
`datacenter.cfg`, so migration rides the SSH tunnel; pre-authorising the range
would make a flip to `insecure` — guest RAM on the wire in the clear —
invisible at the packet filter. `proxmox_firewall_insecure_migration_ports:
true` renders the rules again, and should only ever be set alongside a
deliberate `proxmox_ha_migration_type: insecure`.

`sg-smtp-relay`'s OUT rules are only *enforced* because the smtp-relay guest
sets `guest_firewall_policy_out: "DROP"`. With Proxmox's guest default
(`policy_out ACCEPT`) they are no-ops; conntrack auto-allows replies to inbound
`:25`/`:587` either way.

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

### Firewall implementation per node

PVE 9 ships two implementations of the same rule files. pve-opt-02 runs the
nftables one (`proxmox_firewall_nftables: true` in its `host_vars` renders
`nftables: 1` into its `host.fw`, selecting the `proxmox-firewall` package);
the other five hosts run the classic iptables `pve-firewall`. The choice is per
node and the rule files are identical either way, so the security groups and
ipsets above apply unchanged. nftables takes the bridge-netfilter hook out of
that node's bridged path, which is why opt-02 is the trial host for the
192-byte slab leak ([docs/16](16-next-steps.md)). Read the live result with
`pve-firewall status` there, and confirm guest rules still apply — a VIP-bound
flow is filtered by the *guest* firewall (below), so `sg-syslog-vip` on the
ingress agents is the one to check.

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
GROUP sg-syslog-vip
GROUP sg-k3s-gitssh
GROUP sg-metrics
```

## Kubernetes NetworkPolicies (in-cluster pod egress)

The Proxmox firewall above governs host/VM/LXC traffic. *Inside* the k3s cluster,
pod traffic is governed by Kubernetes NetworkPolicies, all Flux-managed.

- Every namespace runs an **ingress default-deny**, applied by the
  `netpol-baseline` Kustomize component. Two namespaces are documented
  exceptions: `downloads` (its local policy covers ingress *and* egress) and
  `flux-system` (upstream gotk manifests). The canonical list is
  [docs/29](29-flux-operations.md) § Network policy exceptions.
- Egress is an **allowlist per app**, scoped by `app.kubernetes.io/name`. The
  three recurring allowances ship as components — `netpol-egress-dns`,
  `netpol-egress-apiserver`, `netpol-egress-public` — used only where the policy
  they replace already selected the whole namespace; elsewhere the rule stays
  inline. [`kubernetes/components/README.md`](../kubernetes/components/README.md)
  is canonical for that rule and for why the rest is still copy-pasted.
- `scripts/check-netpol-except-parity.py` keeps the reserved-CIDR except-lists
  of the remaining copies identical.

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
        - sg-k3s-ingress-pub  # Public ingress (all sources) + the wg-easy VIP
        - sg-syslog-vip       # UniFi gateway syslog to the alloy-syslog VIP
        - sg-k3s-gitssh       # Git SSH :2222 passthrough to the GitLab VM
        - sg-metrics          # Prometheus exporter scraping
```

**Available security groups**: the full list — what each group opens, the
sources it opens it to and where it attaches — is the table in
[§ Security Groups](#security-groups) above. Five of them (`sg-host-admin`,
`sg-pve-cluster`, `sg-nfs-server`, `sg-smb-server`, `sg-host-egress`) are
host-only: they attach through `host.fw` and never appear in a guest's
`guest_security_groups`.

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
  (`host.fw`) run on each node itself. Per-guest rules (`<vmid>.fw`) use the same
  first-reachable delegate as the cluster tasks; set `firewall_deploy_host` to
  pin a specific node instead
- Emptying a guest's `guest_security_groups` deletes its `<vmid>.fw`
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
task infra:deploy -- --limit dns-01
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
