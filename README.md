# weisssrv

> **Note**: The canonical source for this repository is [git.ericsweiss.com](https://git.ericsweiss.com/eric/weisssrv).
> GitHub is a read-only mirror updated automatically via GitLab push mirroring.
> Please submit issues and merge requests on the GitLab instance.

Homelab Infrastructure as Code — a complete GitOps repository for a
Proxmox-based homelab using Ansible, Terraform, Kubernetes, and Flux.

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
- **GitOps**: Flux reconciles all Kubernetes workloads from this repo; External
  Secrets Operator syncs every k8s Secret from 1Password (Connect provider)

## Architecture

```
Internet
    |
[Router 10.0.10.1]
    |
[10.0.10.0/24] -- Core LAN
    |
    +-- Proxmox hosts        6-node cluster "weisssrv" (1 NAS + 5 compute)
    |
    +-- Infrastructure LXC/VMs   DNS x2, SMTP relay, Plex, GitLab,
    |                            Home Assistant, Windows, Nextcloud,
    |                            Immich + Immich ML
    |
    +-- K3s cluster          9 nodes (3 servers + etcd, 6 agents)
    |
    +-- MetalLB / kube-vip VIPs  public + internal ingress, API, wg-easy
```

Canonical host/node/VIP topology reference: [docs/01-overview.md](docs/01-overview.md).

## Quick Start

### Prerequisites

- macOS/Linux workstation
- [Task](https://taskfile.dev/) runner
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`)
- Python 3 with `pip` (for the lint/test tooling in `requirements.txt`)
- Ansible and Terraform
- **A `weisssrv-lib` checkout beside this one** (`../weisssrv-lib`, or point
  `$WEISSSRV_LIB_PATH` at it). `task lint` runs the vendored-copy gate, which
  compares this repo's vendored scripts against the library and **never skips**
  — without a checkout the gate fails rather than passing quietly. This is
  distinct from `$WEISSSRV_COLLECTION_PATH`, which only makes `task
  ansible:lint` use an untagged local collection and does not satisfy the gate.

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

# Install Ansible collections, including weisssrv.infra (every role) at the tag
# pinned in ansible/requirements.yml. Needs read access to the library repo.
task ansible:install-collections

# Sign in to 1Password
eval $(op signin)

# Test connectivity
task ansible:ping
```

### One-time pre-deploy: pin SSH host keys

Cert distribution and Home Assistant config deploys use
`StrictHostKeyChecking=yes` against host keys pinned in inventory
(`acme_certs_distribution_targets[*].host_key` in `host_vars/dns-01.yml`, captured
via `task certs:show-host-keys`; the HAOS key in `group_vars/all.yml` for port
22222) — entries ship empty and each role fails loudly until populated.
Bootstrap and rotation procedures: [docs/09-certs.md](docs/09-certs.md) and
[docs/24-home-assistant-deployment.md](docs/24-home-assistant-deployment.md).

### Common Operations

`task --list` is the authoritative task reference — the essentials:

```bash
task --list                    # List all available tasks

# Validation and linting
task lint                      # Lint everything (Ansible, Terraform, Kubernetes, scripts)
task infra:check               # Dry-run base infrastructure deployment
task ansible:test-integration  # Run the multi-role integration scenarios

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
│   ├── playbooks/            # Deployment playbooks (roles addressed as weisssrv.infra.<role>)
│   ├── integration-tests/    # Multi-role molecule scenarios
│   └── requirements.yml      # Galaxy pins, including the weisssrv.infra collection
├── terraform/
│   ├── cloudflare/           # Cloudflare DNS management
│   ├── tailscale/            # Tailnet ACL policy-as-code (SSH rules, subnet-route auto-approval)
│   ├── authentik/            # Authentik SSO state as code (applications, providers, groups)
│   └── unifi/                # UniFi network state as code (VLANs, firewall zones/policies, WLANs, port forwards)
├── kubernetes/               # Flux-managed cluster state
│   ├── clusters/weisssrv/    # Flux bootstrap + top-level Kustomizations
│   ├── infrastructure/       # Platform — five subdirectories (sources, crds, controllers, configs, observability)
│   │                         #   reconciled in dependsOn order; configs fans out to observability and apps:
│   │                         #     infrastructure-sources       (HelmRepository CRs + versions-configmap + cluster-config)
│   │                         #     infrastructure-crds          (prometheus-operator CRDs, wait:true — fresh-bootstrap ordering)
│   │                         #     infrastructure-controllers   (platform HelmReleases — see the dir for the current set)
│   │                         #     infrastructure-configs       (CRs requiring controller CRDs — see the dir for the current set)
│   │                         #     infrastructure-observability (kube-prometheus-stack, Loki, Alloy, exporters, dashboards)
│   │                         #     infrastructure-metrics-server (own stage off the chain: dependsOn sources only — docs/33)
│   ├── components/           # Reusable Kustomize components — see kubernetes/components/README.md
│   └── apps/                 # Sibling top-level Kustomization (dependsOn infrastructure-configs,
│                             #   parallel to observability so its health can't freeze app reconciliation):
│                             #   one dir per app — see kubernetes/apps/ for the current set
├── scripts/                  # Utility scripts (version checker, versions-configmap generator, etc.)
├── docs/                     # Documentation
└── Taskfile.yml              # Task runner commands (including flux:*)
```

### Related repositories

This repo is one instance of a four-repo family:

```
eric/weisssrv-lib     CI job templates · the weisssrv.infra Ansible collection
   │                  (all 40 roles) · the weisssrv-lib-cli template renderer
   │                  (`weisssrv-new-project`) · Terraform modules · lint profiles
   │
   ├── eric/weisssrv                    this repo — the running cluster
   ├── eric/weisssrv-cluster-template   copier template that generates a NEW
   │                                    cluster repo shaped like this one
   └── eric/weisssrv-app-template       copier template for tenant repos that
                                        deploy INTO this cluster (docs/30)
```

Every arrow is a pinned dependency: this repo pins the library tag in
`variables.WEISSSRV_LIB_REF` (`.gitlab-ci.yml`, enforced by
`scripts/check-lib-pins.py`) and in `ansible/requirements.yml`. Change library
behaviour in the library, tag it, then bump the pins — see
[docs/13-ci-cd.md](docs/13-ci-cd.md) § Shared CI library and `CLAUDE.md`
§ Repo family.

## Ansible Roles

All 40 roles — base, qol, k3s, the Proxmox roles, storage/DNS/mail/certs, the
per-app roles (gitlab, plex, home_assistant, immich, immich_ml, nextcloud), the
exporter and shared-helper roles — ship in the **`weisssrv.infra` collection** in
`eric/weisssrv-lib`, pinned in
[ansible/requirements.yml](ansible/requirements.yml). This repo holds the site
data those roles consume: inventory, playbooks, and the Taskfile/CI wiring.

The collection's own README documents every role, its variables and defaults,
and `MIGRATING.md` there is the variable map. Layout and conventions for the
Ansible tree here: [ansible/README.md](ansible/README.md).

## Secrets Management

All secrets stay in 1Password and are injected at runtime — never in the
inventory. Host-side tooling gets them from the invoking `Taskfile.yml` task's
own `env:` block, mirrored by the matching CI job's `variables:`, both resolved
by `op run --`:

```yaml
# Taskfile.yml — the task that runs the playbook owns the reference
  infra:deploy:
    env:
      SMTP_GMAIL_PASSWORD: op://Homelab/SMTP Relay Gmail/password
```

```yaml
# .gitlab-ci.yml — the matching deploy job repeats it verbatim
deploy-ansible-mail:
  variables:
    SMTP_GMAIL_PASSWORD: "op://Homelab/SMTP Relay Gmail/password"
```

The inventory reads them back with `lookup('ansible.builtin.env', ...)`; there is
no `secrets:` dict in `group_vars/all.yml`. `task secrets:show` prints the live
set of references.

In-cluster Kubernetes Secrets are produced by External Secrets Operator from
`ExternalSecret` manifests. The `remoteRef` key/property format is specified in
[docs/29-flux-operations.md](docs/29-flux-operations.md) ("1Password Connect
Provider Reference Format").

[docs/15-credential-rotation.md](docs/15-credential-rotation.md) § Secrets model
is canonical for all three consumers (Ansible/Terraform + Task, ESO, CI) and for
the required-item inventory.

**Never commit secrets to git.**

## DNS Architecture

Split-horizon DNS:
- **Internal** (`*.esweiss.com`): AdGuard Home rewrites
- **External** (`*.ericsweiss.com`): Cloudflare (Terraform)

## K3s Platform

9-node HA cluster (3 servers + 6 agents) with:
- **kube-vip**: API VIP at 10.0.10.161 (the 3-node etcd quorum tolerates 1 server failure)
- **MetalLB**: LoadBalancer IPs (.100 public, .101 internal, .99 wg-easy endpoint)
- **Traefik**: Ingress controller (TLS served from cert-manager wildcard certs)
- **external-dns**: Automatic Cloudflare DNS management
- **cert-manager**: Let's Encrypt certificate automation
- **Authentik**: SSO/OIDC identity provider — auth.esweiss.com (internal) /
  auth.ericsweiss.com (external, and always the OIDC issuer host)
- **Flux**: Reconciles all Kubernetes manifests from this repo
- **External Secrets Operator**: Syncs k8s Secrets from 1Password (Connect provider, vault `Homelab`)
- **Autoscaling + node ops**: VPA, Reloader, kured (coordinated reboots) — full
  controller set in `kubernetes/infrastructure/controllers/kustomization.yaml`

See [docs/19-k3s-deployment.md](docs/19-k3s-deployment.md) for deployment guide.
See also [docs/29-flux-operations.md](docs/29-flux-operations.md) (operator guide)
and [docs/30-multi-repo-onboarding.md](docs/30-multi-repo-onboarding.md)
(adding external repos that deploy into this cluster).

## Applications

### Authentik SSO

Identity provider for Single Sign-On across all applications:

- **URL**: auth.esweiss.com (internal) / auth.ericsweiss.com (external — always
  the OIDC issuer host, even for internal-only apps)
- **Features**:
  - OIDC/OAuth2 provider for every user-facing app (see the per-app docs)
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
| Bar Assistant | Cocktail recipe management | bar.ericsweiss.com (bar.esweiss.com redirects to it) |

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
- **Stack**: Docker Compose (nextcloud-apache + PostgreSQL + Redis + cron +
  exporter) on VM .156, all state on ZFS zvol passthrough disks (no NFS),
  host-nginx TLS
- **Authentication**: Authentik OIDC SSO-only
- **Documentation**: [docs/35-nextcloud.md](docs/35-nextcloud.md)

### Immich

Self-hosted photo and video management on a NAS-pinned VM:

- **URLs**: photos.esweiss.com (internal), photos.ericsweiss.com (external)
- **Stack**: docker-compose (immich-server + CPU ML + release-pinned
  Postgres/vectorchord + Valkey) on VM .157, encrypted zvols (photo library on
  `tank/immich-data`), host-nginx TLS, nightly pg_dump
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
- **Stack**: Prometheus + Grafana + Loki + Alloy, plus exporters (Proxmox, ZFS,
  AdGuard, Unbound, Blackbox, Plex, Exportarr)
- **Authentication**: Authentik OIDC
- **Features**: community + custom dashboards via the `grafana_dashboard`
  ConfigMap sidecar, Loki log datasource, Discord/email alerting
- **Documentation**: [docs/31-observability.md](docs/31-observability.md)

### Hermes Agent

NousResearch autonomous AI agent platform with a web dashboard:

- **URLs**: agent.esweiss.com (internal), agent.ericsweiss.com (external)
- **Authentication**: the dashboard's own Authentik-OIDC login on both hostnames
  (`hermes-users` group gate; an auth provider is mandatory on its 0.0.0.0 bind),
  plus a Traefik-only NetworkPolicy. Authentik objects in `terraform/authentik`
  (docs/40)
- **Workload**: one pod, three containers (gateway supervisor + FastAPI dashboard
  + camofox anti-detection browser sidecar) off a self-built image (upstream
  ships none — built by the `build-hermes-agent` CI job); NFS `/opt/data` state
  on encrypted `ssd/appdata`
- **Memory backend**: a cluster-internal Hindsight deployment (no ingress) serves
  Hermes' long-term memory — see [docs/37-hermes.md](docs/37-hermes.md)
  (§Memory backend)
- **Documentation**: [docs/37-hermes.md](docs/37-hermes.md)

### Windows 11 VM

On-demand Windows 11 desktop (OVMF/TPM/q35 shell provisioned via `proxmox_vm`):

- **Access**: RDP to VM .155 (NAS-pinned). Its disks are on the encrypted `ssd`
  pool, so it runs `onboot=0` and is started after unlock by
  `pve-start-encrypted-guests` (last in the encrypted-guest cohort)
- **Documentation**: [docs/39-windows-vm.md](docs/39-windows-vm.md)

### Homarr

Homelab dashboard/launcher for every service in the cluster:

- **URLs**: dashboard.esweiss.com (internal), dashboard.ericsweiss.com (external)
- **Authentication**: Authentik OIDC, SSO-only (`homarr-admins` group gate, admin
  via the OIDC `groups` claim; no standing local admin — DR via docs/41 §SSO);
  Authentik objects in `terraform/authentik` (docs/40)
- **Workload**: raw k3s manifests (`kubernetes/apps/homarr/`), NFS-backed SQLite
  state on encrypted `ssd/appdata`
- **Integrations**: direct in-cluster/LAN URLs (bypassing the SSO perimeter) to
  the *arr stack, qBittorrent/NZBGet, AdGuard, Proxmox, Plex, Home Assistant,
  Nextcloud, and Immich
- **Documentation**: [docs/41-homarr.md](docs/41-homarr.md)

### Uptime Kuma

Endpoint monitoring and the public status page:

- **URLs**: status.ericsweiss.com (external — **public status page only**),
  status.esweiss.com (internal — status page plus the admin UI)
- **Authentication**: Authentik forward-auth (`status-admins` group) on the
  admin surface only; the status-page paths are unauthenticated on both
  hostnames and the external hostname has no admin router at all (404). Kuma has
  no SSO of its own; its single local account sits underneath the outpost
- **Workload**: raw k3s manifests (`kubernetes/apps/uptime-kuma/`), the upstream
  `-rootless` image under PSA `restricted`, NFS-backed SQLite state on encrypted
  `ssd/appdata`
- **Monitors**: the pod's NetworkPolicy egress is the monitor inventory —
  public :443, Traefik + both MetalLB VIPs, the two resolvers, the SMTP relay,
  the six Proxmox APIs and the k3s API VIP. ICMP monitors are unsupported
- **Documentation**: [docs/45-uptime-kuma.md](docs/45-uptime-kuma.md)

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
| [46-unifi-network](docs/46-unifi-network.md) | UniFi gateway/switch/AP tier: VLANs, zone-based firewall, WLANs, port map, bench pre-provisioning + cutover runbook |

### Platform (k3s, Flux, observability, SSO, GPU)

| Document | Description |
|----------|-------------|
| [19-k3s-deployment](docs/19-k3s-deployment.md) | K3s cluster deployment (complete workflow) |
| [29-flux-operations](docs/29-flux-operations.md) | Flux operator guide (bootstrap, adopt, rotate, add app, troubleshoot) |
| [30-multi-repo-onboarding](docs/30-multi-repo-onboarding.md) | Adding external repos that deploy into this cluster via Flux |
| [31-observability](docs/31-observability.md) | Observability stack (Prometheus, Grafana, Loki, Alloy, exporters, alerting) |
| [32-zfs-encryption](docs/32-zfs-encryption.md) | ZFS native encryption with passphrase-from-Connect boot-time unlock |
| [33-autoscaling](docs/33-autoscaling.md) | VPA tiers, CoreDNS HPA pin, hand-tuned baselines, Proxmox-level guidance |
| [40-authentik-terraform](docs/40-authentik-terraform.md) | Authentik SSO as code (terraform/authentik day-2 ops: drift, rotation, DR) |
| [43-gpu-passthrough](docs/43-gpu-passthrough.md) | GPU passthrough (pve-prec-01 1660 Ti → Hindsight): VFIO host prep, driver/toolkit, device plugin, DCGM, RAM right-sizing, operator runbook |

### Applications

| Document | Description |
|----------|-------------|
| [20-plex-deployment](docs/20-plex-deployment.md) | Plex Media Server deployment |
| [21-download-clients-deployment](docs/21-download-clients-deployment.md) | Download clients and media stack |
| [22-recipes-deployment](docs/22-recipes-deployment.md) | Recipe management (Mealie, Bar Assistant) |
| [23-recipes-sso-setup](docs/23-recipes-sso-setup.md) | Recipes SSO and OpenAI configuration (the manual SSO walkthrough is **superseded** by terraform/authentik — docs/40) |
| [24-home-assistant-deployment](docs/24-home-assistant-deployment.md) | Home Assistant OS with Authentik SSO |
| [27-gitlab-deployment](docs/27-gitlab-deployment.md) | GitLab EE deployment (VM, registry, pages, runners) |
| [35-nextcloud](docs/35-nextcloud.md) | Nextcloud VM (Docker Compose, zvol storage, host-nginx TLS, Authentik OIDC SSO, backups, observability, runbooks) |
| [36-immich](docs/36-immich.md) | Immich photo management (NAS-pinned VM, docker-compose, encrypted zvols, Authentik OIDC, backups) |
| [37-hermes](docs/37-hermes.md) | Hermes Agent (NousResearch AI agent + dashboard): self-built image, three-container pod, dashboard Authentik-OIDC SSO |
| [38-wireguard-vpn](docs/38-wireguard-vpn.md) | wg-easy internet-exit VPN (two-layer no-LAN egress fence, client onboarding, restore) |
| [39-windows-vm](docs/39-windows-vm.md) | Windows 11 VM (OVMF/TPM/q35 shell via proxmox_vm, interactive install, RDP) |
| [41-homarr](docs/41-homarr.md) | Homarr dashboard (raw manifests, Authentik OIDC, NFS SQLite, direct-URL integrations) |
| [45-uptime-kuma](docs/45-uptime-kuma.md) | Uptime Kuma (endpoint monitoring + public status page; public/admin routing split, forward-auth, NFS SQLite) |

### Operations and Planning

| Document | Description |
|----------|-------------|
| [12-runbooks](docs/12-runbooks.md) | Operational procedures |
| [13-ci-cd](docs/13-ci-cd.md) | CI/CD pipelines (GitLab CI) |
| [15-credential-rotation](docs/15-credential-rotation.md) | Credential rotation procedures |
| [16-next-steps](docs/16-next-steps.md) | TODO and feature roadmap |
| [17-disaster-recovery](docs/17-disaster-recovery.md) | Disaster recovery and backup procedures |
| [18-bootstrap-new-systems](docs/18-bootstrap-new-systems.md) | Bootstrapping new LXC containers and VMs |
| [25-multi-node-expansion](docs/25-multi-node-expansion.md) | Multi-node expansion and Proxmox HA — the current HA-operations reference (docs/26 defers to it) |
| [34-bond-mac-flapping](docs/34-bond-mac-flapping.md) | Opt-node network faults: the active-backup bond `all_slaves_active` MAC-flap black-hole **and** the e1000e TX Hardware Unit Hang — diagnosis, recovery, nic_tuning fixes |
| [42-offsite-backup](docs/42-offsite-backup.md) | Offsite backup (restic → Backblaze B2, client-side encrypted) + encrypted swap |
| [44-storage-bootstrap](docs/44-storage-bootstrap.md) | Storage bootstrap: creating the ZFS pools and datasets a rebuilt NAS needs before restore |

### Historical (completed / superseded — read-only)

| Document | Description |
|----------|-------------|
| [14-post-base-plan](docs/14-post-base-plan.md) | K3s platform roadmap and workload planning (superseded — historical record) |
| [26-multi-node-implementation](docs/26-multi-node-implementation.md) | Step-by-step 6-node cluster implementation (completed — retained for rebuild reference) |
| [28-gitlab-migration](docs/28-gitlab-migration.md) | GitHub to GitLab migration guide |

### Component docs (outside the numbered set)

| Document | Description |
|----------|-------------|
| [ansible/README](ansible/README.md) | Ansible layout, code conventions, where the roles live |
| [ansible/TESTING](ansible/TESTING.md) | Integration-test scenarios: coverage, how to run, how to add one |
| [kubernetes/README](kubernetes/README.md) | Flux tree layout, reconcile order, namespace ownership |
| [kubernetes/clusters/weisssrv/tenants/README](kubernetes/clusters/weisssrv/tenants/README.md) | Onboarding a tenant repo's Flux Kustomization (walkthrough: docs/30) |
| `kubernetes/apps/<app>/README.md` (one per app that has notes — see `kubernetes/apps/`) | Per-app notes; [authentik](kubernetes/apps/authentik/README.md) is the **canonical** Authentik doc (its Terraform layer is docs/40) |
| [terraform/cloudflare/README](terraform/cloudflare/README.md), [terraform/tailscale/README](terraform/tailscale/README.md), [terraform/authentik/README](terraform/authentik/README.md), [terraform/unifi/README](terraform/unifi/README.md) | Per-module ownership, plan/apply rules, import + DR recipes |
| [kubernetes/components/README](kubernetes/components/README.md) | The reusable Kustomize components (netpol-baseline, the three netpol-egress-*, gitlab-runner-common) and when to take one rather than an inline policy |
| [scripts/README](scripts/README.md) | Every script, grouped by purpose, with its origin (local / dual-maintained / vendored) |
| [docker/hermes-agent/README](docker/hermes-agent/README.md), [docker/camofox-browser/README](docker/camofox-browser/README.md) | The two app images this repo builds |
| [.claude/skills/weisssrv-development/SKILL](.claude/skills/weisssrv-development/SKILL.md) | The agent operating map (workflow invariants, gates, decision tree) |

### Documentation conventions

- **Numbered docs (`docs/NN-topic.md`)** are for a subsystem or an application
  someone has to operate. Numbers are assigned in order of creation and are
  **not** re-used; the grouping above (Getting Started / Infrastructure /
  Platform / Applications / Operations / Historical) is the taxonomy, the number
  is just an identifier. Do not repeat the number in the document's `#` title —
  that turns a renumber into a content edit.
- **An app README** (`kubernetes/apps/<app>/README.md`) covers what lives in that
  folder. If a numbered doc owns the subject, say so in the README's first
  paragraph and link it, the way `kubernetes/apps/download-clients/README.md`
  does. Role READMEs live with the roles, in the `weisssrv.infra` collection.
- **Declare the source of truth** in the first paragraph of any doc whose subject
  is also described elsewhere, and link rather than restate. Enumerations that
  must stay exact (apps, exports, namespaces, version pins) should name the file
  that generates them.
- **No table of contents.** The heading structure is the navigation; a hand-kept
  TOC only adds a second thing to drift.
- **Cross-links go in a `## Related documentation` section at the foot** of the
  doc — one name for it everywhere, so it is greppable. It holds **internal**
  links only; third-party reading material goes in a separate
  `## External references` list below it, so grepping the first name returns a
  usable map of the doc set.
- **Wrap prose at about 100 columns.** Tables, code blocks, and URLs are exempt.
- **Commands are repo-root-relative** unless the doc says otherwise — write
  `ansible-playbook -i ansible/inventories/prod ansible/playbooks/…`, never a
  `cd ansible/`-relative form, since that is what the `task` wrappers and CI
  present.
- **Superseded docs keep their number** and gain a status banner plus a row in
  the Historical table above; they are never silently deleted, because older MRs
  and runbooks link to them. `docs/14-post-base-plan.md` is the exemplar — its
  `> **Status: superseded.**` blockquote sits immediately under the H1.
  A doc whose *procedure* is superseded but whose *reference data* is still
  current stays in its topical group instead, carries the banner at the point
  the superseded procedure begins rather than at the top, and is annotated in
  its index row — `docs/23-recipes-sso-setup.md` is that variant.
- Every relative `.md` link is CI-checked (`docs-link-check` over every tracked
  Markdown file), so a rename that breaks a cross-link fails the pipeline.

**Agent guidance**: coding agents should start from the
[`weisssrv-development` skill](.claude/skills/weisssrv-development/SKILL.md) — it
maps the repo workflow, pre-MR gates, and change-type decision tree onto the docs
above. `CLAUDE.md` and `AGENTS.md` defer to it, as do the Cursor rules.

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
