# Next Steps and TODO

This document tracks remaining work and planned improvements for the weisssrv homelab infrastructure.

## Completed Phases

### Phase 1: Base Infrastructure (COMPLETE)

- [x] Proxmox hosts configured (pve-nas-01, pve-opt-03)
- [x] ZFS storage pools configured (tank/ssd/nvme/archive)
- [x] DNS stack (AdGuard Home + Unbound with DoT)
- [x] SMTP relay via Gmail
- [x] Certificates (acme.sh with Cloudflare DNS-01)
- [x] Firewall rules (IPSets + Security Groups)
- [x] Tailscale VPN on all hosts

### Phase 2: K3s Platform (COMPLETE)

- [x] K3s cluster deployed (3 nodes: 1 server + 2 agents)
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
  - HAOS VM on pve-nas-01 (192.168.0.154)
  - Traefik ingress (internal + external domains)
  - Authentik SSO via hass-openid custom integration
  - API bypass routes for *arr integrations
  - NFS media mount for browsing

---

## Priority 1: High Availability

**Goal**: Implement comprehensive HA across both the k3s platform and critical base infrastructure services.

This is a **two-part HA implementation**:

1. **K3s HA**: Expand the cluster from 1 server to 3+ servers for etcd fault tolerance
2. **Proxmox HA**: Enable automatic failover for critical infrastructure VMs/containers

### Part 1: K3s Cluster HA (5-6 Node Target)

**Current State**: Single server node (k3s-srv-nas-01). Cluster functions but server failure = complete cluster outage.

**Target State**: 3 server nodes for etcd quorum (tolerates 1 server failure), with path to 5-6 servers (tolerates 2 failures).

#### Tasks

- [ ] **Bring up pve-prec-01** (192.168.0.107) - Dell Precision 3630
  - Follow `docs/00-hardware-setup.md` for Proxmox installation
  - Add to Proxmox cluster
  - Create k3s-srv-prec-01 (.227) + k3s-agt-prec-01 (.207)
  - This adds server #2 and a compute-class agent

- [ ] **Bring up pve-laptop-01** (192.168.0.103) - MSI GS60 2QD
  - Configure laptop for server use (lid closed = no action, power-on after AC restore)
  - Add to Proxmox cluster
  - Create k3s-srv-laptop-01 (.223) + k3s-agt-laptop-01 (.203)
  - This achieves 3-server HA quorum

- [ ] **Optional: Add pve-opt-01/pve-opt-02** for additional agent capacity

#### K3s HA Success Criteria

- `kubectl get nodes` shows 3+ server nodes with status Ready
- etcd cluster is healthy: `etcdctl endpoint health` shows 3 members
- kube-vip continues functioning when any single server is stopped

### Part 2: Proxmox HA for Critical Infrastructure

**Goal**: Enable Proxmox HA for critical base infrastructure services that run outside k3s.

**Critical Infrastructure VMs/Containers for HA**:

| Service | Type | Current Host | IP | Purpose |
|---------|------|--------------|-----|---------|
| dns-01 | LXC | pve-nas-01 | 192.168.0.150 | AdGuard Home primary DNS |
| dns-02 | LXC | pve-opt-03 | 192.168.0.160 | AdGuard Home secondary DNS |
| smtp-relay | LXC | pve-nas-01 | 192.168.0.151 | Mail relay via Gmail |
| home-assistant | VM | pve-nas-01 | 192.168.0.154 | Home Assistant OS |

**Why these services need Proxmox HA**:
- **DNS (dns-01, dns-02)**: Network-wide DNS resolution; already has redundancy via two instances, but HA ensures automatic recovery
- **SMTP (smtp-relay)**: Mail delivery for alerts, notifications, and cron job output; single point of failure currently
- **Home Assistant**: Smart home control; downtime impacts automations and device control

#### Tasks

- [ ] **Configure Proxmox HA** (requires 3+ Proxmox hosts)
  - Enable ZFS replication between hosts for shared storage
  - Add all Proxmox hosts to an HA cluster with quorum
  - Create HA groups:
    - `infra-critical`: dns-01, dns-02, smtp-relay
    - `apps-critical`: home-assistant
  - Configure migration settings (resource groups, priority)

- [ ] **Enable HA for critical infrastructure**
  - Add dns-01, dns-02, smtp-relay to HA management
  - Add home-assistant VM to HA management
  - Test failover procedures (graceful and forced)
  - Document recovery procedures

- [ ] **Storage replication for HA**
  - Configure ZFS send/receive between hosts
  - Set up replication schedules (e.g., every 15 minutes)
  - Validate data consistency after failover

#### Proxmox HA Success Criteria

- Proxmox HA cluster shows healthy quorum status
- HA resources show as "started" in the HA status view
- Simulated host failure results in automatic VM/container migration
- Services recover within acceptable timeframe (< 5 minutes)
- DNS resolution continues working during failover (via dns-02 redundancy)

### Combined HA Architecture

```
                    +------------------+
                    |   Proxmox HA     |
                    |   (3+ hosts)     |
                    +--------+---------+
                             |
         +-------------------+-------------------+
         |                                       |
+--------v---------+                   +---------v--------+
|   Base Infra     |                   |   K3s Platform   |
|   (Proxmox HA)   |                   |   (etcd quorum)  |
+------------------+                   +------------------+
| - dns-01 (LXC)   |                   | - 3+ server nodes|
| - dns-02 (LXC)   |                   | - kube-vip VIP   |
| - smtp-relay     |                   | - MetalLB LB     |
| - home-assistant |                   | - All k8s apps   |
+------------------+                   +------------------+
```

### Documentation

- `docs/25-multi-node-expansion.md` - Complete expansion guide with IP/VMID allocation

---

## Priority 2: Local GitLab Instance

**Goal**: Self-hosted GitLab for CI/CD pipelines, container registry, and code hosting.

**Why GitLab**:
- Unified platform (code + CI/CD + registry + packages)
- GitLab Runner can run pipelines on k3s cluster
- Integration with Flux for GitOps workflows
- Alternative to GitHub for private/sensitive projects
- Container registry for locally-built images

**Note**: Personal GitHub will continue to be used for public repositories and external CI/CD. GitLab is for internal/private projects and the container registry.

### Tasks

- [ ] **Research deployment options**
  - GitLab Helm chart vs. Omnibus package
  - Resource requirements (minimum 4GB RAM, 2 vCPU)
  - Storage sizing for registry and artifacts
  - Backup strategy

- [ ] **Deploy GitLab**
  - Helm chart to k3s cluster (preferred)
  - PostgreSQL on ZFS zvol (like Authentik/Mealie pattern)
  - Object storage for artifacts (MinIO or NFS-backed)
  - TLS via cert-manager

- [ ] **Configure GitLab Runner**
  - Kubernetes executor for CI/CD jobs
  - Resource limits to prevent cluster impact
  - Cache configuration for faster builds

- [ ] **Integrate with Authentik SSO**
  - SAML or OIDC provider in GitLab
  - User provisioning from Authentik

- [ ] **Set up container registry**
  - `registry.esweiss.com` domain
  - Storage backend (NFS or object storage)
  - Image retention policies

### Success Criteria

- GitLab accessible at `gitlab.esweiss.com`
- CI/CD pipelines run on k3s cluster
- Container registry stores built images
- SSO login works via Authentik

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
task maintenance:check-versions        # Checks 24 services for updates
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

- [ ] Add fail2ban to Proxmox hosts
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
