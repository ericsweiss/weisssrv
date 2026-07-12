# Proxmox Firewall Role

Manages Proxmox VE firewall at cluster, host, and guest levels. Configures IPSets for network groupings and Security Groups for reusable rule templates, plus the `monitoring@pve` user/ACL/API-token used by the Prometheus exporters.

## What This Role Manages

### Cluster Firewall (/etc/pve/firewall/cluster.fw)
- Global firewall options (`policy_in: DROP`, `policy_out: ACCEPT`)
- IPSet definitions: `admin_lan`, `admin_ts`, `smb_clients` are static in
  `templates/cluster.fw.j2`; the rest (`core-cluster`, `k3s_nodes`,
  `pve_hosts`, `nfs_clients`, …) are rendered from inventory (see below)
- Security Group definitions — **edited directly in `templates/cluster.fw.j2`**,
  not driven by a variable. The full group inventory with per-rule rationale is
  documented in `docs/11-firewall.md`
- Cluster-wide rules (`pve_firewall_cluster_rules`, default empty)

### Host Firewall (/etc/pve/nodes/{node}/host.fw)
- Per-host firewall enable + base group references (sg-pve-cluster,
  sg-host-admin, sg-metrics; NAS hosts add sg-nfs-server/sg-smb-server)
- Optional host egress allowlist + trailing OUT DROP (see "Egress filtering")
- Inbound drop logging via `pve_firewall_log_level_in` (default `nolog`; flip
  to `info` for triage of dropped inbound traffic — policy_in is DROP)
- Host-specific extra rules (`pve_firewall_host_rules`, default empty)

### Guest Firewall (/etc/pve/firewall/{vmid}.fw)
- Per-VM/LXC firewall configuration: `enable: 1` + one `GROUP <sg>` line per
  entry in the guest's `guest_security_groups`
- Optional `policy_out` (`guest_firewall_policy_out`, e.g. `DROP` to turn a
  group's OUT ACCEPT rules into an enforced egress allowlist)

### Monitoring user and API token (pveum)
- Creates the `monitoring@pve` user, grants `PVEAuditor` at `/`, and creates
  the `monitoring@pve!exporter` API token (`--privsep 0`) — run once per
  invocation, cluster-wide. The token secret is only printed at creation; the
  role discards it (`no_log`) and tells the operator how to recover/rotate it
  (`/etc/pve/priv/token.cfg`, or token remove + add) into the
  "Proxmox API Token" 1Password item.

## Configuration

There is no `security_groups` variable and no dict-style `firewall_ipsets`
map — groups live in the template, and IPSet membership is declared per host:

```yaml
# In hosts.yml — per-HOST membership list: each named IPSet gains this
# host's ansible_host IP. templates/ipsets.j2 renders every discovered set.
dns-01:
  firewall_ipsets:
    - core-cluster
  guest_security_groups:   # rendered into /etc/pve/firewall/<vmid>.fw
    - sg-vm-admin
    - sg-dns

# In group_vars/all.yml — non-host entries (VIPs etc.) per IPSet:
firewall_ipset_special_entries:
  k3s_nodes:
    - ip: 192.168.0.161
      comment: k3s API VIP
```

Named aliases (`pve_firewall_aliases`) and the log level
(`pve_firewall_log_level_in`) are defined in `defaults/main.yml`.

To add or change a **security group**, edit `templates/cluster.fw.j2` and keep
`docs/11-firewall.md` in sync — that doc is the canonical group inventory.

## Deployment

The role is tagged `proxmox_firewall` in the playbooks:

```bash
# Deploy firewall everywhere it applies
ansible-playbook ansible/playbooks/site.yml --tags proxmox_firewall

# Deploy to Proxmox hosts only
ansible-playbook ansible/playbooks/site.yml --tags proxmox_firewall --limit proxmox

# Deploy guest firewall rules for the DNS containers
ansible-playbook ansible/playbooks/site.yml --tags proxmox_firewall --limit dns
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

- `tasks/main.yml` - Main orchestration (firewall configs + pveum monitoring user/token)
- `tasks/guest.yml` - Per-guest firewall deployment (included when `vmid` is set)
- `templates/cluster.fw.j2` - Cluster firewall with IPSets and Security Groups
- `templates/host.fw.j2` - Per-host firewall rules
- `templates/guest.fw.j2` - Per-guest firewall rules
- `templates/ipsets.j2` - IPSet generation from inventory membership lists

## Dependencies

None - foundational role

## Security

- Default deny with explicit allow rules
- Separate admin access (LAN + Tailscale)
- Service-specific Security Groups
- Per-guest isolation with opt-in networking

## Egress filtering

Inbound is default-deny; host-originated **egress** is `ACCEPT` unless
`proxmox_firewall_egress_filtering` is set (role default `false`; **production
enables it for all six Proxmox hosts** via `group_vars/proxmox.yml`). When
enabled it applies the
`sg-host-egress` allowlist (DNS/NTP/HTTP(S)/Tailscale/corosync/SSH/NFS/SMTP/
migration) and appends an explicit trailing `OUT DROP` rule in `host.fw`.
`pve-firewall` honours OUT *rules* in `host.fw` but **ignores** the host-level
`policy_out` option (that key is only effective in `cluster.fw`), so the trailing
`OUT DROP` rule — not a policy setting — is what enforces default-deny. Guest
traffic is unaffected by the host rules; guests opt in separately via
`guest_firewall_policy_out: "DROP"` (smtp-relay does, turning its
`sg-smtp-relay` OUT rules into an enforced egress allowlist).

Rolling out to a new host (or re-enabling after an opt-out) — a missing
allowlist entry can break a node or remote access:

1. Set `proxmox_firewall_egress_filtering: true` in the host's `host_vars`
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
