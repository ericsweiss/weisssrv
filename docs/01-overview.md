# Architecture Overview

## Network Topology

```
Internet
    |
[Gateway/Firewall] - UniFi UCG-Fiber (192.168.0.1 on VLAN 10; 10.0.1.1 on VLAN 1)
    |
[USW-Pro-XG-8-PoE] --- trunk (native VLAN 1, tagged 10/20/30/40/50)
    |                       |
    |                       +-- [U7 Pro XGS] --- wireless: Home/IoT/Guest/Work
    |
[VLAN 10 - 192.168.0.0/24] ---- Homelab (hosts, guests, k3s, VIPs)
    |
    +-- Proxmox Cluster (6 nodes, cluster name: weisssrv)
    |   +-- pve-nas-01    (192.168.0.102) - NAS + Storage
    |   +-- pve-laptop-01 (192.168.0.103) - Compute
    |   +-- pve-opt-01    (192.168.0.104) - Compute
    |   +-- pve-opt-02    (192.168.0.105) - Compute
    |   +-- pve-opt-03    (192.168.0.106) - Compute
    |   +-- pve-prec-01   (192.168.0.107) - Compute (Dell Precision 3630, 62G RAM, GTX 1660 Ti → VM 207 via VFIO; docs/43)
    |
    +-- Infrastructure LXC (HA-managed)
    |   +-- dns-01        (192.168.0.150) - Primary DNS (AdGuard + Unbound)
    |   +-- dns-02        (192.168.0.160) - Secondary DNS (AdGuard + Unbound)
    |   +-- smtp-relay    (192.168.0.151) - Mail relay (Postfix -> Gmail)
    |
    +-- Application LXC
    |   +-- plex          (192.168.0.152) - Plex Media Server (plex.esweiss.com)
    |   +-- immich-ml     (192.168.0.158) - Immich GPU ML (OpenVINO, Arc B580 /dev/dri shared with plex; docs/36)
    |
    +-- Application VMs
    |   +-- gitlab        (192.168.0.153) - GitLab EE (git.esweiss.com)
    |   +-- home-assistant(192.168.0.154) - Home Assistant OS (HA-managed)
    |   +-- windows       (192.168.0.155) - Windows 11 VM (IaC-provisioned shell, interactive install; docs/39)
    |   +-- nextcloud     (192.168.0.156) - Nextcloud (cloud.esweiss.com; docs/35)
    |   +-- immich        (192.168.0.157) - Immich (photos.esweiss.com; docs/36)
    |
    +-- K3s Cluster VMs (9 nodes: 3 servers + 6 agents)
    |   +-- Servers (.22X range - control plane + etcd)
    |   |   +-- k3s-srv-nas-01    (192.168.0.222) - Server + etcd
    |   |   +-- k3s-srv-laptop-01 (192.168.0.223) - Server + etcd
    |   |   +-- k3s-srv-prec-01   (192.168.0.227) - Server + etcd
    |   +-- Agents (.20X range - workers)
    |       +-- k3s-agt-nas-01    (192.168.0.202) - Agent (NAS workloads)
    |       +-- k3s-agt-laptop-01 (192.168.0.203) - Agent (ingress + general)
    |       +-- k3s-agt-opt-01    (192.168.0.204) - Agent (ingress + general)
    |       +-- k3s-agt-opt-02    (192.168.0.205) - Agent (ingress + general)
    |       +-- k3s-agt-opt-03    (192.168.0.206) - Agent (ingress + general)
    |       +-- k3s-agt-prec-01   (192.168.0.207) - Agent (general + compute + GPU; 30G, 1660 Ti for Hindsight; docs/43)
    |
    +-- Virtual IPs
        +-- vip-wg-easy   (192.168.0.99)  - MetalLB wg-easy VPN endpoint (UDP)
        +-- vip-public    (192.168.0.100) - MetalLB public ingress
        +-- vip-internal  (192.168.0.101) - MetalLB internal services
        +-- k3s-api       (192.168.0.161) - kube-vip K3s API HA endpoint
```

### VLANs

The estate is segmented by the UniFi tier — one zone per VLAN, inter-zone
default-deny, everything the provider supports codified in `terraform/unifi/`.
[docs/46-unifi-network.md](46-unifi-network.md) is canonical for the zone
policy matrix, the physical port map, and the cutover/validation runbook.

| VLAN | Network | Subnet | Gateway | DHCP pool | Carries |
|---|---|---|---|---|---|
| 1 | Default (mgmt) | 10.0.1.0/24 | 10.0.1.1 | .100-.199 | Gateway, switch (.2), AP (.3) — public DHCP DNS, not the weisssrv resolvers |
| 10 | Homelab | 192.168.0.0/24 | 192.168.0.1 | .2-.98 | Everything in the tree above (Phase 2 renumbers this to 10.0.10.0/24) |
| 20 | Home | 10.0.20.0/24 | 10.0.20.1 | .50-.199 | Personal client devices; `10.0.20.8/29` is the admin-device block; reservations sit above the pool ([docs/46 § DHCP reservations](46-unifi-network.md)) |
| 30 | IoT | 10.0.30.0/24 | 10.0.30.1 | .50-.99 | TVs, WLED, Kasa, Hue, Hyperion — reservations sit above the pool |
| 40 | Guest | 10.0.40.0/24 | 10.0.40.1 | .50-.249 | Guest WLAN — DNS out, nothing else |
| 50 | Work | 10.0.50.0/24 | 10.0.50.1 | .50-.249 | Work devices — DNS out, nothing else |

