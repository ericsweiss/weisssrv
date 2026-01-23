# Post-Base Cluster Plan

This document outlines the k3s platform and application deployment planned after achieving base infrastructure parity (Proxmox + ZFS/NFS/Samba + DNS + SMTP relay + firewall + cert pipeline). It's written to be directly translatable into GitOps (Terraform/Ansible + Kubernetes manifests/Helm + CI).

## Overview

Once the base infrastructure is codified and idempotent, the next major phase is deploying a production-grade k3s cluster with GitOps.

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

### LoadBalancer VIPs

- **MetalLB Public VIP**: `192.168.0.100` (vip-public) - External ingress
- **MetalLB Internal VIP**: `192.168.0.101` (vip-internal) - Internal services
- **kube-vip API VIP**: `192.168.0.161` - K3s API HA endpoint

### DNS Automation

- **external-dns** (REQUIRED):
  - Manages Cloudflare DNS records for `*.ericsweiss.com`
  - Automatically creates records from Ingress annotations
  - Minimum scope: externally exposed ingresses
- **Internal DNS** (`*.esweiss.com`):
  - Remains managed via AdGuard Home rewrites/host records
  - Future: consider split-horizon automation or second external-dns instance

## K3s Cluster Design

### VM Provisioning

All k3s VMs will be provisioned on Proxmox with Ubuntu 22.04 LTS or Debian 12.

**Automated Configuration**:
- User `eric` created with SSH key (from 1Password)
- NOPASSWD sudo configured automatically via `base` role
- SSH hardening applied
- Common packages installed
- Timezone and locale configured

**No manual bootstrap required** - unlike existing Proxmox hosts, new VMs will have user and sudo configuration handled by Ansible on first run.

### Node Roles

| Node | IP | Role | Resources |
|------|-----|------|-----------|
| k3s-srv-nas-01 | 192.168.0.202 | Server (etcd) | 2 vCPU, 4GB RAM, 64GB disk |
| k3s-srv-laptop-01 | 192.168.0.203 | Server (etcd) + Ingress + General | 3 vCPU, 6GB RAM, 64GB disk |
| k3s-srv-opt-01 | 192.168.0.204 | Server (etcd) + Ingress | 3 vCPU, 6GB RAM, 64GB disk |
| k3s-agt-opt-02 | 192.168.0.205 | Agent + Ingress + General | 3 vCPU, 6GB RAM, 64GB disk |
| k3s-agt-opt-03 | 192.168.0.206 | Agent + Ingress + General | 3 vCPU, 6GB RAM, 64GB disk |
| k3s-agt-nas-01 | 192.168.0.207 | Agent (NAS) | 4 vCPU, 8GB RAM, 64GB disk |

**Note**: Enable memory ballooning with a reasonable minimum to prevent k3s being squeezed.

### Virtual IPs

| VIP | IP | Purpose |
|-----|-----|---------|
| vip-public | 192.168.0.100 | External ingress (Traefik) |
| vip-internal | 192.168.0.101 | Internal services |
| k3s | 192.168.0.161 | K3s API server (kube-vip) |

### Scheduling Model (Labels/Taints)

Namespace: `esweiss.com/*`

**Labels**:
- `esweiss.com/ingress=true` - Node can run ingress controllers
- `esweiss.com/general=true` - Node can run general workloads
- `esweiss.com/nas=true` - Node has fast access to NAS storage
- `esweiss.com/control-plane=true` - Informational label

**Taints**:
- NAS server control-plane: `NoSchedule` (etcd + API only)
- Other servers (k3s-srv-opt-01, k3s-srv-laptop-01): control-plane `PreferNoSchedule` (allow overflow)
- NAS agent: `esweiss.com/nas=true:PreferNoSchedule` (prefer NAS workloads, allow overflow)

**Node Eligibility Plan**:
- `k3s-srv-opt-01` server: ingress-only
- `k3s-srv-laptop-01` server: ingress + general (reserve headroom for HA workloads like Home Assistant)
- `k3s-agt-opt-02` agent: ingress + general
- `k3s-agt-opt-03` agent: ingress + general
- `k3s-agt-nas-01` agent: nas workloads (and optionally general overflow)

## Platform Components

### Layer 0: Cluster Bootstrap

1. **kube-vip** - API server HA
   - Provides floating VIP for k3s API
   - Leader election among control plane nodes

2. **MetalLB** - Bare metal LoadBalancer
   - L2 mode for homelab
   - IP pool: 192.168.0.100-192.168.0.120

### Layer 1: Ingress & Certificates

3. **Traefik** - Ingress controller
   - Deployed via Helm
   - Binds to MetalLB LoadBalancer
   - Middleware for auth, redirects

4. **cert-manager** - Certificate automation
   - ACME issuer for Let's Encrypt
   - Cloudflare DNS-01 challenge
   - Internal CA for mTLS

5. **external-dns** - DNS automation
   - Syncs Ingress hosts to Cloudflare
   - Domain: *.ericsweiss.com
   - Annotation-based filtering

### Layer 2: Identity & Auth

6. **Authentik** - SSO/Identity Provider
   - OIDC/OAuth2/SAML
   - Forward auth for Traefik
   - User management

### Layer 3: Observability

7. **Prometheus Stack** (kube-prometheus-stack)
   - Prometheus
   - Grafana
   - Alertmanager
   - Node exporters

