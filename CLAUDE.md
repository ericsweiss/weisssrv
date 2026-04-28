# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weisssrv** - Homelab Infrastructure as Code

Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and Kubernetes.

## Repository Structure

**Canonical source**: https://git.ericsweiss.com/eric/weisssrv (GitLab)
**GitHub mirror**: https://github.com/ericsweiss/weisssrv (read-only)

```
weisssrv/
├── ansible/                    # Configuration management
│   ├── inventories/prod/       # Production inventory + vars
│   ├── roles/                  # 25 roles for all services
│   └── playbooks/              # Deployment playbooks
├── terraform/cloudflare/       # External DNS management
├── kubernetes/                 # Flux-managed k8s state (GitOps source of truth)
│   ├── clusters/weisssrv/      # Flux entrypoint (flux-system, infrastructure-{sources,controllers,configs,observability}.yaml, apps.yaml, tenants/)
│   ├── infrastructure/         # Platform — reconciled in four stages via dependsOn ordering
│   │   ├── sources/            # HelmRepository CRs + versions-configmap.yaml (runs first, no deps)
│   │   ├── controllers/        # external-secrets, onepassword-connect, metallb, cert-manager, traefik, external-dns (HelmReleases; dependsOn sources)
│   │   ├── configs/            # cluster-secret-store, cluster-issuer, metallb-ip-pools, wildcard-certificates, coredns/, cloudflare-ddns/, shared-cloudflare-secrets/ (CRs that require the controllers' CRDs; dependsOn controllers)
│   │   └── observability/      # kube-prometheus-stack, loki, alloy, exporters, service-monitors, dashboards, ingress (dependsOn configs)
│   └── apps/                   # authentik, download-clients, recipes, gitlab-runner, gitlab-runner-privileged, gitlab-agent, vm-ingress (each with release.yaml + externalsecret.yaml; dependsOn infrastructure-observability)
├── docs/                       # Documentation
├── scripts/                    # Utility scripts
├── .gitlab-ci.yml              # CI/CD pipeline (canonical)
└── .github/workflows/          # Legacy workflows (disabled)
```

## Architecture

### Current Infrastructure (Base Parity)

- **6 Proxmox Hosts** (cluster name: weisssrv):
  - pve-nas-01 (192.168.0.102) - NAS + storage
  - pve-laptop-01 (192.168.0.103) - Compute
  - pve-opt-01 (192.168.0.104) - Compute
  - pve-opt-02 (192.168.0.105) - Compute
  - pve-opt-03 (192.168.0.106) - Compute
  - pve-prec-01 (192.168.0.107) - Compute
- **NAS Storage**: ZFS (tank/ssd/nvme/archive pools), NFS, Samba
- **DNS**: 2x AdGuard Home + Unbound (DoT) - 192.168.0.150/160
- **SMTP**: Relay via Gmail - 192.168.0.151
- **Certs**: acme.sh with Cloudflare DNS-01
- **VPN**: Tailscale on Proxmox hosts (remote access)
- **Firewall**: Proxmox firewall with IP Sets + Security Groups
- **HA**: Proxmox HA for infrastructure services (DNS, SMTP, Home Assistant)
- **GitOps**: Flux CD reconciles all Kubernetes state from `kubernetes/` on every push
- **Secrets to k8s**: External Secrets Operator (ESO) with 1Password Connect provider (`ClusterSecretStore` `onepassword-homelab`)

### K3s Platform

**9-node cluster** (3 servers + 6 agents):

**Server Nodes** (etcd quorum):
- **k3s-srv-nas-01** (192.168.0.222) - Server on pve-nas-01
- **k3s-srv-laptop-01** (192.168.0.223) - Server on pve-laptop-01
- **k3s-srv-prec-01** (192.168.0.227) - Server on pve-prec-01

**Agent Nodes**:
- **k3s-agt-nas-01** (.202) - NAS workloads (esweiss.com/nas)
- **k3s-agt-laptop-01** (.203) - Ingress + general
- **k3s-agt-opt-01** (.204) - Ingress + general
- **k3s-agt-opt-02** (.205) - Ingress + general
- **k3s-agt-opt-03** (.206) - Ingress + general
- **k3s-agt-prec-01** (.207) - General + compute (esweiss.com/compute)

**Deployment Model** (Two-phase approach):
1. **Ansible** (`task k3s:deploy`): VMs, k3s, kube-vip (API VIP .161). One-off and idempotent.
2. **Flux GitOps**: Everything in-cluster (platform controllers + apps) is reconciled by Flux from `kubernetes/` on every push to `main`. Local iteration uses `task flux:dev-apply -- <path>` (changes are reverted on next reconcile cycle unless committed).