Every **client** VLAN resolves through the weisssrv resolvers (`.150`/`.160`,
handed out by UniFi DHCP), so split-horizon DNS behaves identically on all of
them; the management VLAN gets public resolvers instead, because the gear on it
sits above the LXCs that serve DNS ([docs/08](08-dns.md)).
pve-nas-01 reaches VLAN 10 over a tagged sub-interface (`vmbr0` bridges
`nic1.10`) because its run shares a port with Home; every other host is on an
untagged VLAN 10 access port.

**NIC note**: the three OptiPlex compute nodes (pve-opt-01/02/03, .104/.105/.106)
uplink through a **2-NIC active-backup bond** (`nic0`/`nic1`, hand-maintained in
`/etc/network/interfaces`); the other hosts are single-NIC. See
[docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md).

### IP Allocation Strategy

| Range | Purpose | Status |
|-------|---------|--------|
| 192.168.0.1-98 | Infrastructure, DHCP, workstations | Active |
| 192.168.0.99-101 | MetalLB VIPs (`vpn-pool` .99 / public .100 / internal .101) | Active |
| 192.168.0.102-109 | Proxmox hosts | Active (.102-.107) |
| 192.168.0.150-159 | Infrastructure services (DNS, SMTP, apps) | Active (.150-.158) |
| 192.168.0.160-169 | Additional infrastructure services | Active (.160 dns-02) |
| 192.168.0.200-207 | K3s agent VMs (subnet: 192.168.0.200/29) | Active (.202-.207) |
| 192.168.0.220-227 | K3s server VMs (.222/.223 in 192.168.0.220/29; .227 is outside it) | Active (.222, .223, .227) |

**Note**: K3s VM subnets are allowlisted in NFS exports for secure access to
storage. Mind the CIDR gotcha: `192.168.0.220/29` masks to .216–.223 and does
**not** cover server .227 (prec-01), so every export that needs .227 carries an
explicit `192.168.0.227/32` line alongside the two /29 blocks — the RW exports
(`/export/{appdata,share,media}`) as well as the fsid=0 pseudo-root and
`/export/k3s-etcd`. `host_vars/pve-nas-01.yml` is the source of truth.

## Service Architecture

### DNS Stack

```
LAN Clients
    |
    v
AdGuard Home (dns-01/dns-02)
    |-- Ad blocking
    |-- DNS rewrites (*.esweiss.com)
    |-- Query logging
    v
Unbound (localhost:5335)
    |-- DNSSEC validation
    |-- DoT upstream
    v
Cloudflare/Google DoT
```

### Mail Flow

```
Proxmox Hosts / LXC Containers
    |
    | (localhost only)
    v
Postfix Null Client
    |
    | SMTP/TLS :587
    v
smtp-relay.esweiss.com (192.168.0.151)
    |
    | SMTP/TLS :587
    v
smtp.gmail.com
    |
    v
External Recipients
```

### Storage Tiers

```
Hot Tier (NVMe)                 Cold Tier (HDD)
/mnt/nvme/media                 /mnt/tank/media
    |                               ^
    |                               |
    +-------[MergerFS]--------------+
                |
                v
         /mnt/media (MergerFS union)
                |
                v
         /export/media (NFS bind mount)
```

## Domain Strategy

| Domain | Purpose | Management |
|--------|---------|------------|
| `esweiss.com` | Internal services | AdGuard Home rewrites |
| `ericsweiss.com` | External/public | Cloudflare DNS (Terraform) |

## Security Layers

1. **Proxmox Firewall**: IPsets and security groups control inter-host traffic (default-deny policy)
2. **SSH Hardening**: Key-only auth, fail2ban on all hosts
3. **TLS Everywhere**: ACME certificates for internal services
4. **Secrets Management**: 1Password CLI for runtime secret injection
5. **Network segmentation**: UniFi VLANs with a zone-per-VLAN firewall — client
   devices (Home/IoT/Guest/Work) reach service ports only, and the management
   plane only from the admin block ([docs/46](46-unifi-network.md),
   [docs/11](11-firewall.md))

## Implementation Status

Base infrastructure, the 9-node k3s platform, and the application stacks are
all deployed and production-ready. Status checklists and the roadmap
(including planned apps) live in [16-next-steps.md](16-next-steps.md) — the
canonical home for implementation status.

---

## Related documentation

- [docs/00-hardware-setup.md](00-hardware-setup.md) — hardware inventory and Proxmox install
- [docs/46-unifi-network.md](46-unifi-network.md) — the UniFi tier: VLANs, zone firewall, port map, cutover runbook
- [docs/11-firewall.md](11-firewall.md) — firewall IP sets and security groups
- [docs/19-k3s-deployment.md](19-k3s-deployment.md) — the k3s cluster layer
- [docs/16-next-steps.md](16-next-steps.md) — remaining work and accepted risks
