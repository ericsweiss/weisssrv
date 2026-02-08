# Post-Base Cluster Plan

This document outlines the k3s platform architecture and deployment roadmap. It was originally written to plan the transition from base infrastructure to a production-grade k3s cluster with GitOps.

**Status**: Phases 1-7 are COMPLETE. Phase 8 (HA Expansion) is the next major milestone, followed by Phase 9 (GitLab) and Phase 10 (GitOps with Flux).

## Overview

The k3s platform is deployed on Proxmox-hosted VMs with a layered architecture: cluster bootstrap, ingress/certificates, identity/auth, and application workloads. The cluster is managed via Ansible for VM provisioning and k3s installation, with Task/Helm for workload deployment. Migration to Flux GitOps is planned as the next major phase.

## Guiding Principles

- **Reproducible**: Everything codified (Terraform/Ansible + GitOps for k8s)
- **Least surprise**: Minimal "pet" state; prefer declarative configs and idempotent automation
- **Separation of concerns**:
  - Base infra: Proxmox + storage + network primitives + core services (DNS/SMTP/certs/firewall)
  - Cluster platform: k3s + ingress/LB + GitOps + auth + DNS automation + monitoring/logging
  - Workloads: media + apps
- **Secrets**: Injected at runtime via 1Password (no secrets committed)
- **Fail-safe**: Storage and backups remain usable even if k8s is down

## Domains and Ingress Strategy

### Domain Strategy

| Domain | Purpose | Management | Exposure |
|--------|---------|------------|----------|
| `*.esweiss.com` | Internal services | AdGuard Home rewrites | LAN / VPN (Tailscale) only |
| `*.ericsweiss.com` | External/public services | Cloudflare (Terraform + external-dns) | Internet-facing |

### LoadBalancer VIPs (DEPLOYED)

- **MetalLB Public VIP**: `192.168.0.100` (vip-public) - External ingress
- **MetalLB Internal VIP**: `192.168.0.101` (vip-internal) - Internal services
- **kube-vip API VIP**: `192.168.0.161` - K3s API HA endpoint

### DNS Automation (DEPLOYED)

- **external-dns**: Manages Cloudflare DNS records for `*.ericsweiss.com` automatically from IngressRoute annotations
- **Internal DNS** (`*.esweiss.com`): Managed via AdGuard Home rewrites configured in Ansible

## K3s Cluster Design

### Current Deployment (3 Nodes)

| Node | IP | VMID | Proxmox Host | Role | Status |
|------|-----|------|--------------|------|--------|
| k3s-srv-nas-01 | 192.168.0.222 | 222 | pve-nas-01 | Server (etcd) | ACTIVE |
| k3s-agt-nas-01 | 192.168.0.202 | 202 | pve-nas-01 | NAS workloads | ACTIVE |
| k3s-agt-opt-03 | 192.168.0.206 | 206 | pve-opt-03 | Ingress + general | ACTIVE |

### Future Expansion: Two-Part HA Implementation

The HA implementation covers both the k3s platform and critical base infrastructure:

#### Part 1: K3s HA (5-6 Node Target)

Expand from 1 server to 3+ servers for etcd fault tolerance:
- k3s-srv-laptop-01 (.223) + k3s-srv-prec-01 (.227) for 3-node HA quorum
- Additional agents on opt-01, opt-02, prec-01 for capacity
- kube-vip continues providing API VIP with leader election

#### Part 2: Proxmox HA for Critical Infrastructure

Enable automatic failover for base infrastructure VMs/containers:
- **dns-01** (LXC, 192.168.0.150) - AdGuard Home primary
- **dns-02** (LXC, 192.168.0.160) - AdGuard Home secondary
- **smtp-relay** (LXC, 192.168.0.151) - Mail relay
- **home-assistant** (VM, 192.168.0.154) - Home Assistant OS

These services run outside k3s and require Proxmox-level HA for automatic recovery.

See `docs/25-multi-node-expansion.md` for the complete expansion plan including IP/VMID allocation, Proxmox HA configuration, and ZFS replication setup.

### Node Roles

#### Servers (.22X range - control plane + etcd)

| Node | IP | VMID | Proxmox Host | Resources | Status |
|------|-----|------|--------------|-----------|--------|
| k3s-srv-nas-01 | 192.168.0.222 | 222 | pve-nas-01 | 2 vCPU, 4GB RAM, 64GB disk | ACTIVE |
| k3s-srv-laptop-01 | 192.168.0.223 | 223 | pve-laptop-01 | TBD | PLANNED |
| k3s-srv-prec-01 | 192.168.0.227 | 227 | pve-prec-01 | TBD | PLANNED |

