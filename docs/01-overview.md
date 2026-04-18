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
    +-- Proxmox Cluster (6 nodes, cluster name: weisssrv)
    |   +-- pve-nas-01    (192.168.0.102) - NAS + Storage
    |   +-- pve-laptop-01 (192.168.0.103) - Compute
    |   +-- pve-opt-01    (192.168.0.104) - Compute
    |   +-- pve-opt-02    (192.168.0.105) - Compute
    |   +-- pve-opt-03    (192.168.0.106) - Compute
    |   +-- pve-prec-01   (192.168.0.107) - Compute (Dell Precision 3630)
    |
    +-- Infrastructure LXC (HA-managed)
    |   +-- dns-01        (192.168.0.150) - Primary DNS (AdGuard + Unbound)
    |   +-- dns-02        (192.168.0.160) - Secondary DNS (AdGuard + Unbound)
    |   +-- smtp-relay    (192.168.0.151) - Mail relay (Postfix -> Gmail)
    |
    +-- Application LXC
    |   +-- plex          (192.168.0.152) - Plex Media Server (plex.esweiss.com)
    |
    +-- Application VMs
    |   +-- gitlab        (192.168.0.153) - GitLab EE (git.esweiss.com)
    |   +-- home-assistant(192.168.0.154) - Home Assistant OS (HA-managed)
    |
    +-- K3s Cluster VMs (9 nodes: 3 servers + 6 agents)
    |   +-- Servers (.22X range - control plane + etcd)
    |   |   +-- k3s-srv-nas-01    (192.168.0.222) - Server + etcd
    |   |   +-- k3s-srv-laptop-01 (192.168.0.223) - Server + etcd
    |   |   +-- k3s-srv-prec-01   (192.168.0.227) - Server + etcd
    |   +-- Agents (.20X range - workers)
    |       +-- k3s-agt-nas-01    (192.168.0.202) - Agent (NAS workloads)
    |       +-- k3s-agt-laptop-01 (192.168.0.203) - Agent (ingress + general)
    |       +-- k3s-agt-opt-01    (192.168.0.204) - Agent (general)
    |       +-- k3s-agt-opt-02    (192.168.0.205) - Agent (general)
    |       +-- k3s-agt-opt-03    (192.168.0.206) - Agent (ingress + general)
    |       +-- k3s-agt-prec-01   (192.168.0.207) - Agent (general + compute)
    |
    +-- Virtual IPs
        +-- vip-public    (192.168.0.100) - MetalLB public ingress
        +-- vip-internal  (192.168.0.101) - MetalLB internal services
        +-- k3s-api       (192.168.0.161) - kube-vip K3s API HA endpoint
```

### IP Allocation Strategy

| Range | Purpose | Status |
|-------|---------|--------|
| 192.168.0.1-99 | Infrastructure, DHCP, workstations | Active |
| 192.168.0.100-101 | MetalLB VIPs (public/internal) | Active |
| 192.168.0.102-109 | Proxmox hosts | Active (.102-.107) |
| 192.168.0.150-155 | Infrastructure services (DNS, SMTP, apps) | Active |
| 192.168.0.160-169 | Additional infrastructure services | Active (.160 dns-02) |
| 192.168.0.200-207 | K3s agent VMs (subnet: 192.168.0.200/29) | Active (.202-.207) |
| 192.168.0.220-227 | K3s server VMs (subnet: 192.168.0.220/29) | Active (.222, .223, .227) |

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

1. **Proxmox Firewall**: IPsets and security groups control inter-host traffic (default-deny policy)
2. **SSH Hardening**: Key-only auth, fail2ban on all hosts
3. **TLS Everywhere**: ACME certificates for internal services
4. **Secrets Management**: 1Password CLI for runtime secret injection
5. **Future**: Network segmentation with VLANs (IoT, guest, management) planned

## Implementation Status

### Base Infrastructure (Complete)

The base infrastructure automation is complete and production-ready:

**Core Infrastructure**:
- Proxmox cluster (6 nodes: pve-nas-01, pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01)
- LXC containers (dns-01, dns-02, smtp-relay)
- ZFS storage pools (tank, ssd, nvme, archive on NAS; local-ssd on compute nodes)
- Network configuration and firewall rules

**Services Configured**:
- DNS: AdGuard Home + Unbound (HA pair on dns-01, dns-02)
- Certificates: Let's Encrypt via acme.sh with automated distribution
- Mail: SMTP relay (Gmail) with null clients on all hosts
- Storage: ZFS + NFS + Samba + MergerFS with automated media mover
- Security: Proxmox firewall with IPSets and Tailscale VPN
- Monitoring: SMART disk health monitoring and backup jobs

**Automation**:
- 21 Ansible roles covering all infrastructure services
- Terraform for Cloudflare DNS (*.ericsweiss.com)
- 1Password integration for secrets (no secrets in git)
- Update playbooks with rolling deployments
- Post-deployment verification checks

**Documentation**:
- 31 documentation files
- Operational runbooks covering common procedures
- Disaster recovery procedures
- Maintenance and update strategies

### K3s Platform (Active)

9-node K3s cluster with full platform services:

- [x] K3s cluster (3 servers + 6 agents) with etcd quorum
- [x] kube-vip for API HA (192.168.0.161)
- [x] MetalLB for LoadBalancer services
- [x] Traefik ingress, cert-manager, external-dns
- [x] Authentik SSO identity provider
- [x] Application workloads (*arr stack, Mealie, Bar Assistant)
- [x] Plex Media Server (LXC container with Traefik ingress)
- [x] GitLab EE with container registry and CI/CD runners
- [x] Home Assistant with Traefik ingress and SSO
- [x] GitOps with Flux (deployed)
- [ ] Future apps: Immich, Nextcloud

See [16-next-steps.md](16-next-steps.md) for the detailed roadmap.
