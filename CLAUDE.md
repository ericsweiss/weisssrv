# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weisssrv** - Homelab Infrastructure as Code

Complete GitOps repository for a Proxmox-based homelab using Ansible, Terraform, and (future) Kubernetes.

## Repository Structure

```
weisssrv/
├── ansible/                 # Configuration management
│   ├── inventories/prod/    # Production inventory + vars
│   ├── roles/               # 15 roles for all services
│   └── playbooks/           # Deployment playbooks
├── terraform/cloudflare/    # External DNS management
├── kubernetes/              # Future k3s manifests (Flux)
├── docs/                    # Comprehensive documentation (20 files)
├── scripts/                 # Utility scripts
└── .github/workflows/       # CI/CD automation
```

## Architecture

### Current Infrastructure (Base Parity)

- **2 Proxmox Hosts**: pve-nas-01 (192.168.0.102), pve-opt-03 (192.168.0.106)
- **NAS Storage**: ZFS (tank/ssd/nvme/archive pools), NFS, Samba
- **DNS**: 2x AdGuard Home + Unbound (DoT) - 192.168.0.150/160
- **SMTP**: Relay via Gmail - 192.168.0.151
- **Certs**: acme.sh with Cloudflare DNS-01
- **VPN**: Tailscale on all hosts
- **Firewall**: Proxmox firewall with IP Sets + Security Groups

### K3s Platform (Ready to Deploy)

**Initial 3-node cluster** (fully codified):
- **k3s-srv-nas-01** (192.168.0.202) - Server + etcd on pve-nas-01
- **k3s-agt-nas-01** (192.168.0.207) - Agent on pve-nas-01 (NAS workloads)
- **k3s-agt-opt-03** (192.168.0.206) - Agent on pve-opt-03 (ingress/general)

**Deployment Model** (Two-phase approach):
1. **Ansible** (`task k3s:deploy`): VMs, k3s, kube-vip (API VIP .161)
2. **Task/Helm** (`task k3s:deploy-workloads`): MetalLB, Traefik, cert-manager, external-dns, DDNS, IngressRoutes

All tasks are idempotent - safe to re-run. See `docs/19-k3s-deployment.md` for complete workflow.

**Features**:
- kube-vip (API VIP .161), MetalLB (VIPs .100/.101)
- Traefik ingress, external-dns (Cloudflare)
- Ready for HA expansion (add 4 more servers for 5-node HA)

**Applications**:
- Authentik SSO (auth.esweiss.com) - Identity provider for SSO/OIDC/SAML
- Plex Media Server (plex.esweiss.com) - LXC container with Traefik ingress

**Future**:
- GitOps via Flux
- Apps: Media stack (*arr), Immich, Nextcloud

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

# Deployments (base infrastructure)
task deploy:check                 # Dry-run (--check mode)
task deploy:all                   # Full base infrastructure deployment (excludes k3s)
task deploy:verify                # Post-deployment verification
task deploy:base                  # Base packages + SSH only
task deploy:dns                   # DNS stack
task deploy:storage               # NAS services
task deploy:plex                  # Plex Media Server (LXC + Plex install)
task deploy:plex-check            # Plex dry-run

# K3s cluster (Ansible - separate lifecycle)
task k3s:provision-vms            # Provision k3s VMs on Proxmox
task k3s:deploy                   # Deploy k3s cluster (idempotent, safe to re-run)
task k3s:kubeconfig               # Fetch kubeconfig from cluster

# K3s workloads (Helm/kubectl - after cluster is running)
task k3s:deploy-workloads         # Deploy ALL platform workloads in order
task k3s:deploy-metallb           # Deploy MetalLB load balancer
task k3s:deploy-traefik           # Deploy Traefik ingress controller
task k3s:deploy-cert-manager      # Deploy cert-manager with Let's Encrypt
task k3s:deploy-external-dns      # Deploy external-dns for Cloudflare
task k3s:deploy-ddns              # Deploy DDNS CronJob
task k3s:deploy-ingress-routes    # Deploy Traefik IngressRoutes
task k3s:deploy-authentik         # Deploy Authentik SSO identity provider
task k3s:status                   # Show cluster and workload status

# Maintenance
task maintenance:update-full      # Full update (OS + apps, interactive)
task maintenance:update-full-auto # Full update (OS + apps, auto-reboot)
task maintenance:update-packages  # OS packages only
task maintenance:update-applications # Applications only

# Terraform
task terraform:init               # Initialize Terraform
task terraform:plan               # Plan changes
task terraform:apply              # Apply changes
task terraform:validate           # Validate syntax

# Linting
task lint                         # Lint everything

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

```bash
cd terraform/cloudflare
terraform init
terraform plan
terraform apply
```

## Secrets Management (1Password)

All secrets reference 1Password items and are resolved at runtime via Task wrapper (`op run --`):

```yaml
# Format in group_vars/all.yml
secrets:
  smtp_gmail_password: "op://Homelab/SMTP Relay Gmail/password"
```

**NEVER commit secrets to git**. All sensitive values use 1Password references.

### Required 1Password Items