#### Agents (.20X range - workers with specialized roles)

| Node | IP | VMID | Proxmox Host | Role | Status |
|------|-----|------|--------------|------|--------|
| k3s-agt-nas-01 | 192.168.0.202 | 202 | pve-nas-01 | NAS workloads | ACTIVE |
| k3s-agt-laptop-01 | 192.168.0.203 | 203 | pve-laptop-01 | Ingress + general | PLANNED |
| k3s-agt-opt-01 | 192.168.0.204 | 204 | pve-opt-01 | General | PLANNED |
| k3s-agt-opt-02 | 192.168.0.205 | 205 | pve-opt-02 | General | PLANNED |
| k3s-agt-opt-03 | 192.168.0.206 | 206 | pve-opt-03 | Ingress + general | ACTIVE |
| k3s-agt-prec-01 | 192.168.0.207 | 207 | pve-prec-01 | Compute + general | PLANNED |

### Scheduling Model (Labels/Taints)

Namespace: `esweiss.com/*`

**Labels**:
- `esweiss.com/ingress=true` - Node can run ingress controllers
- `esweiss.com/general=true` - Node can run general workloads
- `esweiss.com/nas=true` - Node has fast access to NAS storage
- `esweiss.com/compute=true` - Node can run high-computation tasks (ML, transcoding, etc.)
- `esweiss.com/control-plane=true` - Informational label

**Taints**:
- All server nodes: `node-role.kubernetes.io/control-plane=true:NoSchedule` (etcd + API only)
- NAS agent: `esweiss.com/nas=true:PreferNoSchedule` (prefer NAS workloads, allow overflow)
- Ingress agent (laptop-01): `esweiss.com/ingress=true:PreferNoSchedule` (prefer ingress, allow general overflow)
- Compute agent (prec-01): `esweiss.com/compute=true:PreferNoSchedule` (prefer compute workloads, allow general overflow)

## Platform Components

### Layer 0: Cluster Bootstrap (COMPLETE)

1. **kube-vip** - API server HA
   - Provides floating VIP (192.168.0.161) for k3s API
   - Leader election among control plane nodes
   - Deployed via static pod manifest

2. **MetalLB** - Bare metal LoadBalancer
   - L2 mode for homelab
   - IP pools: 192.168.0.100 (public), 192.168.0.101 (internal)
   - Deployed via Helm

### Layer 1: Ingress & Certificates (COMPLETE)

3. **Traefik** - Ingress controller
   - Deployed via Helm
   - Bound to MetalLB LoadBalancer VIPs
   - Middleware for auth, redirects, WebSocket support

4. **cert-manager** - Certificate automation
   - ACME issuer for Let's Encrypt
   - Cloudflare DNS-01 challenge
   - Wildcard certificates for both domains

5. **external-dns** - DNS automation
   - Syncs IngressRoute hosts to Cloudflare
   - Domain: *.ericsweiss.com
   - Annotation-based filtering

### Layer 2: Identity & Auth (COMPLETE)

6. **Authentik** - SSO/Identity Provider
   - OIDC/OAuth2/SAML for all applications
   - Forward auth for Traefik (protects downloads stack)
   - User management with password-less SSO (OIDC required for apps)
   - PostgreSQL on ZFS zvol for persistence

### Layer 3: Observability (PLANNED)

7. **Prometheus Stack** (kube-prometheus-stack) - PLANNED
   - Prometheus
   - Grafana
   - Alertmanager
   - Node exporters

8. **Loki** - Log aggregation - PLANNED
   - Log collection agents (promtail or vector)
   - Grafana integration

### Layer 4: Storage

9. **NFS Direct Mounts** (current approach)
   - Media stack mounts `/export/media` directly via NFS
   - App data on ZFS zvols attached to k3s VMs
   - Simple, reliable, avoids CSI driver complexity

10. **Future: democratic-csi or nfs-subdir-external-provisioner**
    - Dynamic PVC provisioning if needed
    - Evaluate when application count increases

## GitOps with Flux (PLANNED - Phase 10)

**Note**: Local GitLab (Phase 9) should be deployed before Flux to enable internal GitOps repository hosting. Personal GitHub continues to be used for public repositories and external CI/CD.

