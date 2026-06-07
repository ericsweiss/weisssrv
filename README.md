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
    |
    +-- K3s Cluster (9 nodes)
        +-- k3s-srv-nas-01    (.222) - Server + etcd
        +-- k3s-srv-laptop-01 (.223) - Server + etcd
        +-- k3s-srv-prec-01   (.227) - Server + etcd
        +-- k3s-agt-nas-01    (.202) - Agent (NAS workloads)
        +-- k3s-agt-laptop-01 (.203) - Agent (ingress)
        +-- k3s-agt-opt-01    (.204) - Agent (ingress)
        +-- k3s-agt-opt-02    (.205) - Agent (ingress)
        +-- k3s-agt-opt-03    (.206) - Agent (ingress)
        +-- k3s-agt-prec-01   (.207) - Agent (compute)
```

## Quick Start

### Prerequisites

- macOS/Linux workstation
- [Task](https://taskfile.dev/) runner
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`)
- Ansible and Terraform

### Setup

```bash
# Clone from GitLab (canonical source)
git clone https://git.ericsweiss.com/eric/weisssrv.git
cd weisssrv

# Or clone from GitHub (read-only mirror)
# git clone https://github.com/ericsweiss/weisssrv.git

# Install Ansible collections
task ansible:install-collections

# Sign in to 1Password
eval $(op signin)

# Test connectivity
task ansible:ping
```

### One-time pre-deploy: pin SSH host keys

Both cert distribution (via dns-01) and Home Assistant config deploys
use `StrictHostKeyChecking=yes` against pinned host keys in inventory.
The entries are committed empty; each role fails loudly with a
remediation message until populated. The two workflows are independent
and have separate capture + validate commands.

#### Cert distribution host-key bootstrap

Targets: dns-02, smtp-relay, gitlab, pve-nas-01, k3s-agt-nas-01, plex,
home. (`task certs:show-host-keys` enumerates from
`cert_distribution_targets` in `host_vars/dns-01.yml`, so its output
is the authoritative list — keep this paragraph aligned with the
inventory if you add or remove targets.) Note that the `home` target
uses SSH port 22222; the helper handles the per-target `ssh_port` field
automatically.

1. Capture host keys via the helper task:

   ```bash
   task certs:show-host-keys
   ```

2. Paste each algorithm-and-key value (without the leading hostname/IP)
   into the matching entry under `cert_distribution_targets[*].host_key`
   in `host_vars/dns-01.yml`. Commit.

3. Validate the inventory parses + the role sees the new keys with a
   dry-run:

   ```bash
   task dns:deploy -- --check
   ```

4. Then run `task dns:deploy` for real.

Same workflow rotates a key after a target rebuild.

#### Home Assistant host-key bootstrap

HAOS runs sshd on port 22222 (SSH add-on), not the standard 22.

1. Capture the HAOS ed25519 host key:

   ```bash
   ssh-keyscan -t ed25519 -p 22222 192.168.0.154
   ```

2. Paste the value (without the leading hostname/IP) into
   `home_assistant_host_key` in `host_vars/home.yml`. Commit.

3. Validate with a dry-run of the Home Assistant config deploy
   (`task home-assistant:deploy-config` doesn't forward extra flags
   to ansible-playbook, so dry-run via ansible-playbook directly):

   ```bash
   cd ansible && op run -- ansible-playbook -i inventories/prod \
     -e ansible_become=false playbooks/home-assistant.yml --check
   ```

4. Then run `task home-assistant:deploy-config` for real.

### Common Operations

```bash
task --list                    # List all available tasks

# Validation and linting
task lint                      # Lint everything (Ansible, Terraform, Kubernetes)
task infra:check               # Dry-run base infrastructure deployment
task ansible:test              # Run Molecule unit tests

# Base infrastructure deployment
task infra:deploy              # Deploy all base infrastructure
task infra:verify              # Verify deployment status
task dns:deploy                # Deploy DNS stack
task storage:deploy            # Deploy storage services

# Application deployments
task plex:deploy               # Deploy Plex Media Server (LXC)
task gitlab:deploy             # Deploy GitLab (VM + application)
task home-assistant:deploy-config # Deploy Home Assistant config (ingress is Flux-managed)

# K3s cluster operations (Ansible - cluster infrastructure)
task k3s:deploy                # Deploy/update k3s cluster (Ansible)
task k3s:status                # Show cluster and workload status
task k3s:backup                # Create etcd snapshot

# Flux GitOps (all Kubernetes workloads deploy via git push)
task flux:status               # Concise health summary
task flux:verify               # flux check + get all -A
task flux:reconcile            # Force reconciliation
task flux:rotate-secret -- <app>  # Refresh ExternalSecret + restart consumers
task flux:sync-versions        # Regenerate versions-configmap from all.yml

# Operational tasks (non-deploy)
task downloads:status          # Check downloads namespace status
task downloads:vpn-status      # Verify VPN connection and public IP
task recipes:status            # Check recipes namespace status
task authentik:status          # Check Authentik status

# GitLab operations
task gitlab:status             # Show GitLab and runner status
task gitlab:verify             # Run GitLab smoke tests

# Terraform (Cloudflare DNS)
task terraform:plan            # Plan Cloudflare DNS changes
task terraform:apply           # Apply Cloudflare DNS changes

# Maintenance
task maintenance:check-versions       # Check all services for updates
task maintenance:update-full          # Full base infrastructure update (interactive)
task maintenance:update-k3s-nodes     # Rolling k3s node upgrades
task collect-state                    # Generate cluster state snapshot
```