8. **Loki** - Log aggregation
   - Log collection agents (promtail or vector - TBD)
   - Grafana integration

### Layer 4: Storage

9. **democratic-csi** or **nfs-subdir-external-provisioner**
   - Dynamic PVC provisioning
   - NFS backend to pve-nas-01

## GitOps with Flux

### Repository Structure

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

## Storage Model in K3s

### Downloads and Media (Direct NFS Mounts)

Keep NAS-backed NFS mounts for IO-heavy workloads:
- `/export/media` - MergerFS view (combines nvme/media + tank/media) with unified structure:
  - `/export/media/downloads/` - Download client working directories
  - `/export/media/library/` - Organized media library

Media stack (*arr, qBittorrent, NZBGet, Plex) mounts these directly for best performance. The unified `/media` mount enables hardlinking between downloads and library.

### App Data (Dynamic PVCs)

- PVCs provisioned under `/export/appdata` (NFS-backed)
- Use Kubernetes NFS provisioner (e.g., nfs-subdir-external-provisioner or democratic-csi)
- Subdir-per-PVC approach
- Storage classes for "critical" vs "scratch" if needed

### Backups

- Keep primary "source of truth" in Git (manifests)
- For stateful apps: rely on ZFS/NFS snapshotting + app-level exports initially
- Optionally adopt Velero later if PV backups become necessary

## Application Deployment Plan

### Plex (Foundational - NOT Optional)

**Plex runs on NAS** - primary objective is always-available media library access and streaming.

**Placement Options**:
- **Phase 1** (recommended): Run Plex as Proxmox VM/LXC on pve-nas-01 (simple, stable)
- **Phase 2** (optional): Migrate Plex into k3s, pinned to `esweiss.com/nas=true` (NAS worker), still mounting `/export/media`

**Storage**:
- Media: `/export/media` (read-only or read-write)
- Metadata/config: `/export/appdata/plex` or dedicated dataset

**Hardware Acceleration** (future):
- Label nodes with GPU capability if hardware transcoding is added

### Media + Download Stack (NAS-Adjacent)

**Applications**:
- qBittorrent / Transmission - Torrent client
- NZBGet / SABnzbd - Usenet client
- Radarr - Movie management
- Sonarr - TV show management
- Lidarr - Music management
- Prowlarr - Indexer management
- Overseerr / Jellyseerr - Request management

**Scheduling**:
- Prefer `esweiss.com/nas=true` for IO-heavy services
- Mount `/export/media` directly (contains both downloads and library)
- Use NFS provisioner for app config/state

### Photos / Personal Cloud

**Immich** (Photo Management):
- TBD decisions:
  - Exposure: internal-only vs also external?
  - Storage: originals under tank/photos, or dedicated dataset?
  - Performance: NFS-backed DB acceptable, or DB on local SSD?
  - ML/acceleration: GPU/TPU for face recognition?
  - Backup: ZFS snapshots + DB dumps?

**Nextcloud** (File Sync/Share):
- TBD decisions:
  - Primary use: file sync only, or also office/collab (Collabora/OnlyOffice)?
  - Exposure: internal-only vs external?
  - Storage: tank/share (existing SMB) or dedicated dataset?
  - Auth: integrate with Authentik OIDC/SAML from day 1?
  - Backups: snapshot schedule + DB dumps + config export

### Home Automation

- **Home Assistant**: Stays as Proxmox HA workload (not necessarily in k3s)
- **Zigbee2MQTT**: Can run in k3s or alongside Home Assistant

## Application Migration Phases

### Phase 1: Media Stack
- Plex (on NAS VM/LXC initially)
- Sonarr, Radarr, Prowlarr
- qBittorrent/Transmission
- Overseerr

### Phase 2: Photos & Cloud
- Immich (after decisions finalized)
- Nextcloud (after decisions finalized)

### Phase 3: Development (Optional)
- GitLab or GitHub integration
- Harbor (container registry)
- Argo Workflows / Tekton (CI/CD)

## Placement Rules (Workload Scheduling)

Use `nodeSelector` / `nodeAffinity` and `tolerations`:
- **Ingress workloads** → `esweiss.com/ingress=true`
- **General workloads** → `esweiss.com/general=true`
- **NAS-adjacent workloads** → `esweiss.com/nas=true`
- Always set resource requests; use limits only where useful
- Apply tolerations only when needed

## Security Considerations

### Network Policies

- Default deny ingress
- Explicit allow for required traffic
- Namespace isolation

### RBAC

- Minimal service account permissions
- Authentik integration for kubectl access

### Secrets Management

- External Secrets Operator
- 1Password Connect backend

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

## Milestones / Sequencing

1. Base infra parity codified + documented (COMPLETE)
2. K3s VM provisioning (Terraform/Ansible) (COMPLETE)
3. K3s bootstrap + kube-vip + MetalLB + Traefik (COMPLETE)
4. Platform services: Authentik + cert-manager + external-dns (COMPLETE)
5. Workloads: Plex (NAS LXC) + download/*arr stack (COMPLETE)
6. GitOps controller (Flux) - PLANNED
7. Immich + Nextcloud decisions and deployment
8. Logging stack decision and rollout (TBD: Loki vs OpenSearch)
9. Hardening + backup validation drills

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