### Current State

Deployments are managed via:
- `task k3s:deploy-*` commands that run Helm and kubectl
- Manifests in `kubernetes/apps/` applied via `kubectl apply`
- 1Password integration for secrets at runtime

### Target State

Full GitOps with Flux:
- HelmRelease and Kustomization resources in git
- Automatic reconciliation on git push
- 1Password secrets continue to be injected at runtime (no secrets in git)
- Renovate Bot for automated dependency updates (complements existing `check-versions`)

### Repository Structure (Planned)

```
kubernetes/
  bootstrap/
    kustomization.yaml
    kube-vip/
    metallb/
  flux/
    flux-system/
    sources/
    kustomizations/
  apps/
    base/
      traefik/
      cert-manager/
      external-dns/
      authentik/
      monitoring/
    production/
      traefik/
      cert-manager/
      ...
```

### Flux Bootstrap

```bash
flux bootstrap github \
  --owner=ericsweiss \
  --repository=weisssrv \
  --branch=main \
  --path=kubernetes/flux \
  --personal
```

### Renovate Bot Integration

Renovate Bot complements the existing `task maintenance:check-versions` automation:

- **Keep `check-versions`**: For Ansible-managed infrastructure (AdGuard, Tailscale, Plex, k3s) and centralized version visibility
- **Add Renovate**: For Kubernetes manifests, Helm charts, Terraform providers, and GitHub Actions

See `docs/16-next-steps.md` "Renovate Bot Integration" section for detailed integration strategy.

## Storage Model in K3s

### Downloads and Media (Direct NFS Mounts) - DEPLOYED

NAS-backed NFS mounts for IO-heavy workloads:
- `/export/media` - MergerFS view (combines nvme/media + tank/media) with unified structure:
  - `/export/media/downloads/` - Download client working directories
  - `/export/media/library/` - Organized media library

Media stack (*arr, qBittorrent, NZBGet, Plex) mounts these directly for best performance. The unified `/media` mount enables hardlinking between downloads and library.

### Persistent Database Storage (ZFS Zvols) - DEPLOYED

For applications requiring durable database storage:
- `ssd/appdata/authentik/postgres` - 10GB zvol, ext4, attached to k3s-agt-nas-01
- `ssd/appdata/mealie/postgres` - 32GB zvol, ext4, attached to k3s-agt-nas-01

Zvols are defined in `vm_additional_disks` in hosts.yml, created by proxmox_vm role, formatted/mounted by k3s role. Data survives pod and VM recreation.

### Backups

- Keep primary "source of truth" in Git (manifests)
- For stateful apps: rely on ZFS/NFS snapshotting + app-level exports
- Optionally adopt Velero later if PV backups become necessary

## Application Deployment Status

### Deployed Applications

| Application | Namespace | Domain | Status | Notes |
|-------------|-----------|--------|--------|-------|
| Authentik | authentik | auth.ericsweiss.com | DEPLOYED | SSO for all apps |
| Plex | LXC (not k8s) | plex.esweiss.com | DEPLOYED | LXC container with Traefik ingress |
| Gluetun VPN | downloads | - | DEPLOYED | VPN gateway for download clients |
| NZBGet | downloads | nzbget.esweiss.com | DEPLOYED | Usenet client |
| qBittorrent | downloads | qbittorrent.esweiss.com | DEPLOYED | BitTorrent client |
| Prowlarr | downloads | prowlarr.esweiss.com | DEPLOYED | Indexer manager |
| Sonarr | downloads | tv.esweiss.com | DEPLOYED | TV show management |
| Radarr | downloads | movies.esweiss.com | DEPLOYED | Movie management |
| Lidarr | downloads | music.esweiss.com | DEPLOYED | Music management |
| Pulsarr | downloads | pulsarr.esweiss.com | DEPLOYED | Plex Watchlist automation |
| Mealie | recipes | food.esweiss.com | DEPLOYED | Recipe management |
| Bar Assistant | recipes | bar.esweiss.com | DEPLOYED | Cocktail recipes |
| Home Assistant | VM (not k8s) | home.esweiss.com | DEPLOYED | HAOS VM with Traefik ingress |

### Planned Applications