> **Kubernetes deploys use Flux**: changes to `kubernetes/apps/<component>/` or
> `kubernetes/infrastructure/` are reconciled automatically after `git push`.
> Flux polls git every ~1 minute (a planned webhook will reduce this to seconds).
> `task flux:reconcile` forces a sync.
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
├── terraform/cloudflare/     # Cloudflare DNS management
├── kubernetes/               # Flux-managed cluster state
│   ├── clusters/weisssrv/    # Flux bootstrap + top-level Kustomizations
│   ├── infrastructure/       # Platform — four subdirectories (sources, controllers, configs, observability)
│   │                         #   that form the first four stages of the five-stage Flux Kustomization chain:
│   │                         #     infrastructure-sources       (HelmRepository CRs + versions-configmap)
│   │                         #     infrastructure-controllers   (HelmReleases for ESO, Connect, MetalLB, cert-manager, Traefik, external-dns)
│   │                         #     infrastructure-configs       (ClusterSecretStore, ClusterIssuer, Traefik middlewares, wildcard certs, CoreDNS, DDNS, Connect IngressRoute)
│   │                         #     infrastructure-observability (kube-prometheus-stack, Loki, Alloy, exporters, dashboards)
│   └── apps/                 # Sibling top-level Kustomization (fifth stage; dependsOn infrastructure-observability):
│                             #   authentik, download-clients, recipes, gitlab-runner,
│                             #   gitlab-runner-privileged, gitlab-agent, vm-ingress
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
| proxmox_vm | VM provisioning with cloud-init and autostart |
| proxmox_lxc | LXC container provisioning with autostart |
| proxmox_ha | Proxmox HA rules, resources, and ZFS replication |
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
| resolv_conf | Shared /etc/resolv.conf management |
| zvol_mount | Shared ZFS zvol mounting with UUID-based fstab |
| nic_tuning | NIC/kernel tuning (AQC113 GRO disable, `ip_forward` sysctl drop-in) |
| zfs_exporter | Prometheus ZFS exporter (pool health, scrub status) on the NAS |
| unbound_exporter | Prometheus Unbound exporter on DNS hosts |
| node_exporter_host | Prometheus node_exporter on bare-metal Proxmox hosts (port 9101) |
| alloy_host | Grafana Alloy on non-k8s hosts and k3s VMs for journald → Loki |
| zfs_encryption | Boot-time ZFS pool key fetch from 1Password Connect |
| nfs_tls | NFSv4 over kernel TLS via tlshd (opt-in, `nfs_tls_enabled`) |

## Secrets Management

All secrets stored in 1Password, injected at runtime:

```yaml
# group_vars/all.yml
secrets:
  smtp_gmail_password: "op://Homelab/SMTP Relay Gmail/password"
```

**Never commit secrets to git.**

## DNS Architecture

Split-horizon DNS:
- **Internal** (`*.esweiss.com`): AdGuard Home rewrites
- **External** (`*.ericsweiss.com`): Cloudflare (Terraform)

## K3s Platform

9-node HA cluster (3 servers + 6 agents) with:
- **kube-vip**: API VIP at 192.168.0.161 (tolerates 1 server failure)
- **MetalLB**: LoadBalancer IPs (.100 public, .101 internal)
- **Traefik**: Ingress controller with Let's Encrypt
- **external-dns**: Automatic Cloudflare DNS management
- **cert-manager**: Let's Encrypt certificate automation
- **Authentik**: SSO/OIDC identity provider (auth.esweiss.com)
- **Flux**: Reconciles all Kubernetes manifests from this repo
- **External Secrets Operator**: Syncs k8s Secrets from 1Password (Connect provider, vault `Homelab`)

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
- **Documentation**: [docs/19-k3s-deployment.md](docs/19-k3s-deployment.md)

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

Both services use Authentik SSO for authentication.

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
| [14-post-base-plan](docs/14-post-base-plan.md) | K3s platform roadmap and workload planning |
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
| [26-multi-node-implementation](docs/26-multi-node-implementation.md) | Step-by-step 6-node cluster implementation |
| [27-gitlab-deployment](docs/27-gitlab-deployment.md) | GitLab EE deployment (VM, registry, pages, runners) |
| [28-gitlab-migration](docs/28-gitlab-migration.md) | GitHub to GitLab migration guide |
| [29-flux-operations](docs/29-flux-operations.md) | Flux operator guide (bootstrap, adopt, rotate, add app, troubleshoot) |
| [30-multi-repo-onboarding](docs/30-multi-repo-onboarding.md) | Adding external repos that deploy into this cluster via Flux |
| [31-observability](docs/31-observability.md) | Observability stack (Prometheus, Grafana, Loki, Alloy, exporters, alerting) |
| [32-zfs-encryption](docs/32-zfs-encryption.md) | ZFS native encryption with passphrase-from-Connect boot-time unlock |

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
