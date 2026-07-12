# Post-Base Cluster Plan (SUPERSEDED — historical)

> **Status**: All 10 planned phases are COMPLETE, including Phase 10 (Flux
> GitOps). This doc is retained as a record of the original architectural
> planning; current operational state lives in:
>
> - `docs/19-k3s-deployment.md` — k3s cluster deployment
> - `docs/29-flux-operations.md` — Flux operations (day-2, rotation, rollback)
> - `docs/30-multi-repo-onboarding.md` — tenant onboarding
> - `docs/31-observability.md` — the observability stack (Prometheus + Grafana +
>   Loki + Alloy), **deployed since 2026-04-17** — the "Layer 3: Observability
>   (PLANNED)" / "Planned Applications" sections below are now DONE (only Uptime
>   Kuma remains)
> - `docs/16-next-steps.md` — ongoing/next work (observability, alerting, etc.)
>
> Do NOT follow any "deploy" commands below — they predate the Flux
> migration and will either no longer exist or do the wrong thing. Treat
> this page as read-only architectural history.

## Overview

The k3s platform is deployed on Proxmox-hosted VMs with a layered architecture: cluster bootstrap, ingress/certificates, identity/auth, and application workloads. Ansible provisions VMs and installs k3s; Flux reconciles all in-cluster state from `kubernetes/` in this repo.

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

### Planned Topology (9 Nodes: 3 Servers + 6 Agents)

The 9-node topology (3 etcd servers + 6 agents) was deployed as planned. The
node/IP/VMID tables that used to live here duplicated the live topology and
drifted; the **current** node roster lives in `docs/01-overview.md`
(architecture diagram) and `docs/19-k3s-deployment.md` — consult those, not
this page.

### HA Implementation (delivered as planned)

The HA plan covered both the k3s platform and critical base infrastructure,
and both parts shipped:

- **K3s HA**: 3-node etcd quorum (servers on the NAS, laptop, and Precision
  hosts) + 6 agents; kube-vip API VIP (.161) with leader election.
- **Proxmox HA**: automatic failover with ZFS replication for dns-01,
  dns-02, smtp-relay, and home-assistant (services that run outside k3s).

Current HA placement/replication state: `task proxmox:ha-status`; procedures
in `docs/12-runbooks.md`. See `docs/25-multi-node-expansion.md` for the
original expansion plan including IP/VMID allocation.

### Node Roles

Per-node roles (NAS / ingress / compute / general), labels, and sizing were
planned per host; the as-built roster is in `docs/01-overview.md` and
`docs/19-k3s-deployment.md`.

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

### Layer 3: Observability (DONE — deployed 2026-04-17, see docs/31)

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

## GitOps with Flux (COMPLETE - Phase 10)

Implemented. See [docs/29-flux-operations.md](29-flux-operations.md) for the Flux day-2 operations guide and [docs/30-multi-repo-onboarding.md](30-multi-repo-onboarding.md) for multi-repo tenant onboarding.

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
| GitLab | VM (not k8s) | git.esweiss.com | DEPLOYED | GitLab EE with registry, pages, CI runners |

### Planned Applications

| Application | Namespace | Domain | Priority | Notes |
|-------------|-----------|--------|----------|-------|
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

8. [x] **HA Expansion** (Two-Part Implementation):
   - **K3s HA**: 3 server nodes for etcd quorum, 6 agents (9-node cluster)
   - **Proxmox HA**: Automatic failover for critical infrastructure (dns-01, dns-02, smtp-relay, home-assistant)
9. [x] **GitLab**: Self-hosted Git + CI/CD + Container Registry (git.esweiss.com)
10. [x] **GitOps Controller** (Flux)
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

Current: External Secrets Operator with 1Password Connect provider (in-cluster); 1Password with `op run` for host-side tooling

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