| Application | Namespace | Domain | Priority | Notes |
|-------------|-----------|--------|----------|-------|
| GitLab | gitlab | gitlab.esweiss.com | Priority 2 | Self-hosted Git/CI/Registry |
| Prometheus/Grafana | monitoring | grafana.esweiss.com | Priority 5 | Observability stack |
| Loki | monitoring | - | Priority 5 | Log aggregation |
| Uptime Kuma | monitoring | status.esweiss.com | Priority 5 | Status page |
| Immich | photos | photos.esweiss.com | Priority 6 | Photo management |
| Nextcloud | cloud | cloud.esweiss.com | Priority 6 | File sync |

## Milestones / Sequencing

### Completed

1. [x] Base infra parity codified + documented
2. [x] K3s VM provisioning (Terraform/Ansible)
3. [x] K3s bootstrap + kube-vip + MetalLB + Traefik
4. [x] Platform services: Authentik + cert-manager + external-dns
5. [x] Workloads: Plex (NAS LXC) + download/*arr stack
6. [x] Recipe stack: Mealie + Bar Assistant with SSO
7. [x] Home Assistant: HAOS VM with Traefik ingress and SSO

### In Progress / Planned

8. [ ] **HA Expansion** (Two-Part Implementation):
   - **K3s HA**: Add 2 more server nodes for 3-node etcd quorum (5-6 node target)
   - **Proxmox HA**: Enable automatic failover for critical infrastructure (dns-01, dns-02, smtp-relay, home-assistant)
9. [ ] **GitLab**: Self-hosted Git + CI/CD + Container Registry
10. [ ] **GitOps Controller** (Flux) + Renovate Bot
11. [ ] **Observability**: Prometheus + Grafana + Loki
12. [ ] **Photos/Cloud**: Immich + Nextcloud deployment
13. [ ] **Hardening**: Network policies, RBAC, backup validation drills

## Placement Rules (Workload Scheduling)

Use `nodeSelector` / `nodeAffinity` and `tolerations`:
- **Ingress workloads** -> `esweiss.com/ingress=true`
- **General workloads** -> `esweiss.com/general=true`
- **NAS-adjacent workloads** -> `esweiss.com/nas=true`
- Always set resource requests; use limits only where useful
- Apply tolerations only when needed

## Security Considerations

### Network Policies (PLANNED)

- Default deny ingress
- Explicit allow for required traffic
- Namespace isolation

### RBAC

- Minimal service account permissions
- Authentik integration for kubectl access (future)

### Secrets Management

Current: 1Password with `op run` for runtime injection
Future: External Secrets Operator with 1Password Connect backend

## DNS Strategy

### Internal (*.esweiss.com)

- Managed via AdGuard Home rewrites
- Updated by Ansible when VMs change
- No external exposure

### External (*.ericsweiss.com)

- Managed by external-dns
- Cloudflare DNS backend
- Automatic record creation from Ingress

### Split-Horizon

```
Client on LAN:
  app.ericsweiss.com -> 192.168.0.100 (via AdGuard rewrite)

Client on Internet:
  app.ericsweiss.com -> public IP (via Cloudflare)
  -> port forward -> 192.168.0.100
```

## Open Questions (Immich + Nextcloud)

### Immich

- **Exposure**: internal-only (`*.esweiss.com`) vs also external (`*.ericsweiss.com`)?
- **Storage layout**: store originals under `tank/photos`, or dedicate a dataset?
- **Performance**: acceptable on NFS-backed storage for DB + uploads, or DB on local SSD?
- **ML/acceleration**: GPU/TPU acceleration later (plan labels/taints now)?
- **Backup/retention**: ZFS snapshots only + periodic DB dumps? Restore procedure expectations?

### Nextcloud

- **Primary use**: file sync/share only, or also office/collab (Collabora/OnlyOffice)?
- **Exposure**: internal-only vs external?
- **Storage**: Nextcloud data in `tank/share` (existing SMB) or dedicated dataset?
- **Auth**: integrate with Authentik OIDC/SAML from day 1?
- **Backups**: snapshot schedule + DB dumps + config export expectations?

## References

- [k3s Documentation](https://docs.k3s.io/)
- [Flux Documentation](https://fluxcd.io/docs/)
- [Authentik Documentation](https://goauthentik.io/docs/)
- [cert-manager Documentation](https://cert-manager.io/docs/)
- [Renovate Bot Documentation](https://docs.renovatebot.com/)
- `docs/16-next-steps.md` - Prioritized TODO list
- `docs/19-k3s-deployment.md` - K3s cluster deployment workflow
- `docs/25-multi-node-expansion.md` - Multi-node HA expansion guide