Ansible tasks remain idempotent - safe to re-run. Flux reconciles automatically within ~1 minute of a push; use `task flux:reconcile` to force immediately. See `docs/19-k3s-deployment.md` for the k3s layer and `docs/29-flux-operations.md` for Flux day-2 operations.

**Features**:
- kube-vip (API VIP .161), MetalLB (VIPs .100/.101)
- Traefik ingress, external-dns (Cloudflare)
- 3-node etcd quorum (tolerates 1 server failure)
- Flux CD (source-controller, kustomize-controller, helm-controller, notification-controller) + External Secrets Operator with 1Password Connect backend
- Observability stack: Prometheus + Grafana + Loki + Alloy (metrics, logs, dashboards, alerting); k3s-irrelevant components disabled (kubeProxy, kubeScheduler, kubeControllerManager, kubeEtcd); alertmanager config uses ExternalSecret template for webhook injection

**Applications**:
- Authentik SSO (auth.esweiss.com) - Identity provider for SSO/OIDC/SAML
  - Version managed in all.yml
  - PostgreSQL data on persistent ZFS zvol (ssd/appdata/authentik/postgres, 10GB)
- Plex Media Server (plex.esweiss.com) - LXC container with Traefik ingress
- Download clients + media stack (downloads namespace):
  - Gluetun (VPN gateway with killswitch)
  - NZBGet (nzbget.esweiss.com) - Usenet
  - qBittorrent (qbittorrent.esweiss.com) - BitTorrent
  - Prowlarr (prowlarr.esweiss.com) - Indexer manager
  - Sonarr (tv.esweiss.com) - TV shows
  - Radarr (movies.esweiss.com) - Movies
  - Lidarr (music.esweiss.com) - Music
  - Pulsarr (pulsarr.esweiss.com) - Plex Watchlist automation (pinned to NAS nodes, requires AVX)
- Recipe management stack (recipes namespace):
  - Mealie (food.esweiss.com) - Recipe management and meal planning
  - Bar Assistant (bar.esweiss.com) - Cocktail/bar recipe management
  - Authentik SSO integration for both apps
  - OpenAI integration for Mealie recipe parsing
  - Mealie PostgreSQL on persistent ZFS zvol (ssd/appdata/mealie/postgres, 32GB)
- Home Assistant (home.esweiss.com / home.ericsweiss.com):
  - HAOS VM on pve-prec-01 (192.168.0.154, HA-managed with multi-node replication)
  - Traefik ingress with WebSocket support
  - Authentik SSO via hass-openid custom integration (OIDC)
  - API bypass routes for HA integrations (sonarr, radarr, lidarr, nzbget, qbittorrent)
  - NFS media mount (read-only access to unified media library)
- GitLab (git.esweiss.com / git.ericsweiss.com):
  - GitLab EE (CE features) on pve-nas-01 (192.168.0.153) — version in all.yml
  - VM with 6 vCPUs, 16GB RAM, 100GB root disk
  - Repository data on separate ZFS zvol (ssd/appdata/gitlab/repos, 200GB)
  - Container Registry (registry.git.ericsweiss.com)
  - GitLab Pages (*.pages.git.ericsweiss.com)
  - Web IDE extension host (*.ide.git.ericsweiss.com) — per-extension SOP isolation, CVE-2026-5816 mitigation
  - CI/CD Runners on k3s cluster (infrastructure runner for weisssrv, shared runner for other projects)
  - Authentik SSO integration
  - Git SSH on port 22 (internal), port 2222 (external)

- Grafana (grafana.esweiss.com):
  - Community dashboards imported for Node Exporter, Traefik, AdGuard, Redis, Prometheus, Alertmanager
  - Custom dashboards for Cluster Overview, Home Assistant, Media Stack, Recipes, DNS Combined, Mail, Infrastructure, Thermals, Blackbox Exporter, cert-manager, Flux Cluster, Unbound, GitLab
  - Authentik OIDC SSO integration
  - Loki datasource for log queries
  - Dashboard sidecar auto-discovers ConfigMaps with `grafana_dashboard` label

**Planned**:
- Apps: Immich, Nextcloud
- `weisssrv-project-template` GitLab template project for tenant-side scaffold (tracked in `docs/16-next-steps.md`)

## Common Development Commands

### Task Runner

All operations use Taskfile.yml:

```bash
# List all tasks
task --list

# Ansible operations
task ansible:install-collections  # Install required collections
task ansible:ping                 # Test connectivity
task ansible:lint                 # Lint playbooks
task ansible:test                 # Run all Molecule unit tests
task ansible:test-integration     # Run all integration tests (multi-role)
task ansible:test-integration-dns # DNS stack (unbound + adguard_home + adguard_sync)
task ansible:test-integration-mail # Mail stack (smtp_relay + postfix_null_client)
task ansible:test-integration-base # Base stack (base + qol + tailscale)
task ansible:test-integration-storage # Storage stack (nas_storage + Samba client, NFS server-side only)
task ansible:test-integration-certs # Certificate distribution (acme_certs multi-host)

# Deployments (base infrastructure)
task infra:check                  # Dry-run (--check mode)
task infra:deploy                 # Full base infrastructure deployment (excludes k3s)
task infra:verify                 # Post-deployment verification
task infra:base                   # Base packages + SSH only
task dns:deploy                   # DNS stack
task storage:deploy               # NAS services
task plex:deploy                  # Plex Media Server (LXC + Plex install)
task plex:check                   # Plex dry-run

# Proxmox HA (multi-node high availability)
task proxmox:ha                   # Configure HA rules, resources, and replication
task proxmox:ha-check             # Dry-run HA configuration
task proxmox:ha-status            # Show HA manager, rules, and replication status

# K3s cluster (Ansible - separate lifecycle)
task k3s:provision-vms            # Provision k3s VMs on Proxmox
task k3s:deploy                   # Deploy k3s cluster (idempotent, safe to re-run)
task k3s:kubeconfig               # Fetch kubeconfig from cluster
task k3s:backup                   # Create etcd snapshot
task k3s:status                   # Show cluster and workload status

# Flux GitOps (all Kubernetes workloads - edit YAML in kubernetes/ + git push)
task flux:install-cli             # Install flux CLI (brew, macOS)
task flux:bootstrap               # One-time: bootstrap Flux into the cluster
task flux:bootstrap-onepassword   # One-time: create 1P Connect bootstrap secrets in external-secrets ns
task flux:reconcile               # Force immediate reconciliation of all Flux-managed resources
task flux:verify                  # Run `flux check` + show status of all managed resources
task flux:status                  # Concise health summary
task flux:suspend -- <ns>/<kind>/<name>  # Suspend a Flux resource
task flux:resume  -- <ns>/<kind>/<name>  # Resume a suspended Flux resource
task flux:refresh-secret -- <ns>/<name>  # Force an ExternalSecret to re-fetch from 1Password
task flux:rotate-secret  -- <app>        # Refresh ExternalSecret + restart consuming pods
task flux:sync-versions           # Regenerate versions-configmap.yaml from all.yml
task flux:dev-apply -- <path>     # Local iteration: kustomize build + envsubst + kubectl apply (Flux reverts on next reconcile)
task flux:lint                    # Build + envsubst + kubeconform on every Flux Kustomization path (fails on unknown ${var})
task flux:webhook-register        # One-time: register GitLab push webhook to Flux Receiver (requires Receiver manifest — planned follow-up)

# Download clients and media stack (workload operations only; deployment via Flux)
task downloads:status             # Show downloads namespace status
task downloads:vpn-status         # Check VPN connection and public IP
task downloads:restart            # Restart all download/media apps
task downloads:logs               # View app logs
task downloads:shell              # Shell into app container
task downloads:delete             # Remove stack (preserves data)

# Recipe management stack (workload operations only; deployment via Flux)
task recipes:status               # Show recipes namespace status
task recipes:restart              # Restart all recipe apps
task recipes:logs                 # View app logs (APP=mealie)
task recipes:shell                # Shell into app container (APP=mealie)
task recipes:delete               # Remove stack (preserves data)

# Authentik (workload operations only; deployment via Flux)
task authentik:status             # Show Authentik status
task authentik:logs               # View Authentik logs
task authentik:restart            # Restart Authentik pods

# Observability stack (workload operations only; deployment via Flux)
task observability:status         # Show observability namespace health (pods, services, PVCs, HelmReleases, ExternalSecrets, ServiceMonitors)
task observability:logs           # View component logs (COMPONENT=prometheus|loki|alloy|grafana|alertmanager)
task observability:restart        # Restart all observability workloads
task observability:silence        # Create Alertmanager silence (ALERT=alertname, DURATION=2H — BSD date units)

# Home Assistant (VM on Proxmox; IngressRoute is Flux-managed under apps/vm-ingress/)
task home-assistant:deploy-config # Deploy HAOS configuration via Ansible with 1Password secrets
task home-assistant:logs          # View Home Assistant logs
task home-assistant:restart-after-config # Restart after config deployment
task home-assistant:status        # Show VM and ingress status
task home-assistant:vm-start      # Start the Home Assistant VM
task home-assistant:vm-stop       # Stop the Home Assistant VM
task home-assistant:vm-restart    # Restart the Home Assistant VM
task home-assistant:console       # SSH to Home Assistant (requires SSH add-on)
task home-assistant:snapshot      # Create Proxmox VM snapshot

# GitLab (VM on pve-nas-01 with Traefik ingress)
# Note: GitLab runners, agent, and ingress routes live in kubernetes/apps/gitlab-runner*, gitlab-agent, and vm-ingress
# and are reconciled by Flux. The VM-side tasks below manage the GitLab server itself.
task gitlab:deploy                # Deploy GitLab (VM + application)
task gitlab:deploy-check          # Dry-run deployment
task gitlab:status                # Show GitLab and runner status
task gitlab:verify                # Run smoke tests (web UI, registry, pages, SSH)
task gitlab:backup                # Create GitLab backup
task gitlab:console               # SSH to GitLab VM
task gitlab:logs                  # View GitLab logs
task gitlab:reconfigure           # Reconfigure GitLab after changes

# Version discovery (automated update checking)
task maintenance:check-versions        # Check all 40 managed services for available updates
task maintenance:check-versions-json   # JSON output for scripting
task maintenance:update-version        # Update single service: SERVICE=gluetun
task maintenance:update-all-versions   # Update all outdated versions in all.yml

# Maintenance (Base Infrastructure)
task maintenance:update-full           # Full base update (OS + apps, interactive)
task maintenance:update-full-auto      # Full base update (OS + apps, auto-reboot)
task maintenance:update-packages       # OS packages only
task maintenance:update-applications   # Applications only (AdGuard, Tailscale, Plex)
task maintenance:update-plex           # Plex Media Server only

# Maintenance (K3s Cluster)
# Helm chart + container image updates are now driven by Flux:
# edit versions in ansible/inventories/prod/group_vars/all.yml, run `task flux:sync-versions`,
# then commit + push — Flux reconciles the new versions within ~1 minute.
task maintenance:update-k3s-nodes      # Rolling k3s node upgrades (drain/cordon)
task maintenance:update-cluster        # Check all service versions + update all.yml + regenerate versions ConfigMap (commit + push to reconcile via Flux)

# Terraform
task terraform:init               # Initialize Terraform
task terraform:plan               # Plan changes
task terraform:apply              # Apply changes
task terraform:validate           # Validate syntax

# Linting and validation
task lint                         # Lint everything (Ansible, Terraform, Flux, Python scripts)
task kubernetes:lint              # Alias for flux:lint (kept for discoverability)
task kubernetes:validate-helm     # Alias for flux:lint (kept for discoverability)

# State collection
task collect-state                # Generate cluster snapshot
```

