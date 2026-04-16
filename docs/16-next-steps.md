# Next Steps and TODO

This document tracks remaining work and planned improvements for the weisssrv homelab infrastructure.

## Completed Phases

### Phase 1: Base Infrastructure (COMPLETE)

- [x] Proxmox cluster configured (6 nodes: pve-nas-01, pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01)
- [x] ZFS storage pools configured (tank/ssd/nvme/archive on NAS; local-ssd on compute nodes)
- [x] DNS stack (AdGuard Home + Unbound with DoT)
- [x] SMTP relay via Gmail
- [x] Certificates (acme.sh with Cloudflare DNS-01)
- [x] Firewall rules (IPSets + Security Groups)
- [x] Tailscale VPN on Proxmox hosts only (remote access to cluster nodes)

### Phase 2: K3s Platform (COMPLETE)

- [x] K3s cluster deployed (9 nodes: 3 servers + 6 agents)
- [x] kube-vip for API HA (192.168.0.161)
- [x] MetalLB for LoadBalancer services (192.168.0.100-101)
- [x] Traefik ingress controller
- [x] cert-manager with Let's Encrypt (DNS-01)
- [x] external-dns for Cloudflare automation
- [x] Authentik SSO identity provider

### Phase 3: Applications (COMPLETE)

- [x] Plex Media Server (LXC on NAS with bind mounts)
- [x] Downloads stack deployed:
  - VPN-protected download clients (Gluetun + NZBGet + qBittorrent)
  - Media managers (Sonarr, Radarr, Lidarr, Prowlarr)
  - Plex Watchlist automation (Pulsarr)
  - All services with Authentik SSO protection
- [x] Recipe management stack deployed:
  - Mealie (food.esweiss.com) with PostgreSQL on ZFS zvol
  - Bar Assistant (bar.esweiss.com) with Meilisearch
  - Authentik SSO integration for both apps
  - OpenAI integration for Mealie recipe parsing
- [x] Home Assistant deployed:
  - HAOS VM on pve-prec-01 (192.168.0.154, HA-managed with multi-node replication)
  - Traefik ingress (internal + external domains)
  - Authentik SSO via hass-openid custom integration
  - API bypass routes for *arr integrations
  - NFS media mount for browsing

---

## Priority 1: High Availability (COMPLETE)

**Status**: Fully implemented across both K3s and Proxmox infrastructure.

### Part 1: K3s Cluster HA (COMPLETE)

**Achieved State**: 9-node cluster (3 servers + 6 agents) with full etcd quorum.

- [x] **pve-prec-01** (192.168.0.107) - Dell Precision 3630
  - k3s-srv-prec-01 (.227) + k3s-agt-prec-01 (.207) deployed
- [x] **pve-laptop-01** (192.168.0.103) - MSI GS60 2QD
  - k3s-srv-laptop-01 (.223) + k3s-agt-laptop-01 (.203) deployed
- [x] **pve-opt-01/pve-opt-02/pve-opt-03** - Additional agent capacity
  - k3s-agt-opt-01 (.204), k3s-agt-opt-02 (.205), k3s-agt-opt-03 (.206) deployed

**K3s HA Verified**:
- 3 server nodes with Ready status
- etcd quorum healthy (tolerates 1 server failure)
- kube-vip API VIP (.161) survives server failures

### Part 2: Proxmox HA for Critical Infrastructure (COMPLETE)

**Achieved State**: Full HA with ZFS replication and automatic failover.

| Service | Type | Primary Host | Failover Targets | Status |
|---------|------|--------------|------------------|--------|
| dns-01 | LXC | pve-laptop-01 | pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 | HA Active |
| dns-02 | LXC | pve-opt-03 | pve-laptop-01, pve-opt-01, pve-opt-02, pve-prec-01 | HA Active |
| smtp-relay | LXC | pve-opt-01 | pve-laptop-01, pve-opt-02, pve-opt-03, pve-prec-01 | HA Active |
| home-assistant | VM | pve-prec-01 | pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03 | HA Active |

- [x] **Proxmox HA configured** via `proxmox_ha` role
  - Node-affinity rules control placement
  - Resources managed by HA manager
