# weisssrv

Homelab Infrastructure as Code - Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and Kubernetes.

## Overview

Multi-node Proxmox homelab with:

- **Proxmox Cluster**: 2 active hosts (NAS + compute), 3 planned expansion hosts
- **Storage**: ZFS pools (tank/ssd/nvme/archive) with NFS and Samba
- **DNS**: Dual AdGuard Home + Unbound (DoT) for ad blocking and secure resolution
- **Mail**: SMTP relay via Gmail for system notifications
- **Certificates**: Let's Encrypt via acme.sh with automated distribution
- **VPN**: Tailscale for secure remote access
- **Firewall**: Proxmox firewall with IPSets and security groups
- **K3s Cluster**: 3-node initial deployment (codified), expandable to 5-node HA

## Architecture

```
Internet
    |
[Router 192.168.0.1]
    |
[192.168.0.0/24] -- Core LAN
    |
    +-- Proxmox Hosts
    |   +-- pve-nas-01    (.102) - NAS + Storage
    |   +-- pve-opt-03    (.106) - Compute
    |
    +-- Infrastructure LXC
    |   +-- dns-01        (.150) - Primary DNS
    |   +-- dns-02        (.160) - Secondary DNS
    |   +-- smtp-relay    (.151) - Mail relay
    |   +-- plex          (.152) - Plex Media Server
    |
    +-- K3s Cluster VMs
        +-- k3s-srv-nas-01 (.202) - Server + etcd
        +-- k3s-agt-nas-01 (.207) - Agent (NAS workloads)
        +-- k3s-agt-opt-03 (.206) - Agent (ingress)
```

## Quick Start

### Prerequisites

- macOS/Linux workstation
- [Task](https://taskfile.dev/) runner
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`)
- Ansible and Terraform

### Setup

```bash
git clone https://github.com/ericsweiss/weisssrv.git
cd weisssrv

# Install Ansible collections
task ansible:install-collections

# Sign in to 1Password
eval $(op signin)

# Test connectivity
task ansible:ping
```

### Common Operations

```bash
task --list                    # List all tasks

task lint                      # Lint everything
task deploy:check              # Dry-run deployment
task deploy:all                # Deploy all infrastructure
task deploy:verify             # Verify deployment

task deploy:dns                # Deploy DNS stack
task deploy:storage            # Deploy storage services
task deploy:plex               # Deploy Plex Media Server

task terraform:plan            # Plan Cloudflare DNS changes
task terraform:apply           # Apply Cloudflare DNS changes

task maintenance:update-full   # Full system update
task collect-state             # Generate cluster state snapshot
```

## Repository Structure

```
weisssrv/
├── ansible/
│   ├── inventories/prod/     # Production inventory
│   │   ├── hosts.yml         # Host definitions
│   │   └── group_vars/       # Group variables
│   ├── roles/                # 15 Ansible roles
│   └── playbooks/            # Deployment playbooks
├── terraform/cloudflare/     # Cloudflare DNS management
├── kubernetes/               # K3s manifests (Flux-ready)
├── docs/                     # Documentation (20 files)
└── Taskfile.yml              # Task runner commands
```

## Ansible Roles

| Role | Purpose |
|------|---------|
| base | Packages, SSH hardening, users, timezone |
| qol | zsh + Oh My Zsh, neovim, fzf |
| postfix_null_client | Local mail relay |
| tailscale | VPN setup |
| proxmox_firewall | IPSets and security groups |
| proxmox_vm | VM provisioning with cloud-init |
| proxmox_lxc | LXC container provisioning |
| nas_storage | ZFS, NFS, Samba, MergerFS |
| unbound | DoT recursive resolver |
| adguard_home | DNS filtering (non-root) |
| acme_certs | Let's Encrypt certificates |
| smtp_relay | Gmail SMTP relay |
| adguard_sync | DNS sync (dns-01 -> dns-02) |
| k3s | K3s cluster deployment |
| plex | Plex Media Server with GPU transcoding |

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

Initial 3-node cluster with:
- **kube-vip**: API VIP at 192.168.0.161
- **MetalLB**: LoadBalancer IPs (.100 public, .101 internal)
- **Traefik**: Ingress controller
- **external-dns**: Automatic Cloudflare DNS
- **Authentik**: SSO/OIDC identity provider (auth.esweiss.com)

See [docs/19-k3s-deployment.md](docs/19-k3s-deployment.md) for deployment guide.

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
| [13-ci-cd](docs/13-ci-cd.md) | CI/CD pipelines and GitHub Actions |
| [14-post-base-plan](docs/14-post-base-plan.md) | K3s platform roadmap and workload planning |
| [15-credential-rotation](docs/15-credential-rotation.md) | Credential rotation procedures |
| [16-next-steps](docs/16-next-steps.md) | TODO and feature roadmap |
| [17-disaster-recovery](docs/17-disaster-recovery.md) | Disaster recovery and backup procedures |
| [18-bootstrap-new-systems](docs/18-bootstrap-new-systems.md) | Bootstrapping new LXC containers and VMs |
| [19-k3s-deployment](docs/19-k3s-deployment.md) | K3s cluster deployment (complete workflow) |
| [20-plex-deployment](docs/20-plex-deployment.md) | Plex Media Server deployment |

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
