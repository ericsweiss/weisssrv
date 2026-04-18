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
task gitlab:deploy          # Deploy GitLab (VM + application via Ansible)
# IngressRoutes + runners + agent are Flux-managed (kubernetes/apps/gitlab-*
# and kubernetes/apps/vm-ingress/gitlab*.yaml) — edit and git push.
task gitlab:status          # Show GitLab and runner status
task gitlab:verify          # Run smoke tests
task gitlab:backup          # Create GitLab backup
task gitlab:console         # SSH to GitLab VM
task gitlab:logs            # View GitLab logs
task gitlab:reconfigure     # Reconfigure after changes
```

See `docs/27-gitlab-deployment.md` for complete deployment documentation.

---

## Priority 3: GitOps with Flux (COMPLETE)

**Status**: Fully migrated. Flux reconciles every Kubernetes workload from this repo;
External Secrets Operator (1Password SDK provider) supplies all k8s Secrets.

### Architecture

- **Flux controllers** bootstrapped via `flux bootstrap gitlab` into
  `kubernetes/clusters/weisssrv/flux-system/`.
- **Platform** (`kubernetes/infrastructure/`): sources (HelmRepositories), controllers
  (MetalLB, Traefik, cert-manager, external-dns, external-secrets), and configs
  (ClusterSecretStore, ClusterIssuer, CoreDNS HelmChartConfig, DDNS CronJob,
  versions-configmap).
- **Apps** (`kubernetes/apps/`): `authentik/`, `download-clients/`, `recipes/`,
  `gitlab-runner/`, `gitlab-runner-privileged/`, `gitlab-agent/`, `vm-ingress/`
  (IngressRoutes for non-k8s services: Plex, Home Assistant, GitLab VM, AdGuard,
  router, Traefik dashboard).
- **Version flow**: `all.yml` → `task flux:sync-versions` → `cluster-versions`
  ConfigMap → Flux `postBuild.substituteFrom` substitutes `${...}` placeholders.
- **Secrets**: one bootstrap `onepassword-sdk-token` Secret in `external-secrets`
  namespace; all other Secrets created by ExternalSecrets referencing 1Password
  item IDs in the `Homelab` vault.

### Deploy workflow

Any Kubernetes change is a git commit under `kubernetes/`. On push, Flux polls
and reconciles within ~1 minute (a planned webhook will reduce this to seconds).
Helm releases upgrade, Kustomizations re-apply, ExternalSecrets refresh, no manual
kubectl/helm invocations.

### Reference

- [docs/29-flux-operations.md](./29-flux-operations.md) — operator guide (bootstrap,
  adopt Helm releases, rotate secrets, add an app, troubleshoot, emergency stop)
- [docs/30-multi-repo-onboarding.md](./30-multi-repo-onboarding.md) — onboarding
  external repos that deploy into this cluster via Flux
- Taskfile: `task flux:*` — status, verify, reconcile, suspend, resume,
  refresh-secret, rotate-secret, sync-versions, dev-apply, lint

---

## Renovate Bot Integration

**Status**: Not yet implemented. `all.yml` remains the single source of truth for
versions; the `cluster-versions` ConfigMap flows into Flux via substitutions.

### Current Automation (Keep)

The existing version management tasks serve a critical role for **all managed versions**,
both Ansible-deployed and Flux-deployed:

```bash
task maintenance:check-versions        # Checks all managed services for updates
task maintenance:update-version        # Updates single version in all.yml
task maintenance:update-all-versions   # Updates all outdated versions
task flux:sync-versions                # Regenerates kubernetes/infrastructure/sources/versions-configmap.yaml
```

**What these tasks manage**:
- Base infrastructure versions (AdGuard Home, Tailscale, Plex, k3s)
- Helm chart versions for platform components (consumed by Flux HelmReleases via substitutions)
- Container image tags for k8s workloads (consumed by Flux Kustomizations via substitutions)
- All versions centralized in `ansible/inventories/prod/group_vars/all.yml`

**Why keep them**:
1. **Ansible-deployed services** (AdGuard, Unbound, Plex, SMTP) are not visible to Renovate
2. **k3s binary version** requires coordinated rolling upgrades via Ansible
3. **Centralized version file** (`all.yml`) enables atomic updates and easy rollback
4. **Offline capability**: Works without external dependencies
5. **Flux substitution**: workload manifests reference `${var}` placeholders, so a single
   bump in `all.yml` + `task flux:sync-versions` + commit updates everything atomically.

### Renovate Bot (Future Addition)

Renovate could supplement `check-versions` by creating MRs automatically instead of
requiring a manual `task maintenance:check-versions` run. If added, it must:
- Edit `all.yml` (not individual manifests) to keep a single source of truth
- Run `scripts/generate-versions-configmap.py` as part of each MR so the ConfigMap
  stays in sync (already enforced by the `flux-versions-sync` CI job)

Decision: defer until operational overhead justifies it. `task maintenance:check-versions`
runs on a weekly schedule and surfaces updates without noise.

---

## Priority 5: Observability Stack (DONE -- 2026-04-17)

**Status**: Fully deployed. See [docs/31-observability.md](./31-observability.md) for the complete guide.

### Deployed Components

- [x] **kube-prometheus-stack** -- Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics
- [x] **Loki** -- Log aggregation (single-binary mode, 30-day retention, 75GB ZFS zvol)
- [x] **Alloy** -- DaemonSet log collector on all nodes (successor to Promtail)
- [x] **Exporters** -- Proxmox (all 6 hosts), ZFS, AdGuard, Unbound, Blackbox, Plex, Exportarr (NZBGet + qBittorrent active; Sonarr/Radarr/Lidarr/Prowlarr staged at replicas:0 pending API keys)
- [x] **Service Monitors** -- Flux controllers (GitLab VM and Home Assistant VM manifests ready, activate after enabling metrics on each)
- [x] **Alerting** -- Discord webhook + email via smtp-relay, custom alert rules for storage, infrastructure, and backups
- [x] **Grafana** -- `grafana.esweiss.com` with Authentik OIDC, Loki datasource, dashboard sidecar
- [x] **Persistent storage** -- Prometheus 150GB zvol, Loki 75GB zvol (both on NAS SSD pool)

### Remaining (Future)

- [ ] **Uptime Kuma** -- External endpoint monitoring and status page at `status.esweiss.com`

---

## Future: weisssrv-project-template GitLab template project

Create a dedicated GitLab project template (separate repo) that new repos
deploying to this cluster fork/copy. Pre-wired with:

- `.gitlab-ci.yml` with standard stages (lint, validate, security,
  AI review conditional on OP/openai secrets, Flux webhook trigger (planned))
- `.gitleaks.toml`, `.editorconfig`, `.pre-commit-config.yaml`, `renovate.json`
- `Taskfile.yml` with flux:* and maintenance:* wrappers
- `kubernetes/flux/` stub: namespace, Kustomization, ExternalSecret,
  release.yaml/resource.yaml templates
- `.cursorrules`, `CLAUDE.md`, `AGENTS.md` templates
- `CODEOWNERS`, issue/MR templates, LICENSE, README scaffold

Tracked as a future separate project (see docs/30-multi-repo-onboarding.md for the tenant onboarding pattern it would use).

Onboarding flow: fork template → add CI vars → add wiring YAML under
`kubernetes/clusters/weisssrv/tenants/` in this repo.

---

## Outstanding Follow-Ups

### Complete k3s secrets-encryption

The k3s Ansible role config template has `secrets-encryption: true`, but the
currently-deployed `config.yaml` on the server nodes does not. Completing this
requires a k3s role re-run that regenerates the config plus staggered server
restarts (`systemctl restart k3s` on one server at a time, verifying etcd
quorum between each). Once done, run `sudo k3s secrets-encrypt status` on
k3s-srv-nas-01 and, if needed, `sudo k3s secrets-encrypt reencrypt` to
re-encrypt existing Secrets with the active key.

### AQC113 firmware update (pve-nas-01)

Still outstanding: update the AQC113 NIC firmware from `1.5.38` to `1.5.45` on
pve-nas-01. Requires the Windows flashing tool on a USB stick and a downtime
window. The GRO disable (interim stability fix) is now codified via the
`nic_tuning` Ansible role (`/etc/network/interfaces.d/99-nic-nic1-tuning.cfg` +
`/etc/sysctl.d/99-nic-tuning-ip-forward.conf` — see `ansible/roles/nic_tuning/README.md`
for exact filenames), so this is no longer an emergency — it
remains a planned maintenance task.

### pve-nas-01 stale manual config cleanup

Earlier manual entries still exist in `/etc/network/interfaces` and
`/etc/sysctl.conf` on pve-nas-01 (GRO off, `net.ipv4.ip_forward=1`). The
`nic_tuning` role now manages these via drop-ins, so the manual entries are
redundant (harmless but stale). Remove them in a scheduled cleanup pass and
verify the role's drop-ins are still authoritative after reboot.

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
- [x] External Secrets Operator with 1Password SDK provider (deployed -- see docs/29-flux-operations.md)

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
# Base infrastructure (Ansible)
task infra:deploy         # Deploy base infrastructure
task infra:check          # Dry-run
task infra:verify         # Post-deployment verification

# K3s cluster (Ansible — cluster infrastructure only)
task k3s:provision-vms    # Provision k3s VMs on Proxmox
task k3s:deploy           # Deploy/upgrade k3s (idempotent)
task k3s:kubeconfig       # Fetch kubeconfig
task k3s:backup           # Create etcd snapshot
task k3s:status           # Show cluster status

# Flux GitOps (all k8s workload deploys happen via git push)
task flux:status          # Concise health summary
task flux:verify          # flux check + get all -A
task flux:reconcile       # Force full reconciliation
task flux:suspend -- <ns>/<kind>/<name>   # Emergency pause
task flux:resume -- <ns>/<kind>/<name>    # Resume
task flux:refresh-secret -- <ns>/<name>   # Force ExternalSecret sync
task flux:rotate-secret -- <app>          # Refresh secret + restart consumers
task flux:sync-versions   # Regenerate versions-configmap.yaml from all.yml
task flux:dev-apply -- <path>   # Local kubectl apply (Flux reverts on next cycle)
task flux:lint            # kustomize build + envsubst + kubeconform on every Kustomization

# Operational (workload introspection)
task downloads:status     # Show downloads namespace status
task downloads:vpn-status # Check VPN connection
task downloads:restart    # Restart all download/media apps
task downloads:logs       # View app logs (APP=nzbget [CONTAINER=gluetun])
task downloads:shell      # Shell into container
task downloads:delete     # Remove stack (preserves data; Flux will recreate)
task recipes:status       # Show recipes namespace status
task recipes:restart      # Restart all recipe apps
task recipes:logs         # View app logs (APP=mealie)
task recipes:shell        # Shell into app container
task recipes:delete       # Remove stack (preserves data; Flux will recreate)
task authentik:status     # Show Authentik pods/status
task authentik:logs       # View Authentik logs
task authentik:restart    # Restart Authentik pods

# Home Assistant (HAOS VM, configuration via Ansible)
task home-assistant:deploy-config  # Deploy HA configuration via Ansible
task home-assistant:status         # Show VM status
task home-assistant:snapshot       # Create Proxmox snapshot

# Plex (LXC on NAS, managed by Ansible)
task plex:deploy          # Deploy Plex LXC

# GitLab (VM on NAS, managed by Ansible; runners + agent managed by Flux)
task gitlab:deploy          # Deploy GitLab (VM + application)
task gitlab:deploy-check    # Dry-run deployment
task gitlab:status          # Show GitLab and runner status
task gitlab:verify          # Run smoke tests (HTTP readiness, registry, pages, SSH)
task gitlab:backup          # Create GitLab backup
task gitlab:console         # SSH to GitLab VM
task gitlab:logs            # View GitLab logs
task gitlab:reconfigure     # Reconfigure after changes

# Maintenance
task maintenance:check-versions   # Check for updates
task maintenance:update-full      # Full base infrastructure update
task maintenance:update-k3s-nodes # Rolling k3s node upgrades
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