- [x] **ZFS replication** configured (15-minute intervals)
  - All critical services replicate to 2+ target nodes
- [x] **Failover tested** and documented in `docs/12-runbooks.md`

**Management Commands**:
```bash
task proxmox:ha         # Configure HA rules, resources, replication
task proxmox:ha-status  # Show HA manager, rules, and replication status
```

See `docs/25-multi-node-expansion.md` and `docs/26-multi-node-implementation.md` for details.

---

## Priority 2: Local GitLab Instance (COMPLETE)

**Status**: Fully deployed as VM on pve-nas-01 with Traefik ingress.

### Deployment Summary

GitLab is deployed as a dedicated VM (not k3s) due to its resource requirements and complexity:
- **VM**: 6 vCPU, 16GB RAM, 100GB root disk on pve-nas-01
- **Repository storage**: 200GB ZFS zvol (`ssd/appdata/gitlab/repos`)
- **Access**: `git.esweiss.com` (internal) / `git.ericsweiss.com` (external)
- **Container Registry**: `registry.git.ericsweiss.com`
- **GitLab Pages**: `*.pages.git.ericsweiss.com`
- **Git SSH**: Port 22 (internal), Port 2222 (external via iptables NAT redirect)

### Completed Tasks

- [x] **GitLab EE deployed** (CE features, version managed in all.yml)
  - Omnibus package on Debian 13 VM
  - Repository data on separate ZFS zvol for persistence
  - Traefik IngressRoutes via k3s cluster
  - fail2ban protection for Git SSH on port 2222

- [x] **Authentik SSO integration**
  - SAML provider configured in GitLab
  - Users authenticate via Authentik
  - Auto-provisioning from Authentik directory

- [x] **Container Registry configured**
  - Accessible at `registry.git.ericsweiss.com`
  - TLS via Let's Encrypt (cert-manager)
  - Storage on local VM disk

- [x] **GitLab Pages enabled**
  - Wildcard domain: `*.pages.git.ericsweiss.com`
  - Direct access via `direct.ericsweiss.com` (non-proxied for wildcard TLS)

- [x] **CI/CD Runners on k3s**
  - GitLab Runner Helm chart deployed
  - Kubernetes executor for pipeline jobs
  - Resource limits configured

### Management Commands

```bash
task gitlab:deploy          # Deploy GitLab (VM + application)
task gitlab:deploy-ingress  # Deploy Traefik IngressRoutes
task gitlab:deploy-runner   # Deploy CI/CD runners on k3s
task gitlab:status          # Show GitLab and runner status
task gitlab:verify          # Run smoke tests
task gitlab:backup          # Create GitLab backup
task gitlab:console         # SSH to GitLab VM
task gitlab:logs            # View GitLab logs
task gitlab:reconfigure     # Reconfigure after changes
```

See `docs/27-gitlab-deployment.md` for complete deployment documentation.

---

## Priority 3: GitOps with Flux

**Goal**: Migrate from imperative Task/kubectl deployments to declarative GitOps.

**Why Flux over Helm tasks**:
- Git as single source of truth for cluster state
- Automatic drift detection and reconciliation
- Webhook-triggered deployments on git push
- Seamless integration with existing 1Password secrets workflow

**Prerequisites**: Local GitLab instance (Priority 2) should be deployed first to host the GitOps repository internally if desired, though Flux can also work with GitHub.

### Tasks

- [ ] **Bootstrap Flux CD**
  ```bash
  flux bootstrap github \
    --owner=ericsweiss \
    --repository=weisssrv \
    --branch=main \
    --path=kubernetes/flux \
    --personal
  ```

- [ ] **Migrate platform components to Flux**
  - Convert Helm releases: MetalLB, Traefik, cert-manager, external-dns
  - Create HelmRelease resources with values inline
  - Organize under `kubernetes/apps/`

- [ ] **Migrate application deployments**
  - Convert Authentik deployment to HelmRelease
  - Convert downloads stack (preserve VPN secrets handling)
  - Convert recipes stack

