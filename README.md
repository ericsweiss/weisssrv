# weisssrv - Homelab Infrastructure as Code

This repository contains the complete infrastructure-as-code for the Weiss homelab environment.
All configurations are managed through Ansible and Terraform with GitOps principles.

## Repository Structure

```
weisssrv/
├── ansible/
│   ├── inventories/prod/       # Production inventory
│   │   ├── hosts.yml           # Host definitions
│   │   ├── group_vars/         # Group variables
│   │   └── host_vars/          # Host-specific variables
│   ├── roles/                  # Ansible roles
│   ├── playbooks/              # Playbooks
│   └── requirements.yml        # Ansible collections
├── terraform/
│   └── cloudflare/             # Cloudflare DNS management
├── kubernetes/                 # k3s and GitOps (future)
│   ├── bootstrap/              # Cluster bootstrap configs
│   ├── flux/                   # Flux GitOps controller
│   └── apps/                   # Application manifests
├── docs/                       # Documentation
├── scripts/                    # Utility scripts
├── .github/workflows/          # CI/CD pipelines
└── Taskfile.yml                # Task automation
```

## Getting Started

This repository provides a complete "bare metal to production" automation path.

### For New Installations

If you're starting from scratch:

1. **Hardware Setup**: Follow [docs/00-hardware-setup.md](docs/00-hardware-setup.md) to:
   - Configure BIOS/UEFI settings
   - Install Proxmox VE from ISO
   - Set up initial networking and storage
   - Create ZFS pools (NAS node)
   - Prepare nodes for Ansible automation

2. **Environment Setup**: Follow [docs/02-install.md](docs/02-install.md) to:
   - Install required tools (Ansible, Terraform, Task, 1Password CLI)
   - Configure 1Password secrets
   - Set up SSH access
   - Deploy infrastructure via Ansible

3. **Verify Deployment**: Run `task collect-state` to generate a cluster state snapshot

### For Existing Clusters

If you already have infrastructure running:

1. Clone this repository:
   ```bash
   git clone https://github.com/ericsweiss/weisssrv.git
   cd weisssrv
   ```

2. Install Ansible collections:
   ```bash
   task ansible:install-collections
   ```

3. Verify connectivity:
   ```bash
   task ansible:ping
   ```

4. Run a dry-run deployment:
   ```bash
   task deploy:check
   ```

5. Apply configuration:
   ```bash
   task deploy:all
   ```

### Common Operations

```bash
# List all available tasks
task --list

# Deploy specific components
task deploy:base          # Base packages and SSH hardening
task deploy:dns           # DNS stack (Unbound + AdGuard Home)
task deploy:storage       # NAS storage services

# Linting and validation
task lint                 # Run all linters
task terraform:validate   # Validate Terraform configs

# State collection (for auditing/debugging)
task collect-state        # Generate redacted cluster state report
```

## Secrets Management

All secrets are stored in 1Password and injected at runtime using the 1Password CLI.

Secret references are defined in `group_vars` as 1Password item paths:
```yaml
smtp_sasl_password: "op://Homelab/SMTP Relay Gmail/password"
cloudflare_api_token: "op://Homelab/Cloudflare DNS Token/credential"
```

**Never commit secrets to this repository.**

## DNS Architecture

This homelab uses a split DNS architecture:

| Domain | Purpose | Resolution |
|--------|---------|------------|
| `*.esweiss.com` | Internal services | AdGuard Home rewrites to internal IPs |
| `*.ericsweiss.com` | External/public services | Cloudflare DNS (managed via Terraform) |

Internal DNS queries go through AdGuard Home (dns-01/dns-02) which forwards to Unbound for DoT upstream resolution.

## Documentation

Complete documentation is available in the `docs/` directory:

### Getting Started
- [00-hardware-setup.md](docs/00-hardware-setup.md) - Hardware setup and Proxmox installation (bare metal → ready for Ansible)
- [01-overview.md](docs/01-overview.md) - Architecture overview and network topology
- [02-install.md](docs/02-install.md) - Complete installation guide (laptop setup → production deployment)
- [03-ssh-users.md](docs/03-ssh-users.md) - SSH and user management

### Infrastructure Services
- [04-qol.md](docs/04-qol.md) - Quality of life configurations (Oh My Zsh, Neovim, etc.)
- [05-tailscale.md](docs/05-tailscale.md) - Tailscale VPN setup
- [06-zfs.md](docs/06-zfs.md) - ZFS storage configuration and management
- [07-fileservices.md](docs/07-fileservices.md) - NFS and Samba file services
- [08-dns.md](docs/08-dns.md) - DNS stack (AdGuard Home + Unbound)
- [09-certs.md](docs/09-certs.md) - TLS certificates (acme.sh + distribution)
- [10-mail.md](docs/10-mail.md) - Mail relay configuration (Postfix → Gmail)
- [11-firewall.md](docs/11-firewall.md) - Proxmox firewall (IPSets + Security Groups)

### Operations & Planning
- [12-runbooks.md](docs/12-runbooks.md) - Operational runbooks and procedures
- [13-ci-cd.md](docs/13-ci-cd.md) - CI/CD pipelines and GitHub Actions
- [14-post-base-plan.md](docs/14-post-base-plan.md) - K3s platform roadmap and workload planning
- [15-credential-rotation.md](docs/15-credential-rotation.md) - Credential rotation procedures
- [16-next-steps.md](docs/16-next-steps.md) - TODO and feature roadmap
- [17-disaster-recovery.md](docs/17-disaster-recovery.md) - Disaster recovery and backup procedures
- [18-bootstrap-new-systems.md](docs/18-bootstrap-new-systems.md) - Bootstrapping new LXC containers and VMs for Ansible automation

## Credits

Project + Repository structure and patterns inspired by:

- [FreekingDean/homelab](https://github.com/FreekingDean/homelab)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