### Manual Ansible

```bash
# Install collections
ansible-galaxy install -r ansible/requirements.yml

# Ping all hosts
ansible all -m ping

# Dry-run deployment
ansible-playbook ansible/playbooks/site.yml --check

# Deploy to specific host
ansible-playbook ansible/playbooks/site.yml --limit pve-nas-01

# Deploy specific role
ansible-playbook ansible/playbooks/base.yml --tags ssh
```

### Manual Terraform

> **Prefer `task terraform:*` commands** -- they handle Cloudflare API credentials and GitLab HTTP state backend auth automatically via `op run`. Manual commands require exporting `TF_VAR_cloudflare_api_token`, `TF_VAR_cloudflare_account_id`, and `TF_HTTP_*` env vars.

```bash
cd terraform/cloudflare
terraform init
terraform plan
terraform apply
```

## Secrets Management (1Password)

Two consumers pull from the same 1Password "Homelab" vault:

1. **Ansible / Terraform / Task wrapper** — uses `op run --` to inject `op://Homelab/Item/field` references at runtime.
   ```yaml
   # Format in group_vars/all.yml
   secrets:
     smtp_gmail_password: "op://Homelab/SMTP Relay Gmail/password"
     # Item names with spaces are fine here — `op run` parses the full path.
   ```

2. **External Secrets Operator in the cluster** — the `onepassword-homelab` ClusterSecretStore (namespace `external-secrets`, 1Password Connect provider) syncs `ExternalSecret` resources into Kubernetes `Secret`s. Connect runs in-cluster (no calls to 1Password cloud). `ExternalSecret.spec.data[].remoteRef.key` is the **1Password item title**, and `remoteRef.property` is the **field name**:
   ```yaml
   remoteRef:
     key: <1Password item title>
     property: <field name>
   ```

3. **CI pipelines** — `.gitlab-ci.yml` uses `op run` / `op read` with `OP_SERVICE_ACCOUNT_TOKEN` to inject secrets at runtime. This is separate from Connect and unchanged by the migration.

The bootstrap Secrets `op-credentials` and `onepassword-connect-token` in the `external-secrets` namespace are the **only manually created** Kubernetes Secrets. Every other in-cluster Secret is produced by ESO from `ExternalSecret` manifests reconciled by Flux.