- [ ] **Configure Renovate Bot for automated updates**
  - See "Renovate Bot Integration" section below for strategy
  - Install Renovate GitHub App (or self-hosted on GitLab)
  - Configure for Kubernetes manifests, Helm charts, and container images
  - Set up auto-merge policies for minor/patch updates

### Success Criteria

- All k8s resources are defined in git under `kubernetes/`
- `flux get all` shows all resources reconciled
- Changes to git automatically deploy to cluster
- Renovate creates PRs for version updates

---

## Renovate Bot Integration

**Question**: How does Renovate Bot complement or replace the existing `task maintenance:check-versions` automation?

### Current Automation (Keep)

The existing version management tasks serve a critical role for **Ansible-managed infrastructure**:

```bash
task maintenance:check-versions        # Checks all managed services for updates
task maintenance:update-version        # Updates single version in all.yml
task maintenance:update-all-versions   # Updates all outdated versions
```

**What these tasks manage**:
- Base infrastructure versions (AdGuard Home, Tailscale, Plex, k3s)
- Helm chart versions for platform components
- Container image tags for k8s workloads
- All versions are centralized in `ansible/inventories/prod/group_vars/all.yml`

**Why keep them**:
1. **Ansible-deployed services** (AdGuard, Unbound, Plex, SMTP) are not visible to Renovate
2. **k3s binary version** requires coordinated rolling upgrades via Ansible
3. **Centralized version file** (`all.yml`) enables atomic updates and easy rollback
4. **Offline capability**: Works without external dependencies

### Renovate Bot (Add for Kubernetes)

Renovate excels at **Kubernetes manifest and Helm chart updates** within git:

**What Renovate would manage**:
- Container image tags in `kubernetes/apps/**/*.yaml` manifests
- HelmRelease version references (after Flux migration)
- Terraform provider versions in `terraform/**/*.tf`
- GitHub Actions versions in `.github/workflows/*.yml`

**How it complements existing automation**:

| Component | Current Automation | Renovate Bot |
|-----------|-------------------|--------------|
| AdGuard Home | `check-versions` | N/A (Ansible) |
| Tailscale | `check-versions` | N/A (Ansible) |
| Plex | `check-versions` | N/A (LXC/Ansible) |
| k3s binary | `check-versions` | N/A (Ansible) |
| Helm charts | `check-versions` | HelmRelease versions |
| Container images | `check-versions` | Image tags in manifests |
| Terraform providers | Manual | Terraform files |
| GitHub Actions | Manual | Workflow files |

### Recommended Integration Strategy

**Phase 1: Keep Both (Recommended)**

1. Continue using `task maintenance:check-versions` for all services
2. Add Renovate for Kubernetes manifests and Terraform
3. Renovate PRs update the manifest files; `check-versions` shows the same updates in all.yml
4. Choose which workflow to apply updates from (either is valid)

**Phase 2: After Flux Migration**

Once Flux is managing Kubernetes deployments:
1. Renovate becomes primary for k8s workloads (creates PRs against HelmReleases)
2. `check-versions` continues for Ansible-managed infrastructure
3. Update `check-versions` script to skip Flux-managed components (avoid duplicate tracking)

**Phase 3: Full Integration (Optional)**

If desired, enhance `check-versions.py` to:
- Read versions from Kubernetes manifests (in addition to all.yml)
- Understand which components are Renovate-managed
- Provide unified dashboard across all version sources