In vault "Homelab":
- **Cloudflare DNS Token** - API token (credential) + account ID (username)
- **SMTP Relay Gmail** - username + app password
- **SMTP Relay Auth** - username + password (for null client auth to smtp-relay)
- **Email Config** - root_alias (ericsweiss1@gmail.com)
- **AdGuard Home** - admin password
- **Tailscale Auth Key** - auth key
- **SSH Key** - public + private key
- **Samba NAS User** - nas user password
- **DNS-01 SSH Key** - private + public key (for cert distribution)
- **K3s Cluster Token** - cluster join token (credential)
- **Authentik Secrets** - secret-key, postgresql-password, postgresql-admin-password

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
- Proxmox hosts: .102-.106
- DNS: .150, .160
- SMTP: .151
- Services: .152-.155

### K3s Cluster
- API VIP: .161
- Server: .202
- Agents: .206, .207
- MetalLB: .100 (public), .101 (internal)

### Firewall IP Sets
- `admin_lan`: 192.168.0.0/24
- `admin_ts`: 100.64.0.0/10 (Tailscale)
- `core-cluster`: All infra nodes (.102-.106, .150-.155, .160, .202-.207)
- `k3s_nodes`: k3s VMs + API VIP
- `pve_hosts`: Proxmox hosts only
- `nfs_clients`: Hosts allowed NFS
- `smb_clients`: Entire LAN

## Ansible Roles

1. **base** - Packages, SSH hardening, users, timezone, DNS configuration
2. **qol** - zsh + Oh My Zsh (20 plugins), neovim + Vundle, fzf, ripgrep
3. **postfix_null_client** - Local mail relay to smtp-relay
4. **tailscale** - VPN setup (manual `tailscale up` required)
5. **proxmox_firewall** - IPSets, security groups, cluster.fw
6. **proxmox_vm** - VM provisioning with cloud-init and autostart configuration
7. **proxmox_lxc** - LXC container provisioning with autostart configuration
8. **nas_storage** - ZFS properties, NFS exports, Samba, mergerfs, media-mover, SMART
9. **unbound** - DoT recursive resolver (port 5335)
10. **adguard_home** - DNS filtering + DoT (port 53), running as non-root
11. **acme_certs** - Let's Encrypt via DNS-01, cert distribution via SSH
12. **smtp_relay** - Postfix relay to Gmail via SASL + incoming auth
13. **adguard_sync** - Sync dns-01 → dns-02 via systemd timer (every 5min)
14. **k3s** - K3s cluster installation and configuration
15. **plex** - Plex Media Server installation and configuration

## User Management

- **Proxmox hosts**: User `eric` with passwordless sudo
- **LXC containers**: User `eric` with passwordless sudo
- **VMs**: User `eric` via cloud-init
- **Services**: Run as dedicated users (adguard, unbound, plex; postfix runs as root)

All hosts use `eric` for SSH access with passwordless sudo. LXC containers are unprivileged (mapped UIDs for security). Note that while we SSH as `eric` to smtp-relay, Postfix itself runs as root (which is normal and expected for mail servers).

## Testing / Deployment Workflow

1. **Pre-deployment**:
   ```bash
   task ansible:ping          # Verify connectivity
   task lint                  # Validate syntax
   task deploy:check          # Dry-run
   ```

2. **Deploy**:
   ```bash
   task deploy:all            # Full stack
   # Or target specific hosts/roles
   ansible-playbook ansible/playbooks/base.yml --limit pve-nas-01
   ```

3. **Post-deployment**:
   ```bash
   task deploy:verify         # Comprehensive verification
   task collect-state         # Snapshot current state
   ```

## Version Management

Application versions centralized in `ansible/inventories/prod/group_vars/all.yml`:

```yaml
adguard_home_version: "0.107.52"
adguardhome_sync_version: "0.8.2"
tailscale_version: "latest"
acme_version: "latest"
debian_version: "13"  # trixie
```

Update versions here, then run `task maintenance:update-full` to upgrade.

## Storage Architecture (pve-nas-01)

**ZFS Pools**:
- `tank` - 6x 22TB raidz2 (~122TB usable) - Media and bulk storage
- `ssd` - 3x 4TB raidz1 (~10.9TB) - App data and databases
- `nvme` - 1x 4TB NVMe (~2.27TB) - Hot downloads and fast scratch
- `archive` - 4x 6TB raidz1 (~21.8TB) - Cold storage and backups

**Key Datasets**: tank/media, tank/share, tank/downloads, ssd/appdata, nvme/downloads

**NEVER create/destroy ZFS pools via Ansible** - pools are created manually (too critical to automate). Ansible only sets properties and mounts.

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
- 13-ci-cd.md - CI/CD pipelines and GitHub Actions
- 14-post-base-plan.md - K3s platform roadmap and workload planning
- 15-credential-rotation.md - Credential rotation procedures
- 16-next-steps.md - TODO and feature roadmap
- 17-disaster-recovery.md - Disaster recovery and backup procedures
- 18-bootstrap-new-systems.md - Bootstrapping new LXC containers and VMs
- 19-k3s-deployment.md - K3s cluster deployment (complete workflow with all components)
- 20-plex-deployment.md - Plex Media Server deployment (LXC with bind mounts)

## Important Context Files

- `CLUSTER_STATUS.txt` - Full cluster state snapshot (generated via `task collect-state`)

## Credits

Repository structure inspired by [FreekingDean/homelab](https://github.com/FreekingDean/homelab)
