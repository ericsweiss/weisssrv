# Architecture Overview

This document describes the architecture of the weisssrv homelab infrastructure.

## Network Topology

```
Internet
    |
[Router/Firewall] - Asus GT-AX11000 Pro (192.168.0.1)
    |
[192.168.0.0/24] ---- Core LAN
    |
    +-- Proxmox Cluster
    |   +-- pve-nas-01    (192.168.0.102) - NAS + Storage (active)
    |   +-- pve-laptop-01 (192.168.0.103) - Compute (future)
    |   +-- pve-opt-01    (192.168.0.104) - Compute (future)
    |   +-- pve-opt-02    (192.168.0.105) - Compute (future)
    |   +-- pve-opt-03    (192.168.0.106) - Compute (active)
    |   +-- pve-prec-01   (192.168.0.107) - Compute (future, Dell Precision 3630)
    |
    +-- Infrastructure LXC
    |   +-- dns-01        (192.168.0.150) - Primary DNS (AdGuard + Unbound)
    |   +-- dns-02        (192.168.0.160) - Secondary DNS (AdGuard + Unbound)
    |   +-- smtp-relay    (192.168.0.151) - Mail relay (Postfix → Gmail)
    |
    +-- K3s Cluster VMs
    |   +-- Servers (.22X range - control plane + etcd)
    |   |   +-- k3s-srv-nas-01    (192.168.0.222) - Server + etcd (active)
    |   |   +-- k3s-srv-laptop-01 (192.168.0.223) - Server + etcd (future HA)
    |   |   +-- k3s-srv-prec-01   (192.168.0.227) - Server + etcd (future HA)
    |   +-- Agents (.20X range - workers)
    |   |   +-- k3s-agt-nas-01    (192.168.0.202) - Agent (NAS workloads)
    |   |   +-- k3s-agt-laptop-01 (192.168.0.203) - Agent (ingress + general, future)
    |   |   +-- k3s-agt-opt-01    (192.168.0.204) - Agent (general, future)
    |   |   +-- k3s-agt-opt-02    (192.168.0.205) - Agent (general, future)
    |   |   +-- k3s-agt-opt-03    (192.168.0.206) - Agent (ingress + general, active)
    |   |   +-- k3s-agt-prec-01   (192.168.0.207) - Agent (general + compute, future)
    |
    +-- Virtual IPs (Reserved)
        +-- vip-public    (192.168.0.100) - MetalLB public ingress
        +-- vip-internal  (192.168.0.101) - MetalLB internal services
        +-- k3s-api       (192.168.0.161) - kube-vip K3s API HA endpoint
```

### IP Allocation Strategy

| Range | Purpose | Status |
|-------|---------|--------|
| 192.168.0.1-99 | Infrastructure, DHCP, workstations | Active |
| 192.168.0.100-101 | MetalLB VIPs (public/internal) | Reserved |
| 192.168.0.102-109 | Proxmox hosts | Active (.102, .106) / Reserved (.103-.105, .107-.109) |
| 192.168.0.150-159 | Infrastructure LXCs (DNS, monitoring, etc.) | Active |
| 192.168.0.160-169 | Additional infrastructure services | Partially active |
| 192.168.0.200-207 | K3s agent VMs (subnet: 192.168.0.200/29) | Active (.202, .206) / Reserved |
| 192.168.0.220-227 | K3s server VMs (subnet: 192.168.0.220/29) | Active (.222) / Reserved |

**Note**: K3s VM subnets (192.168.0.200/29 for agents, 192.168.0.220/29 for servers) are allowlisted in NFS exports for secure access to storage.

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

1. **Network Segmentation**: VLANs separate trusted and untrusted traffic
2. **Proxmox Firewall**: IPsets and security groups control inter-host traffic
3. **SSH Hardening**: Key-only auth, fail2ban (future)
4. **TLS Everywhere**: ACME certificates for internal services
5. **Secrets Management**: 1Password CLI for runtime secret injection

## Implementation Status

### Base Infrastructure (Complete)

The base infrastructure automation is complete and production-ready:

**Core Infrastructure**:
- Proxmox cluster (pve-nas-01, pve-opt-03)
- LXC containers (dns-01, dns-02, smtp-relay)
- ZFS storage pools (tank, ssd, nvme, archive)
- Network configuration and firewall rules

**Services Configured**:
- DNS: AdGuard Home + Unbound (HA pair on dns-01, dns-02)
- Certificates: Let's Encrypt via acme.sh with automated distribution
- Mail: SMTP relay (Gmail) with null clients on all hosts
- Storage: ZFS + NFS + Samba + MergerFS with automated media mover
- Security: Proxmox firewall with IPSets and Tailscale VPN
- Monitoring: SMART disk health monitoring and backup jobs

**Automation**:
- 13 Ansible roles covering all infrastructure services
- Terraform for Cloudflare DNS (*.ericsweiss.com)
- 1Password integration for secrets (no secrets in git)
- Comprehensive update playbooks with rolling deployments
- Post-deployment verification checks

**Documentation**:
- 19 comprehensive documentation files
- Operational runbooks covering common procedures
- Disaster recovery procedures
- Maintenance and update strategies

### Next Phase (Planned)

K3s cluster deployment on dedicated VMs:

- [ ] K3s VM provisioning (6 nodes: 3 control plane, 3 workers)
- [ ] K3s cluster bootstrap with kube-vip and MetalLB
- [ ] GitOps with Flux
- [ ] Platform services (Authentik SSO, cert-manager, external-dns)
- [ ] Monitoring stack (Prometheus/Grafana)
- [ ] Application workloads (Plex, *arr stack, Immich/Nextcloud)

See [14-post-base-plan.md](14-post-base-plan.md) for the detailed k3s roadmap.