### Renovate Configuration (Post-Flux)

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["config:recommended"],
  "kubernetes": {
    "fileMatch": ["kubernetes/.+\\.ya?ml$"]
  },
  "flux": {
    "fileMatch": ["kubernetes/.+\\.ya?ml$"]
  },
  "helm-values": {
    "fileMatch": ["kubernetes/.+\\.ya?ml$"]
  },
  "terraform": {
    "fileMatch": ["terraform/.+\\.tf$"]
  },
  "packageRules": [
    {
      "description": "Auto-merge patch updates for stable apps",
      "matchUpdateTypes": ["patch"],
      "matchPackagePatterns": ["linuxserver/*", "ghcr.io/onedr0p/*"],
      "automerge": true
    },
    {
      "description": "Group all download client updates",
      "matchPackagePatterns": ["sonarr", "radarr", "lidarr", "prowlarr"],
      "groupName": "arr-stack"
    },
    {
      "description": "Require manual review for major updates",
      "matchUpdateTypes": ["major"],
      "automerge": false,
      "labels": ["breaking-change"]
    }
  ]
}
```

### Summary

**Keep `task maintenance:check-versions`** for:
- Ansible-managed infrastructure (always)
- Centralized version visibility across all 24 services
- Offline/air-gapped capability
- Pre-Flux k8s workload version tracking

**Add Renovate Bot** for:
- Automated PR creation for Kubernetes manifests (post-Flux)
- Terraform provider updates
- GitHub Actions updates
- Dependency grouping and auto-merge policies

---

## Priority 5: Observability Stack

**Goal**: Comprehensive monitoring, alerting, and log aggregation.

### Metrics: Prometheus + Grafana

- [ ] **Deploy kube-prometheus-stack** (Helm chart)
  - Prometheus for metrics collection
  - Grafana for dashboards
  - Alertmanager for notifications
  - Node exporters on all hosts
  - ServiceMonitors for k8s components

- [ ] **Configure alerting**
  - Discord/Slack webhook for alerts
  - Email via smtp-relay
  - Alert rules for:
    - Node down
    - Pod crash loops
    - High resource usage
    - Certificate expiration
    - etcd health

- [ ] **Create dashboards**
  - Cluster overview
  - Per-namespace resource usage
  - Application-specific (Traefik, Authentik, *arr apps)
  - ZFS pool health (custom exporter needed)

### Logging: Options Analysis

| Solution | Pros | Cons | Resource Requirements |
|----------|------|------|----------------------|
| **Loki + Promtail** | Native Grafana integration, label-based queries, lightweight | No full-text search, limited features | Low (1-2GB RAM) |
| **Vector + Loki** | High-performance agent, flexible routing | Additional component to manage | Low-Medium |
| **OpenSearch** | Full-text search, Kibana-like UI, feature-rich | High resource usage, complex | High (4GB+ RAM) |
| **Elasticsearch** | Industry standard, excellent search | Very high resources, licensing concerns | Very High (8GB+ RAM) |

**Recommendation**: Start with **Loki + Promtail** for low overhead, evaluate OpenSearch later if advanced log search is needed.

- [ ] **Deploy Loki** (via Helm)
  - Single-binary mode initially (simple)
  - Storage on NFS-backed PV
  - Retention: 30 days

- [ ] **Deploy Promtail** (or Vector as alternative)
  - DaemonSet on all nodes
  - Scrape container logs
  - Add labels for namespace, pod, container

- [ ] **Integrate with Grafana**
  - Add Loki as data source
  - Create log exploration dashboard
  - Correlate logs with metrics

### Uptime Monitoring

- [ ] **Deploy Uptime Kuma** (simple, self-hosted)
  - Monitor external endpoints
  - Monitor internal services via internal VIP
  - Status page at `status.esweiss.com`

### Success Criteria

- Grafana accessible at `grafana.esweiss.com`
- All nodes and pods have metrics visible
- Alerts fire correctly (test with intentional failure)
- Logs searchable from Grafana
- Status page shows all service health

---

## Priority 6: Additional Applications

### Immich (Photo Management)

**Open Questions** (decide before deployment):
- Exposure: Internal-only or also external?
- Storage: `tank/photos` dataset or under `ssd/appdata`?
- Performance: NFS-backed DB acceptable? (likely needs local SSD for PostgreSQL)
- ML acceleration: GPU passthrough for face recognition?

- [ ] Finalize deployment decisions
- [ ] Create ZFS dataset for photos
- [ ] Deploy Immich Helm chart
- [ ] Configure Authentik SSO
- [ ] Mobile app testing

### Nextcloud (File Sync)

**Open Questions**:
- Primary use: File sync only, or also Collabora/OnlyOffice?
- Storage: Dedicated dataset or reuse `tank/share`?
- Auth: Authentik OIDC from day 1

- [ ] Finalize deployment decisions
- [ ] Deploy Nextcloud Helm chart (or AIO container)
- [ ] Configure Authentik SSO
- [ ] Desktop/mobile client testing

---

## Infrastructure Improvements

### Security Hardening

- [x] Add fail2ban to Proxmox hosts (deployed)
- [ ] Network segmentation with VLANs (IoT, guest, management)
- [ ] Implement Network Policies in k3s (default-deny ingress)
- [ ] External Secrets Operator with 1Password Connect backend

### Storage Enhancements

- [ ] ZFS auto-scrub notifications (systemd timer + email)
- [ ] Backup verification testing (quarterly restore drills)
- [ ] Consider ZFS special devices for metadata acceleration

### Documentation

- [ ] Network topology diagrams (draw.io or Mermaid)
- [ ] Disaster recovery runbook updates
- [ ] Troubleshooting flowcharts
- [ ] Document ZFS scrub schedule details (see docs/06-zfs.md)

---

## Commands Reference

```bash
# Base infrastructure
task deploy:all           # Deploy base infrastructure
task deploy:check         # Dry-run
task deploy:verify        # Post-deployment verification

