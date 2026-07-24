# weisssrv

> **Note**: The canonical source for this repository is [git.ericsweiss.com](https://git.ericsweiss.com/eric/weisssrv).
> GitHub is a read-only mirror updated automatically via GitLab push mirroring.
> Please submit issues and merge requests on the GitLab instance.

Homelab Infrastructure as Code - Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, Kubernetes, and Flux.

## Overview

Multi-node Proxmox homelab with:

- **Proxmox Cluster**: 6-node cluster (NAS + 5 compute hosts) with HA enabled
- **Storage**: ZFS pools (tank/ssd/nvme/archive) with NFS and Samba
- **DNS**: Dual AdGuard Home + Unbound (DoT) for ad blocking and secure resolution
- **Mail**: SMTP relay via Gmail for system notifications
- **Certificates**: Let's Encrypt via acme.sh with automated distribution
- **VPN**: Tailscale for secure remote access
- **Firewall**: Proxmox firewall with IPSets and security groups
- **K3s Cluster**: 9-node cluster (3 servers + 6 agents) with etcd HA
- **GitOps**: Flux reconciles all Kubernetes workloads from this repo; External Secrets Operator syncs all k8s Secrets from 1Password (Connect provider)

## Architecture

```
Internet
    |
[Router 192.168.0.1]
    |
[192.168.0.0/24] -- Core LAN
    |
    +-- Proxmox Hosts (6-node cluster)
    |   +-- pve-nas-01    (.102) - NAS + Storage
    |   +-- pve-laptop-01 (.103) - Compute
    |   +-- pve-opt-01    (.104) - Compute
    |   +-- pve-opt-02    (.105) - Compute
    |   +-- pve-opt-03    (.106) - Compute
    |   +-- pve-prec-01   (.107) - Compute
    |
    +-- Infrastructure LXC/VMs
    |   +-- dns-01        (.150) - Primary DNS
    |   +-- dns-02        (.160) - Secondary DNS
    |   +-- smtp-relay    (.151) - Mail relay
    |   +-- plex          (.152) - Plex Media Server
    |   +-- gitlab        (.153) - GitLab EE
    |   +-- home-assistant (.154) - Home Assistant OS
    |   +-- windows        (.155) - Windows 11 VM (IaC shell, interactive install)
    |   +-- nextcloud      (.156) - Nextcloud
    |   +-- immich         (.157) - Immich photos
    |   +-- immich-ml      (.158) - Immich GPU ML (OpenVINO LXC)
    |
    +-- K3s Cluster (9 nodes)
        +-- k3s-srv-nas-01    (.222) - Server + etcd
        +-- k3s-srv-laptop-01 (.223) - Server + etcd
        +-- k3s-srv-prec-01   (.227) - Server + etcd
        +-- k3s-agt-nas-01    (.202) - Agent (NAS workloads)
        +-- k3s-agt-laptop-01 (.203) - Agent (ingress + general)
        +-- k3s-agt-opt-01    (.204) - Agent (ingress + general)
        +-- k3s-agt-opt-02    (.205) - Agent (ingress + general)
        +-- k3s-agt-opt-03    (.206) - Agent (ingress + general)
        +-- k3s-agt-prec-01   (.207) - Agent (general + compute)
```

Canonical host/node/VIP topology reference: [docs/01-overview.md](docs/01-overview.md).

## Quick Start

### Prerequisites

- macOS/Linux workstation
- [Task](https://taskfile.dev/) runner
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`)
- Python 3 with `pip` (for the lint/test tooling in `requirements.txt`)
- Ansible and Terraform

### Setup

```bash
# Clone from GitLab (canonical source)
git clone https://git.ericsweiss.com/eric/weisssrv.git
cd weisssrv

# Or clone from GitHub (read-only mirror)
# git clone https://github.com/ericsweiss/weisssrv.git

# Install Python lint/test tooling (Ansible, Molecule, ansible-lint, yamllint).
# The script unit tests run by `task lint` additionally need pytest + pyyaml:
#   pip install pytest pyyaml
pip install -r requirements.txt

# Install Ansible collections
task ansible:install-collections

# Sign in to 1Password
eval $(op signin)

# Test connectivity
task ansible:ping
```

### One-time pre-deploy: pin SSH host keys

Cert distribution and Home Assistant config deploys use
`StrictHostKeyChecking=yes` against host keys pinned in inventory
(`cert_distribution_targets[*].host_key` in `host_vars/dns-01.yml`, captured
via `task certs:show-host-keys`; `haos_ssh_host_key` in `group_vars/all.yml`
for HAOS on port 22222) — entries ship empty and each role fails loudly until
populated. Bootstrap and rotation procedures live in
[ansible/roles/acme_certs/README.md](ansible/roles/acme_certs/README.md) and
[ansible/roles/home_assistant/README.md](ansible/roles/home_assistant/README.md).

### Common Operations

`task --list` is the authoritative task reference — the essentials:

```bash
task --list                    # List all available tasks

# Validation and linting
task lint                      # Lint everything (Ansible, Terraform, Kubernetes, scripts)
task infra:check               # Dry-run base infrastructure deployment
task ansible:test              # Run Molecule unit tests

# Base infrastructure deployment (Ansible)
task infra:deploy              # Deploy all base infrastructure
task infra:verify              # Verify deployment status

# K3s cluster operations (Ansible - node layer)
task k3s:deploy                # Deploy/update k3s cluster
task k3s:status                # Show cluster and workload status

# Flux GitOps (all Kubernetes workloads deploy via git push)
task flux:status               # Concise health summary
task flux:reconcile            # Force reconciliation
task flux:sync-versions        # Regenerate versions-configmap from all.yml

# Maintenance
task maintenance:check-versions       # Check all services for updates
task collect-state                    # Generate cluster state snapshot
```

> **Kubernetes deploys use Flux**: changes to `kubernetes/apps/<component>/` or
> `kubernetes/infrastructure/` are reconciled automatically after `git push` —
> push-triggered via the GitLab agent's Flux module, with the ~1-minute git
> poll as fallback. `task flux:reconcile` forces a sync.
> Helm chart versions and container image tags flow through `ansible/inventories/prod/group_vars/all.yml`
> into the `cluster-versions` ConfigMap via `task flux:sync-versions`.

## Repository Structure

```
weisssrv/
├── .gitlab-ci.yml            # CI/CD pipeline (canonical)
├── ansible/
│   ├── inventories/prod/     # Production inventory
│   │   ├── hosts.yml         # Host definitions
│   │   └── group_vars/       # Group variables (all.yml: single source of truth for versions)
│   ├── roles/                # Ansible roles (base, k3s, gitlab, nic_tuning, etc.)
│   └── playbooks/            # Deployment playbooks
├── terraform/
│   ├── cloudflare/           # Cloudflare DNS management
│   ├── tailscale/            # Tailnet ACL policy-as-code (SSH rules, subnet-route auto-approval)
│   └── authentik/            # Authentik SSO state as code (applications, providers, groups)
├── kubernetes/               # Flux-managed cluster state
│   ├── clusters/weisssrv/    # Flux bootstrap + top-level Kustomizations
│   ├── infrastructure/       # Platform — four subdirectories (sources, controllers, configs, observability)
│   │                         #   reconciled in dependsOn order; configs fans out to observability and apps:
│   │                         #     infrastructure-sources       (HelmRepository CRs + versions-configmap)
│   │                         #     infrastructure-controllers   (platform HelmReleases — see the dir for the current set)
│   │                         #     infrastructure-configs       (CRs requiring controller CRDs — see the dir for the current set)
│   │                         #     infrastructure-observability (kube-prometheus-stack, Loki, Alloy, exporters, dashboards)
│   └── apps/                 # Sibling top-level Kustomization (dependsOn infrastructure-configs,
│                             #   parallel to observability so its health can't freeze app reconciliation):
│                             #   one dir per app — see kubernetes/apps/ for the current set
├── scripts/                  # Utility scripts (version checker, versions-configmap generator, etc.)
├── docs/                     # Documentation
└── Taskfile.yml              # Task runner commands (including flux:*)
```

## Ansible Roles

| Role | Purpose |
|------|---------|
| base | Packages, SSH hardening, users, timezone, DNS configuration |
| qol | zsh + Oh My Zsh, neovim, fzf, ripgrep |
| postfix_null_client | Local mail relay to smtp-relay |
| tailscale | VPN setup |
| proxmox_firewall | IPSets and security groups |
| proxmox_vm | VM provisioning (Linux cloud-init or Windows 11 OVMF/TPM shell) + autostart |
| proxmox_lxc | LXC container provisioning with autostart |
| proxmox_ha | Proxmox HA rules, resources, and ZFS replication |
| proxmox_backup | Declarative Proxmox backup config (storage.cfg entries + nightly vzdump jobs) |
| nas_storage | ZFS, NFS, Samba, MergerFS, SMART monitoring |
| unbound | DoT recursive resolver (port 5335) |
| adguard_home | DNS filtering (non-root, port 53) |
| acme_certs | Let's Encrypt certificates with distribution |
| smtp_relay | Gmail SMTP relay with SASL auth |
| adguard_sync | DNS sync (dns-01 -> dns-02) via systemd timer |
| k3s | K3s cluster installation and configuration |
| plex | Plex Media Server with Intel GPU transcoding |
| home_assistant | Home Assistant OS configuration management |
| gitlab | GitLab EE installation and configuration |
| nextcloud | Nextcloud (Docker Compose: nextcloud-apache + postgres + redis + cron + exporter) on a NAS-pinned VM, host-nginx TLS, Authentik OIDC SSO (docs/35) |
| immich | Immich photo management (docker-compose stack + host nginx) on a NAS-pinned VM |
| immich_ml | Immich machine learning (OpenVINO on the Intel Arc GPU) in a docker LXC on the NAS — the Immich VM's primary ML endpoint (docs/36) |
| resolv_conf | Shared /etc/resolv.conf management |
| zvol_mount | Shared ZFS zvol mounting with UUID-based fstab |
| apt_signed_repo | Shared fingerprint-verified signed-APT-repo setup (used by alloy_host, gitlab, plex, nextcloud, immich, immich_ml) |
| nic_tuning | NIC/kernel tuning (AQC113 GRO disable, `ip_forward` sysctl drop-in, active-backup bond `all_slaves_active` MAC-flap guard — docs/34) |
| prometheus_exporter | Shared install pipeline for download-based exporters (tarball/.deb); backs zfs_exporter + unbound_exporter |
| textfile_collector | Shared textfile-collector oneshot service + timer scaffold; backs node_exporter_host (corosync/zpool/smartmon) and smtp_relay (postfix queue) |
| zfs_exporter | Prometheus ZFS exporter (pool health, scrub status) on the NAS; thin wrapper over prometheus_exporter |
| unbound_exporter | Prometheus Unbound exporter on DNS hosts; thin wrapper over prometheus_exporter |
| node_exporter_host | Prometheus node_exporter on bare-metal Proxmox hosts (port 9101); standalone (apt-repo install + drop-in override + textfile collectors) |
| alloy_host | Grafana Alloy on non-k8s hosts and k3s VMs for journald → Loki |
| zfs_encryption | Boot-time ZFS pool key fetch from 1Password Connect |
| nfs_tls | NFSv4 over kernel TLS via tlshd (opt-in, `nfs_tls_enabled`) |
| restic_offsite | Nightly offsite backup to Backblaze B2 (restic via rclone, client-side encrypted) on the NAS; chained after archive-backup, reads archsync snapshots + clones the immich/nextcloud data zvols (docs/42) |
| encrypted_swap | dm-crypt plain-mode random-key encrypted swap (AES-256-XTS, crypttab + fstab) on the six Proxmox hosts; live-switch when memory-safe, else defer to reboot (docs/42) |
| zfs_arc_cap | Cap the ZFS ARC on the compute Proxmox hosts (modprobe.d + initramfs + live sysfs); protects the VFIO GPU host's pinned guest RAM from an uncapped ARC (docs/43). NAS ARC stays owned by nas_storage |
| vfio_passthrough | Host-side GPU VFIO codification on pve-prec-01 (IOMMU cmdline, nouveau blacklist, vfio-pci bind); stages config, prints reboot-required, never auto-reboots (docs/43) |

## Secrets Management

All secrets stored in 1Password, injected at runtime:

```yaml
# group_vars/all.yml
secrets:
  smtp_gmail_password: "op://Homelab/SMTP Relay Gmail/password"
```

In-cluster Kubernetes Secrets are produced by External Secrets Operator from
`ExternalSecret` manifests. The `remoteRef` key/property format is specified in
[docs/29-flux-operations.md](docs/29-flux-operations.md) ("1Password Connect
Provider Reference Format"); the multi-consumer overview (Ansible/Terraform,
ESO, CI) lives in the Secrets Management section of `CLAUDE.md`.

**Never commit secrets to git.**

## DNS Architecture

Split-horizon DNS:
- **Internal** (`*.esweiss.com`): AdGuard Home rewrites
- **External** (`*.ericsweiss.com`): Cloudflare (Terraform)

## K3s Platform

9-node HA cluster (3 servers + 6 agents) with:
- **kube-vip**: API VIP at 192.168.0.161 (the 3-node etcd quorum tolerates 1 server failure)
- **MetalLB**: LoadBalancer IPs (.100 public, .101 internal)
- **Traefik**: Ingress controller (TLS served from cert-manager wildcard certs)
- **external-dns**: Automatic Cloudflare DNS management
- **cert-manager**: Let's Encrypt certificate automation
- **Authentik**: SSO/OIDC identity provider (auth.esweiss.com)
- **Flux**: Reconciles all Kubernetes manifests from this repo
- **External Secrets Operator**: Syncs k8s Secrets from 1Password (Connect provider, vault `Homelab`)
- **Autoscaling + node ops**: VPA, Reloader, kured (coordinated reboots) — full controller set in `kubernetes/infrastructure/controllers/kustomization.yaml`

See [docs/19-k3s-deployment.md](docs/19-k3s-deployment.md) for deployment guide.
See also [docs/29-flux-operations.md](docs/29-flux-operations.md) (operator guide)
and [docs/30-multi-repo-onboarding.md](docs/30-multi-repo-onboarding.md)
(adding external repos that deploy into this cluster).

## Applications

### Authentik SSO

Identity provider for Single Sign-On across all applications:

- **URL**: auth.esweiss.com
- **Features**:
  - OIDC/OAuth2 provider for Mealie, Bar Assistant, Home Assistant
  - SAML provider for GitLab
  - PostgreSQL data on persistent ZFS zvol
- **Documentation**: [kubernetes/apps/authentik/README.md](kubernetes/apps/authentik/README.md)

### Plex Media Server

LXC container on NAS with Intel GPU passthrough for hardware transcoding.

- **URL**: plex.esweiss.com
- **Documentation**: [docs/20-plex-deployment.md](docs/20-plex-deployment.md)

### Download Clients and Media Stack

VPN-protected download clients with media management applications:

| Service | Purpose | URL |
|---------|---------|-----|
| Gluetun | VPN gateway with killswitch | - |
| NZBGet | Usenet downloads | nzbget.esweiss.com |
| qBittorrent | BitTorrent downloads | qbittorrent.esweiss.com |
| Prowlarr | Indexer manager | prowlarr.esweiss.com |
| Sonarr | TV shows | tv.esweiss.com |
| Radarr | Movies | movies.esweiss.com |
| Lidarr | Music | music.esweiss.com |
| Pulsarr | Plex Watchlist automation | pulsarr.esweiss.com |

All services are protected by Authentik SSO and internal-only DNS.

- **Documentation**: [docs/21-download-clients-deployment.md](docs/21-download-clients-deployment.md)

### Recipe Management

Mealie and Bar Assistant for food and cocktail recipe management:

| Service | Purpose | URL |
|---------|---------|-----|
| Mealie | Recipe management and meal planning | food.esweiss.com |
| Bar Assistant | Cocktail recipe management | bar.esweiss.com |

Both services use Authentik SSO for authentication. Bar Assistant additionally
runs its Salt Rim web UI, Meilisearch, and Redis — see docs/22 for the full
component list.

- **Documentation**: [docs/22-recipes-deployment.md](docs/22-recipes-deployment.md)

### Home Assistant

Home automation platform running on Home Assistant OS:

- **URLs**: home.esweiss.com (internal), home.ericsweiss.com (external)
- **Authentication**: Authentik SSO via hass-openid custom integration
- **Features**:
  - Traefik ingress with WebSocket support
  - API bypass routes for download client integrations
  - Configuration managed via Ansible with 1Password secrets
- **Documentation**: [docs/24-home-assistant-deployment.md](docs/24-home-assistant-deployment.md)

### GitLab

Self-hosted Git repository and CI/CD platform:

- **URLs**: git.esweiss.com (internal), git.ericsweiss.com (external)
- **Features**:
  - GitLab EE (CE features) on dedicated VM (version pinned in `ansible/inventories/prod/group_vars/all.yml`)
  - Container Registry (registry.git.ericsweiss.com)
  - GitLab Pages (*.pages.git.ericsweiss.com)
  - CI/CD Runners on k3s cluster (infrastructure + shared multi-project)
  - Authentik SAML SSO integration
  - Git SSH access on port 2222 (external)
- **Documentation**: [docs/27-gitlab-deployment.md](docs/27-gitlab-deployment.md)

### Nextcloud

Self-hosted file sync and collaboration on a NAS-pinned VM:

- **URLs**: cloud.esweiss.com (internal), cloud.ericsweiss.com (external)
- **Stack**: Docker Compose (nextcloud-apache + PostgreSQL + Redis + cron + exporter) on VM .156, all state on ZFS zvol passthrough disks (no NFS), host-nginx TLS
- **Authentication**: Authentik OIDC SSO-only
- **Documentation**: [docs/35-nextcloud.md](docs/35-nextcloud.md)

### Immich

Self-hosted photo and video management on a NAS-pinned VM:

- **URLs**: photos.esweiss.com (internal), photos.ericsweiss.com (external)
- **Stack**: docker-compose (immich-server + CPU ML + release-pinned Postgres/vectorchord + Valkey) on VM .157, encrypted zvols (photo library on `tank/immich-data`), host-nginx TLS, nightly pg_dump
- **Authentication**: Authentik OIDC SSO-only
- **Documentation**: [docs/36-immich.md](docs/36-immich.md)

### WireGuard VPN (wg-easy)

Internet-exit VPN for the user + friends/family (`wg-easy` v15):

- **Endpoint**: vpn.ericsweiss.com:51820/udp (WAN → MetalLB VIP .99)
- **Admin UI**: vpn.esweiss.com (internal-only, Authentik `vpn-admins`)
- **Model**: full-tunnel internet exit; clients are fenced out of the LAN by a
  two-layer egress no-LAN fence (client full-tunnel + public DNS, and a CNI
  egress NetworkPolicy killswitch that also blocks internal DNS), plus a
  separate `-dest`-scoped WAN firewall rule that scopes the inbound endpoint.
  Public client DNS (1.1.1.1), IPv4-only.
- **Documentation**: [docs/38-wireguard-vpn.md](docs/38-wireguard-vpn.md)

### Observability (Grafana)

Metrics, logs, dashboards, and alerting for the whole platform:

- **URL**: grafana.esweiss.com
- **Stack**: Prometheus + Grafana + Loki + Alloy, plus exporters (Proxmox, ZFS, AdGuard, Unbound, Blackbox, Plex, Exportarr)
- **Authentication**: Authentik OIDC
- **Features**: community + custom dashboards via the `grafana_dashboard` ConfigMap sidecar, Loki log datasource, Discord/email alerting
- **Documentation**: [docs/31-observability.md](docs/31-observability.md)

### Hermes Agent

NousResearch autonomous AI agent platform with a web dashboard:

- **URLs**: agent.esweiss.com (internal), agent.ericsweiss.com (external)
- **Authentication**: the dashboard's own Authentik-OIDC login on both hostnames (`hermes-users` group gate; an auth provider is mandatory on its 0.0.0.0 bind), with a Traefik-only NetworkPolicy restricting ingress. Authentik objects in `terraform/authentik` (docs/40)
- **Workload**: one pod, three containers (gateway supervisor + FastAPI dashboard + camofox anti-detection browser sidecar) off a self-built image (upstream ships none — built by the `build-hermes-agent` CI job); NFS `/opt/data` state on encrypted `ssd/appdata`
- **Memory backend**: a cluster-internal Hindsight deployment (no ingress) serves Hermes' long-term memory — see [docs/37-hermes.md](docs/37-hermes.md) (§Memory backend)
- **Documentation**: [docs/37-hermes.md](docs/37-hermes.md)

### Windows 11 VM

On-demand Windows 11 desktop (OVMF/TPM/q35 shell provisioned via `proxmox_vm`):

- **Access**: RDP to VM .155 (NAS-pinned; kept powered off by default and started on demand)
- **Documentation**: [docs/39-windows-vm.md](docs/39-windows-vm.md)

### Homarr

Homelab dashboard/launcher for every service in the cluster:

- **URLs**: dashboard.esweiss.com (internal), dashboard.ericsweiss.com (external)
- **Authentication**: Authentik OIDC, SSO-only (`homarr-admins` group gate, admin via the OIDC `groups` claim; no standing local admin — DR via docs/41 §SSO); Authentik objects in `terraform/authentik` (docs/40)
- **Workload**: raw k3s manifests (`kubernetes/apps/homarr/`), NFS-backed SQLite state on encrypted `ssd/appdata`
- **Integrations**: direct in-cluster/LAN URLs (bypassing the SSO perimeter) to the *arr stack, qBittorrent/NZBGet, AdGuard, Proxmox, Plex, Home Assistant, Nextcloud, and Immich
- **Documentation**: [docs/41-homarr.md](docs/41-homarr.md)

## Documentation

### Getting Started

| Document | Description |
|----------|-------------|
| [00-hardware-setup](docs/00-hardware-setup.md) | Bare metal to Proxmox ready for Ansible |
| [01-overview](docs/01-overview.md) | Architecture and network topology |
| [02-install](docs/02-install.md) | Laptop setup through production deployment |
| [03-ssh-users](docs/03-ssh-users.md) | SSH and user management |

### Infrastructure Services

| Document | Description |
|----------|-------------|
| [04-qol](docs/04-qol.md) | Quality of life configs (Oh My Zsh, Neovim, etc.) |
| [05-tailscale](docs/05-tailscale.md) | VPN setup |
| [06-zfs](docs/06-zfs.md) | ZFS configuration with exact pool creation commands |
| [07-fileservices](docs/07-fileservices.md) | NFS and Samba |
| [08-dns](docs/08-dns.md) | DNS stack (AdGuard Home + Unbound) |
| [09-certs](docs/09-certs.md) | TLS certificates (acme.sh + distribution) |
| [10-mail](docs/10-mail.md) | Mail relay configuration |
| [11-firewall](docs/11-firewall.md) | Proxmox firewall (IPSets + Security Groups) |

### Operations and Planning

| Document | Description |
|----------|-------------|
| [12-runbooks](docs/12-runbooks.md) | Operational procedures |
| [13-ci-cd](docs/13-ci-cd.md) | CI/CD pipelines (GitLab CI) |
| [14-post-base-plan](docs/14-post-base-plan.md) | K3s platform roadmap and workload planning (superseded — historical record) |
| [15-credential-rotation](docs/15-credential-rotation.md) | Credential rotation procedures |
| [16-next-steps](docs/16-next-steps.md) | TODO and feature roadmap |
| [17-disaster-recovery](docs/17-disaster-recovery.md) | Disaster recovery and backup procedures |
| [18-bootstrap-new-systems](docs/18-bootstrap-new-systems.md) | Bootstrapping new LXC containers and VMs |
| [19-k3s-deployment](docs/19-k3s-deployment.md) | K3s cluster deployment (complete workflow) |
| [20-plex-deployment](docs/20-plex-deployment.md) | Plex Media Server deployment |
| [21-download-clients-deployment](docs/21-download-clients-deployment.md) | Download clients and media stack |
| [22-recipes-deployment](docs/22-recipes-deployment.md) | Recipe management (Mealie, Bar Assistant) |
| [23-recipes-sso-setup](docs/23-recipes-sso-setup.md) | Recipes SSO and OpenAI configuration |
| [24-home-assistant-deployment](docs/24-home-assistant-deployment.md) | Home Assistant OS with Authentik SSO |
| [25-multi-node-expansion](docs/25-multi-node-expansion.md) | Multi-node expansion and Proxmox HA |
| [26-multi-node-implementation](docs/26-multi-node-implementation.md) | Step-by-step 6-node cluster implementation (completed — retained for rebuild reference) |
| [27-gitlab-deployment](docs/27-gitlab-deployment.md) | GitLab EE deployment (VM, registry, pages, runners) |
| [28-gitlab-migration](docs/28-gitlab-migration.md) | GitHub to GitLab migration guide |
| [29-flux-operations](docs/29-flux-operations.md) | Flux operator guide (bootstrap, adopt, rotate, add app, troubleshoot) |
| [30-multi-repo-onboarding](docs/30-multi-repo-onboarding.md) | Adding external repos that deploy into this cluster via Flux |
| [31-observability](docs/31-observability.md) | Observability stack (Prometheus, Grafana, Loki, Alloy, exporters, alerting) |
| [32-zfs-encryption](docs/32-zfs-encryption.md) | ZFS native encryption with passphrase-from-Connect boot-time unlock |
| [33-autoscaling](docs/33-autoscaling.md) | VPA tiers, CoreDNS HPA pin, hand-tuned baselines, Proxmox-level guidance |
| [34-bond-mac-flapping](docs/34-bond-mac-flapping.md) | active-backup bond `all_slaves_active` MAC-flap black-hole: diagnosis, recovery, nic_tuning guard |
| [35-nextcloud](docs/35-nextcloud.md) | Nextcloud VM (Docker Compose, zvol storage, host-nginx TLS, Authentik OIDC SSO, backups, observability, runbooks) |
| [36-immich](docs/36-immich.md) | Immich photo management (NAS-pinned VM, docker-compose, encrypted zvols, Authentik OIDC, backups) |
| [37-hermes](docs/37-hermes.md) | Hermes Agent (NousResearch AI agent + dashboard): self-built image, three-container pod, dashboard Authentik-OIDC SSO |
| [38-wireguard-vpn](docs/38-wireguard-vpn.md) | wg-easy internet-exit VPN (two-layer no-LAN egress fence, client onboarding, restore) |
| [39-windows-vm](docs/39-windows-vm.md) | Windows 11 VM (OVMF/TPM/q35 shell via proxmox_vm, interactive install, RDP) |
| [40-authentik-terraform](docs/40-authentik-terraform.md) | Authentik SSO as code (terraform/authentik day-2 ops: drift, rotation, DR) |
| [41-homarr](docs/41-homarr.md) | Homarr dashboard (raw manifests, Authentik OIDC, NFS SQLite, direct-URL integrations) |
| [42-offsite-backup](docs/42-offsite-backup.md) | Offsite backup (restic → Backblaze B2, client-side encrypted) + encrypted swap |
| [43-gpu-passthrough](docs/43-gpu-passthrough.md) | GPU passthrough (pve-prec-01 1660 Ti → Hindsight): VFIO host prep, driver/toolkit, device plugin, DCGM, RAM right-sizing, operator runbook |

**Agent guidance**: coding agents should start from the
[`weisssrv-development` skill](.claude/skills/weisssrv-development/SKILL.md) — it
maps the repo workflow, pre-MR gates, and change-type decision tree onto the docs
above. `CLAUDE.md` / `AGENTS.md` / `.cursorrules` all defer to it.

## User Management

All hosts use user `eric` with passwordless sudo:
- Proxmox hosts
- LXC containers (unprivileged with mapped UIDs)
- K3s VMs (via cloud-init)

Note: While Postfix on smtp-relay runs as root (normal for mail servers), SSH access is still via user `eric`.

## Credits

Structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab).

## License

MIT License - see [LICENSE](LICENSE) file.