```bash
# Create Connect server (generates 1password-credentials.json in current dir)
op connect server create weisssrv-connect --vaults Homelab

# Create access token
op connect token create weisssrv-eso --server <server-id> --vaults Homelab

# Create bootstrap secrets in cluster
kubectl -n external-secrets create secret generic op-credentials \
  --from-file=1password-credentials.json=./1password-credentials.json
kubectl -n external-secrets create secret generic onepassword-connect-token \
  --from-literal=token=<TOKEN>
```

**NEVER commit secrets to git**. All sensitive values use 1Password references (`op://` for host-side tooling, item titles in ExternalSecrets for in-cluster).

### Required 1Password Items

In vault "Homelab":
- **Cloudflare DNS Token** - API token (credential) + account ID (username field)
- **SMTP Relay Gmail** - username + app password
- **SMTP Relay Auth** - username + password (for null client auth to smtp-relay)
- **Email Config** - root_alias (ericsweiss1@gmail.com)
- **AdGuard Home** - admin username + password
- **Tailscale Auth Key** - auth key
- **SSH Key** - public + private key
- **Samba NAS User** - nas user password
- **DNS-01 SSH Key** - private + public key (for cert distribution)
- **K3s Cluster Token** - cluster join token (credential)
- **Authentik Secrets** - secret-key, postgresql-password, postgresql-admin-password
- **PrivadoVPN Credentials** - openvpn-user, openvpn-password (for Gluetun VPN sidecar)
- **VPN Unlimited Credentials** - openvpn-user, openvpn-password (alternate VPN provider)
- **Mealie Secrets** - postgres-password
- **Mealie SSO** - oidc-client-id, oidc-client-secret (Authentik OIDC, REQUIRED - password login disabled)
- **Bar Assistant Secrets** - meilisearch-master-key
- **Bar Assistant SSO** - authentik-client-id, authentik-client-secret (Authentik OIDC, REQUIRED - password login disabled)
- **OpenAI API Key** - api-key (for Mealie recipe parsing, optional)
- **Home Assistant SSO** - authentik-client-id, authentik-client-secret (Authentik OIDC via hass-openid)
- **Home Assistant API Token** - token (long-lived access token for Prometheus /api/prometheus endpoint)
- **GitLab** - root-password (initial GitLab root user password)
- **GitLab API Token** - credential (personal access token for PR-Agent AI code review)
- **GitLab SSO** - saml-cert-fingerprint (Authentik SAML)
- **GitLab Runner** - runner-token (glrt-* format, tags: k8s-deploy, run untagged: yes, shared multi-project runner)
- **GitLab Runner Privileged** - runner-token (glrt-* format, tags: infrastructure, run untagged: no, weisssrv infrastructure runner)
- **GitLab Agent Token** - credential (agent token for GitLab Kubernetes Agent, registered via Operate > Kubernetes clusters)
- **GitHub Token** - credential (personal access token for version checker API rate limits)
- **GitLab Terraform State Token** - credential (project access token for Terraform HTTP state backend, local use)
- **K3s Kubeconfig** - kubeconfig file content (used by .k3s-deploy-base CI template as fallback; agent is preferred)
- **Service Account Auth Token weisssrv** - 1Password Service Account token used by CI (`OP_SERVICE_ACCOUNT_TOKEN` in `.gitlab-ci.yml`)
- **Flux GitLab PAT** - personal access token used by Flux to read `kubernetes/` from the GitLab repo
- **Flux Webhook Token** - auto-generated hex token shared between GitLab webhook config and the Flux Receiver for push-triggered reconciliation
- **Plex Token** - token (X-Plex-Token for Plex exporter metrics)
- **Download Client API Keys** - sonarr-api-key, radarr-api-key, lidarr-api-key, prowlarr-api-key (from each app's Settings > General)
- **Grafana SSO** - oidc-client-id, oidc-client-secret (Authentik OIDC for Grafana)
- **Proxmox API Token** - user, token-name, token-secret (PVEAuditor role, for Proxmox exporter)
- **Discord Alert Webhook** - url (Discord channel webhook for Alertmanager notifications)

### Using 1Password

```bash
# Sign in
eval $(op signin)

# Read a secret
op read "op://Homelab/SMTP Relay Gmail/password"

# Inject into environment
export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare DNS Token/credential")
```

## DNS Architecture (Important!)

**Split-horizon DNS**:
- **Internal** (`*.esweiss.com`): AdGuard Home rewrites → internal IPs
- **External** (`*.ericsweiss.com`): Cloudflare (Terraform) → public/VIP IPs

**Current DNS servers**:
- Primary: 192.168.0.150 (dns-01)
- Secondary: 192.168.0.160 (dns-02)
- Upstream: Unbound on 127.0.0.1:5335 (DoT to Cloudflare/Google)

## Network / IP Allocation

### Infrastructure
- Proxmox hosts: .102-.109
- DNS: .150, .160
- SMTP: .151
- Services: .152-.155

### K3s Cluster
- API VIP: .161
- Servers: .22X range (.222, .223, .227)
- Agents: .20X range (.202, .203, .204, .205, .206, .207)
- MetalLB: .100 (public), .101 (internal)

### Firewall IP Sets
- `admin_lan`: 192.168.0.0/24
- `admin_ts`: 100.64.0.0/10 (Tailscale)
- `core-cluster`: All infra nodes (.102-.107, .150-.155, .160, .202-.207, .222-.227)
- `k3s_nodes`: k3s VMs + API VIP
- `pve_hosts`: Proxmox hosts only
- `nfs_clients`: Hosts allowed NFS
- `smb_clients`: Entire LAN

## Ansible Roles

1. **base** - Packages, SSH hardening, fail2ban, users, timezone, DNS configuration
2. **qol** - zsh + Oh My Zsh (15 plugins), neovim + Vundle, fzf, ripgrep
3. **postfix_null_client** - Local mail relay to smtp-relay
4. **tailscale** - VPN setup (manual `tailscale up` required)
5. **proxmox_firewall** - IPSets, security groups, cluster.fw
6. **proxmox_vm** - VM provisioning with cloud-init and autostart configuration
7. **proxmox_lxc** - LXC container provisioning with autostart configuration
8. **nas_storage** - ZFS properties, NFS exports, Samba, mergerfs, media-mover, archive-backupctl, SMART
9. **unbound** - DoT recursive resolver (port 5335)
10. **adguard_home** - DNS filtering + DoT (port 53), running as non-root
11. **acme_certs** - Let's Encrypt via DNS-01, cert distribution via SSH
12. **smtp_relay** - Postfix relay to Gmail via SASL + incoming auth
13. **adguard_sync** - Sync dns-01 → dns-02 via systemd timer (every 5min)
14. **k3s** - K3s cluster installation and configuration
15. **plex** - Plex Media Server installation and configuration
16. **home_assistant** - Home Assistant configuration deployment via SSH/SCP (HAOS cannot be managed traditionally)
17. **proxmox_ha** - Proxmox HA rules, resources, and ZFS replication management
18. **gitlab** - GitLab EE (CE features) installation and configuration
19. **resolv_conf** - Shared /etc/resolv.conf management (used by base, adguard_home)
20. **zvol_mount** - Shared ZFS zvol mounting with UUID-based fstab (used by k3s, gitlab)
21. **nic_tuning** - NIC/kernel tuning on Proxmox hosts (codifies AQC113 GRO disable + `net.ipv4.ip_forward` sysctl)
22. **zfs_exporter** - Prometheus ZFS exporter on pve-nas-01 (pool health, dataset usage, scrub status)
23. **unbound_exporter** - Prometheus Unbound exporter on DNS hosts (cache hit rate, query counts)
24. **alloy_host** - Grafana Alloy on non-k8s hosts and k3s VMs for shipping journald logs to Loki via NodePort (on k3s VMs, collects kubelet/containerd/etcd/systemd journals; no duplication with in-cluster DaemonSet which covers container logs only)
25. **node_exporter_host** - Prometheus node_exporter on bare-metal Proxmox hosts for hardware metrics (thermals, SMART, disk I/O). Port 9101 to avoid conflict with k3s DaemonSet on 9100

## User Management

- **Proxmox hosts**: User `eric` with passwordless sudo
- **LXC containers**: User `eric` with passwordless sudo
- **VMs**: User `eric` via cloud-init
- **Services**: Run as dedicated users (adguard, unbound, plex; postfix runs as root)

All hosts use `eric` for SSH access with passwordless sudo. LXC containers are unprivileged (mapped UIDs for security). Note that while we SSH as `eric` to smtp-relay, Postfix itself runs as root (which is normal and expected for mail servers).

## Testing / Deployment Workflow

### Base infrastructure (Ansible/Terraform)

1. **Pre-deployment**:
   ```bash
   task ansible:ping          # Verify connectivity
   task lint                  # Ansible + Terraform + flux:lint + scripts:test (all in one)
   task infra:check           # Dry-run
   ```

2. **Deploy**:
   ```bash
   task infra:deploy          # Full stack
   # Or target specific hosts/roles
   ansible-playbook ansible/playbooks/base.yml --limit pve-nas-01
   ```

3. **Post-deployment**:
   ```bash
   task infra:verify          # Post-deployment verification
   task collect-state         # Snapshot current state
   ```

### Kubernetes workloads (Flux GitOps)

Everything in `kubernetes/` is reconciled by Flux. There is no `kubectl apply` or `helm upgrade` step in the normal workflow — edit YAML, commit, push:

1. **Pre-change**:
   ```bash
   task flux:lint             # kustomize build + kubeconform on every Flux Kustomization
   task flux:dev-apply -- kubernetes/apps/<app>   # Optional: preview change in-cluster (reverted on next reconcile)
   ```

2. **Ship**:
   ```bash
   git add kubernetes/...
   git commit
   git push                   # Flux polls every ~1 min; planned webhook will make this sub-second
   task flux:reconcile        # Optional: force immediate reconcile
   ```

3. **Verify**:
   ```bash
   task flux:status           # Concise health summary
   task flux:verify           # `flux check` + all managed resources
   ```

See `docs/29-flux-operations.md` for day-2 operations including secret rotation, suspend/resume, and webhook setup. Multi-repo tenant onboarding is covered in `docs/30-multi-repo-onboarding.md`.

## Version Management

Application versions are centralized in `ansible/inventories/prod/group_vars/all.yml`. See that file for current version pins — they include base infrastructure (k3s, kube-vip, Authentik, Plex, GitLab), Helm charts (MetalLB, Traefik, cert-manager, external-dns), download clients (Gluetun, NZBGet, qBittorrent, Prowlarr, Sonarr, Radarr, Lidarr, Pulsarr), recipe stack (Mealie, Bar Assistant, Salt Rim), and PostgreSQL versions. Home Assistant (HAOS) is updated manually via its UI and is not version-pinned in all.yml.

**Automated version discovery** (`scripts/check-versions.py`):
- Checks 40 managed services across GitHub releases, Docker Hub, LinuxServer.io, and Helm repos
- Run `task maintenance:check-versions` to see available updates
- Run `task maintenance:update-version SERVICE=<name>` to update a single version in all.yml
- Run `task maintenance:update-all-versions` to update all outdated versions
- Results cached for 1 hour in `.version-cache/`; set `GITHUB_TOKEN` for higher API rate limits

**Update strategy:**
1. **Check for updates:** `task maintenance:check-versions`
2. **Update versions in all.yml:** `task maintenance:update-version SERVICE=<name>` or `task maintenance:update-all-versions`
3. **Deploy:** Run appropriate task (see `docs/12-runbooks.md` for update workflow)

**Version pinning philosophy:**
- k3s, Authentik, Helm charts: Pinned to specific versions for stability
- Download/recipe containers: Pinned to specific stable tags (no "latest") for reproducible deployments
- Bar Assistant / Salt Rim: Pinned to specific versions (check for breaking changes on major bumps)
- Tailscale: Pinned to specific apt version
- Plex: Pinned to specific apt version (set to "latest" for auto-update behavior)
- Home Assistant: Manual updates via HAOS UI (documented version only)

## Storage Architecture

### Storage Strategy

**Automated Storage Selection**: The `proxmox_vm` and `proxmox_lxc` Ansible roles automatically select storage based on the Proxmox host's role:

| Proxmox Host | Role | Default Storage | Details |
|--------------|------|-----------------|---------|
| pve-nas-01 | `nas` | `ssd` | 3x 4TB Samsung SSDs (raidz1) - App data and databases |
| pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 | `compute` / `general` | `local-ssd` | 1TB Samsung 870 EVO per host - VM/container workloads |

Storage can be overridden per-VM/container by setting `proxmox_storage` or `lxc_storage` in the inventory.

**Why local-ssd for compute nodes?**
- **Proxmox HA**: ZFS pools required on all nodes for replication and failover
- **Performance**: Compression (lz4), snapshots, checksumming, atomic operations
- **Stateless workloads**: K3s agents, DNS, SMTP have redundancy via k8s or multiple instances
- **Cost-effective**: 1TB SSD per node is sufficient for local workloads

### NAS Node (pve-nas-01) - Specialized ZFS Pools

**ZFS Pools**:
- `tank` - 6x 22TB raidz2 (~122TB usable) - Media and bulk storage
- `ssd` - 3x 4TB raidz1 (~10.9TB) - App data, databases, and containers
- `nvme` - 1x 4TB NVMe (~2.27TB) - Hot downloads and fast scratch
- `archive` - 4x 6TB raidz1 (~21.8TB) - Cold storage and backups

**Key Datasets**: tank/media, tank/share, ssd/appdata, nvme/media, nvme/fast

**Persistent Storage (ZFS zvols)**:
- `ssd/appdata/authentik/postgres` - 10GB zvol, ext4, attached to k3s-agt-nas-01 as /dev/sdb, mounted at /mnt/postgres-data
- `ssd/appdata/mealie/postgres` - 32GB zvol, ext4, attached to k3s-agt-nas-01 as /dev/sdc, mounted at /mnt/mealie-postgres-data
- `ssd/appdata/gitlab/repos` - 200GB zvol, ext4, attached to gitlab VM as /dev/sdb, mounted at /mnt/gitlab-repos
- `ssd/appdata/prometheus/data` - 150GB zvol, ext4, attached to k3s-agt-nas-01, mounted at /mnt/prometheus-data
- `ssd/appdata/loki/data` - 75GB zvol, ext4, attached to k3s-agt-nas-01, mounted at /mnt/loki-data
- Grafana SQLite DB uses NFS-backed PV at `/appdata/grafana` (1Gi, NFS from pve-nas-01) — persists user preferences and service accounts
- Zvols are defined in `vm_additional_disks` in hosts.yml, created by proxmox_vm role, formatted/mounted by role
- Data survives pod and VM recreation (zvols persist on Proxmox host's ZFS pool)

### Compute Nodes - local-ssd ZFS Pool

**All compute nodes** (pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01):
- Pool: `local-ssd` - 1x 1TB Samsung 870 EVO (~900GB usable)
- Compression: **lz4** (low-latency, optimized for VM workloads)
- Properties: ashift=12, atime=off, autotrim=on, xattr=sa
- Used for k3s VMs and HA-managed containers (dns-01, dns-02, smtp-relay, home-assistant)

**Why lz4 instead of zstd?**
- VM workloads are latency-sensitive (random I/O patterns)
- lz4 has ~10x faster decompression than zstd
- Near-zero CPU overhead vs zstd's higher cost
- Industry standard for VM storage (Proxmox defaults to lz4)

### Resource Pools and Storage Management

**Resource Pools**: infra-core (dns, smtp), apps-public (plex), platform (k3s VMs)

**NEVER create/destroy ZFS pools via Ansible** - pools are created manually (too critical to automate). Ansible only sets properties and mounts. Zvols for persistent storage are managed via `vm_additional_disks` but the parent pools are never touched.

## Documentation

See `docs/` for detailed guides:

**Getting Started**:
- 00-hardware-setup.md - Bare metal to Proxmox ready for Ansible
- 01-overview.md - Architecture and network topology
- 02-install.md - Laptop setup through production deployment
- 03-ssh-users.md - SSH and user management

**Infrastructure Services**:
- 04-qol.md - Quality of life configs (Oh My Zsh, Neovim, etc.)
- 05-tailscale.md - VPN setup
- 06-zfs.md - ZFS configuration with exact pool creation commands
- 07-fileservices.md - NFS and Samba
- 08-dns.md - DNS stack (AdGuard Home + Unbound)
- 09-certs.md - TLS certificates (acme.sh + distribution)
- 10-mail.md - Mail relay configuration
- 11-firewall.md - Proxmox firewall (IPSets + Security Groups)

**Operations & Planning**:
- 12-runbooks.md - Operational procedures
- 13-ci-cd.md - CI/CD pipelines (GitLab CI)
- 14-post-base-plan.md - K3s platform roadmap and workload planning
- 15-credential-rotation.md - Credential rotation procedures
- 16-next-steps.md - TODO and feature roadmap
- 17-disaster-recovery.md - Disaster recovery and backup procedures
- 18-bootstrap-new-systems.md - Bootstrapping new LXC containers and VMs
- 19-k3s-deployment.md - K3s cluster deployment (complete workflow with all components)
- 20-plex-deployment.md - Plex Media Server deployment (LXC with bind mounts)
- 21-download-clients-deployment.md - Download clients and media stack (VPN, *arr apps)
- 22-recipes-deployment.md - Recipe management stack (Mealie, Bar Assistant)
- 23-recipes-sso-setup.md - Recipes SSO and OpenAI configuration
- 24-home-assistant-deployment.md - Home Assistant OS VM with Traefik ingress
- 25-multi-node-expansion.md - Multi-node expansion and Proxmox HA guide
- 26-multi-node-implementation.md - Step-by-step implementation for 6-node cluster
- 27-gitlab-deployment.md - GitLab EE deployment (VM, registry, pages, runners)
- 28-gitlab-migration.md - GitHub to GitLab migration guide
- 29-flux-operations.md - Flux day-2 operations: reconcile, suspend/resume, secret rotation, webhook
- 30-multi-repo-onboarding.md - Adding external tenant repos via `kubernetes/clusters/weisssrv/tenants/`
- 31-observability.md - Observability stack (Prometheus, Grafana, Loki, Alloy, exporters, alerting)

## Important Context Files

- `CLUSTER_STATUS.txt` - Full cluster state snapshot (gitignored; generated locally via `task collect-state`)

## Credits

Repository structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab)