# K3s cluster
task k3s:deploy           # Deploy k3s cluster
task k3s:deploy-workloads # Deploy all platform workloads
task k3s:status           # Show cluster status

# Downloads stack
task downloads:deploy     # Deploy downloads stack
task downloads:status     # Show stack status
task downloads:vpn-status # Check VPN connection
task downloads:vpn        # Enable/disable VPN per-app (APP=nzbget ENABLED=true)
task downloads:restart    # Restart all download/media apps
task downloads:logs       # View app logs (APP=nzbget [CONTAINER=gluetun])
task downloads:shell      # Shell into container (APP=nzbget [CONTAINER=gluetun])
task downloads:delete     # Remove stack (preserves data)

# Recipes stack
task recipes:deploy       # Deploy Mealie and Bar Assistant
task recipes:status       # Show recipes status

# Home Assistant
task home-assistant:deploy       # Deploy HA ingress + config
task home-assistant:status       # Show VM and ingress status
task home-assistant:snapshot     # Create Proxmox snapshot

# Plex
task deploy:plex          # Deploy Plex LXC

# GitLab
task gitlab:deploy          # Deploy GitLab (VM + application)
task gitlab:deploy-check    # Dry-run deployment
task gitlab:deploy-ingress  # Deploy Traefik IngressRoutes
task gitlab:deploy-runner   # Deploy CI/CD runners on k3s
task gitlab:status          # Show GitLab and runner status
task gitlab:verify          # Run smoke tests
task gitlab:backup          # Create GitLab backup
task gitlab:console         # SSH to GitLab VM
task gitlab:logs            # View GitLab logs
task gitlab:reconfigure     # Reconfigure after changes

# Maintenance
task maintenance:check-versions   # Check for updates
task maintenance:update-full      # Full system update
task collect-state                # Generate cluster snapshot
```

---

## Validation Checklist

After deployment, verify:

### Base Infrastructure
- [x] SSH access works to all hosts
- [x] DNS resolution works (internal and external)
- [x] NFS mounts are accessible
- [x] Samba shares are accessible
- [x] Mail delivery works
- [x] TLS certificates are valid
- [x] Proxmox web UI is accessible
- [x] AdGuard Home web UI is accessible
- [x] ZFS pools are healthy

### K3s Platform
- [x] K3s cluster is healthy
- [x] All pods running
- [x] IngressRoutes accessible (internal and external)
- [x] Authentik SSO working

### Applications
- [x] VPN connected for download clients
- [x] Mealie accessible at food.esweiss.com
- [x] Bar Assistant accessible at bar.esweiss.com
- [x] Plex accessible at plex.esweiss.com
- [x] Home Assistant accessible at home.esweiss.com

---

## Related Documentation

- `docs/14-post-base-plan.md` - K3s platform architecture and roadmap
- `docs/19-k3s-deployment.md` - K3s cluster deployment workflow
- `docs/25-multi-node-expansion.md` - Multi-node HA expansion guide
- `docs/12-runbooks.md` - Operational procedures
- `docs/17-disaster-recovery.md` - Disaster recovery procedures
