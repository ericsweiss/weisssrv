# Installation Guide

This guide covers setting up your development environment and deploying the homelab infrastructure from a fresh clone to production.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Laptop/Workstation Setup](#laptopworkstation-setup)
3. [1Password Configuration](#1password-configuration)
4. [Homelab Node Preparation](#homelab-node-preparation)
5. [Installation Steps](#installation-steps)
6. [Testing and Validation](#testing-and-validation)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

### Hardware

Before beginning, ensure your homelab nodes are installed and configured according to [00-hardware-setup.md](00-hardware-setup.md).

Required state:
- Proxmox VE installed on all nodes
- Network configured with static IPs
- ZFS pools created (NAS node)
- User `eric` created with sudo access
- SSH access working

### Required Software

On your laptop/workstation:
- **macOS** 12+ or Linux distribution
- **Homebrew** (macOS) or equivalent package manager
- Internet connection

## Laptop/Workstation Setup

### 1. Install Core Tools

**macOS**:

```bash
# Check Homebrew
brew --version

# If not installed:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install required tools
brew install go-task/tap/go-task    # Task runner
brew install ansible                 # Configuration management
brew tap hashicorp/tap
brew install hashicorp/tap/terraform # Infrastructure as code
brew install --cask 1password-cli   # Secrets management

# Install helpful tools
brew install jq yq                  # JSON/YAML processors
brew install knot                   # provides kdig for DoT verification
```

**Linux (Debian/Ubuntu)**:

```bash
# Install Task
sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

# Install Ansible
sudo apt update
sudo apt install -y ansible

# Install Terraform
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update
sudo apt install terraform

# Install 1Password CLI
# Follow: https://developer.1password.com/docs/cli/get-started/

# Install tools
sudo apt install -y jq knot-dnsutils
```

### 2. Verify Installations

```bash
# Check versions
task --version        # Should be 3.x
ansible --version     # Should be 2.15+
terraform --version   # Should be 1.15+ (matches versions.tf floor + CI image)
op --version          # Should be 2.x+

# Check Python (required by Ansible)
python3 --version     # Should be 3.8+
```

### 3. Clone Repository

```bash
# Primary (GitLab - canonical source)
git clone https://git.ericsweiss.com/eric/weisssrv.git
cd weisssrv

# Alternative (GitHub mirror - read-only)
# git clone https://github.com/ericsweiss/weisssrv.git
```

### 4. Install Ansible Collections

```bash
# Install required Ansible Galaxy collections
task ansible:install-collections

# OR manually:
ansible-galaxy collection install -r ansible/requirements.yml
```

Expected collections:
- `community.general`
- `ansible.posix`
- `community.crypto`
- `community.docker` (required by Molecule tests)

### 5. Install Linting Tools

For local development and CI/CD validation:

```bash
# Install Python linting tools
pip3 install ansible-lint yamllint

# Verify installation
ansible-lint --version
yamllint --version
```

These tools validate:
- Ansible playbooks and roles (production profile)
- YAML syntax and formatting
- Security best practices

### 6. Configure SSH

```bash
# Verify SSH key exists
ls -la ~/.ssh/id_ed25519.pub

# If not, create one:
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com"

# Add to SSH agent
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# Test SSH to homelab (should work if key is deployed)
ssh eric@192.168.0.102  # pve-nas-01
ssh eric@192.168.0.106  # pve-opt-03
ssh eric@192.168.0.150  # dns-01
```

**If SSH fails**, ensure keys are deployed on all nodes (see [Homelab Node Preparation](#homelab-node-preparation)).

## 1Password Configuration

### 1. Sign In to 1Password Desktop

Ensure 1Password desktop app is running and you're signed in.

### 2. Enable 1Password CLI

```bash
# Turn on CLI integration in 1Password app:
# Settings → Developer → Command Line

# Sign in via CLI
op account add --address my.1password.com --email your-email@example.com

# OR if already added:
eval $(op signin)
```

### 3. Create Required Items in "Homelab" Vault

Create these items (if they don't exist):

**Cloudflare DNS Token** (in-cluster ESO consumers + acme_certs; scope the
token to Zone:Read + DNS:Edit only):
```
Title: Cloudflare DNS Token
Type: API Credential
Fields:
  - credential: [your-cloudflare-api-token]
  - username: [your-cloudflare-account-id]
```

**Cloudflare Terraform Token** (Terraform only — needs the extra Zone
Settings:Edit scope because `terraform/cloudflare` manages
`cloudflare_zone_settings_override`; kept separate so the in-cluster token
cannot change zone-wide TLS posture):
```
Title: Cloudflare Terraform Token
Type: API Credential
Fields:
  - credential: [token with Zone:Read + DNS:Edit + Zone Settings:Edit]
  - username: [your-cloudflare-account-id]
```

**SMTP Relay Gmail**:
```
Title: SMTP Relay Gmail
Type: Login
Fields:
  - username: your-email@gmail.com
  - password: [gmail-app-password]
```

**SMTP Relay Auth**:
```
Title: SMTP Relay Auth
Type: Login
Fields:
  - username: relayuser (or your chosen relay username)
  - password: [secure-password-for-null-clients]
```

**Email Config**:
```
Title: Email Config
Type: Secure Note
Fields:
  - root_alias: ericsweiss1@gmail.com
```

**AdGuard Home**:
```
Title: AdGuard Home
Type: Login
Fields:
  - username: eric
  - password: [your-adguard-password]
```

**Tailscale Auth Key**:
```
Title: Tailscale Auth Key
Type: API Credential
Fields:
  - credential: [your-tailscale-auth-key]
```

**SSH Key**:
```
Title: SSH Key
Type: SSH Key
Fields:
  - public key: ssh-ed25519 AAAA... eric@MacBookPro.esweiss.com
  - private key: [optional, for automation]
```

**Samba NAS User** (needed by `task storage:deploy`, Phase 5):
```
Title: Samba NAS User
Type: Login
Fields:
  - password: [password for the nas Samba user]
```

**DNS-01 SSH Key** (needed by `task dns:deploy`, Phase 6 — cert distribution):
```
Title: DNS-01 SSH Key
Type: SSH Key
Fields:
  - private key: [ed25519 private key]
  - public key: ssh-ed25519 AAAA... acme@dns-01
```

This is the minimum set for the deployment phases in this guide. The complete
item list (k3s, applications, observability, encryption) is in
[docs/15-credential-rotation.md](./15-credential-rotation.md) under
"Required 1Password Items".

### 4. Test 1Password CLI Access

```bash
# Test reading secrets
op read "op://Homelab/Cloudflare DNS Token/credential"
op read "op://Homelab/SMTP Relay Gmail/password"
op read "op://Homelab/SMTP Relay Auth/username"
op read "op://Homelab/SMTP Relay Auth/password"

# All should output values (not errors)
```

**If this fails**:
1. Ensure 1Password desktop app is running
2. Check you're signed in: `op account list`
3. Re-sign in: `eval $(op signin)`

## Homelab Node Preparation

### Overview

Ensure SSH access and proper user configuration on all nodes before running Ansible.

### Current State

All hosts use the `eric` user for SSH access with passwordless sudo:

- **Proxmox Hosts** (pve-nas-01, pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01): User `eric` with sudo
- **LXC Containers** (dns-01, dns-02, smtp-relay, plex, immich-ml): User `eric` with sudo

Root SSH login is disabled on all hosts.

### Verify SSH Access

```bash
# From your laptop - all hosts use eric user
ssh eric@192.168.0.102  # pve-nas-01
ssh eric@192.168.0.106  # pve-opt-03
ssh eric@192.168.0.150  # dns-01
ssh eric@192.168.0.160  # dns-02
ssh eric@192.168.0.151  # smtp-relay
```

If SSH fails, see [Bootstrapping New Systems](18-bootstrap-new-systems.md) for setup instructions.

### Creating `eric` User on New LXCs

```bash
# On each LXC:
sudo pct enter 150

# Create user
useradd -m -s /bin/bash eric
mkdir -p /home/eric/.ssh
cp /root/.ssh/authorized_keys /home/eric/.ssh/
chown -R eric:eric /home/eric/.ssh
chmod 700 /home/eric/.ssh
chmod 600 /home/eric/.ssh/authorized_keys

# Add sudo access
echo "eric ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric

exit
```

### Configure NOPASSWD Sudo (Bootstrap Only)

For existing Proxmox hosts, configure passwordless sudo manually (one-time setup):

```bash
# pve-nas-01
ssh eric@192.168.0.102
echo 'eric ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/eric
sudo chmod 440 /etc/sudoers.d/eric
exit

# pve-opt-03
ssh eric@192.168.0.106
echo 'eric ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/eric
sudo chmod 440 /etc/sudoers.d/eric
exit
```

**Note**: This is only needed for existing hosts. New VMs provisioned via Ansible will have this configured automatically by the `base` role.

### Verify Firewall Allows Ansible SSH

The firewall should already allow SSH from:
- LAN (192.168.0.0/24)
- Tailscale (100.64.0.0/10)

**Test from your laptop**:
```bash
# Should succeed if you're on LAN
ssh eric@192.168.0.102

# Or via Tailscale
ssh eric@pve-nas-01.<tailnet>.ts.net  # replace <tailnet> with your tailnet name
```

## Installation Steps

### Phase 1: Connectivity

```bash
cd /Users/eric/src/weisssrv

# Test Ansible can reach all hosts
task ansible:ping

# Expected output:
# pve-nas-01 | SUCCESS => {"changed": false, "ping": "pong"}
# pve-opt-03 | SUCCESS => {"changed": false, "ping": "pong"}
# dns-01 | SUCCESS => {"changed": false, "ping": "pong"}
# dns-02 | SUCCESS => {"changed": false, "ping": "pong"}
# smtp-relay | SUCCESS => {"changed": false, "ping": "pong"}
```

**If any fail**:
- Check SSH: `ssh user@host`
- Check inventory: `ansible/inventories/prod/hosts.yml`
- Verify user and SSH keys

### Phase 2: Gather Facts

```bash
# Collect system information
ansible all -m setup --tree /tmp/facts

# Check a specific host
ansible pve-nas-01 -m setup | grep ansible_distribution
```

This verifies Ansible can execute commands on all hosts.

### Phase 3: Dry-Run Deployment

```bash
# Full dry-run (no changes)
task infra:check

# OR manually:
ansible-playbook ansible/playbooks/site.yml --check

# Watch for errors or failures
# This shows what WOULD change without actually changing anything
```

**Expected**:
- Many "ok" (already configured)
- Some "changed" (would be applied)
- Zero "failed" (indicates issues)

### Phase 4: Deploy Base Configuration

Start with the least risky deployment:

```bash
# Deploy base role only (packages, SSH config)
task infra:base

# OR manually:
ansible-playbook ansible/playbooks/base.yml

# This should be mostly idempotent (no changes if already configured)
```

**Verify**:
```bash
# SSH should still work
ssh eric@192.168.0.102

# Check installed packages
ansible all -m shell -a "which nvim htop"
```

### Phase 5: Deploy Storage Services (NAS Only)

```bash
# Deploy NFS, Samba, ZFS services
task storage:deploy

# Verify NFS exports
showmount -e 192.168.0.102

# Verify Samba
smbclient -L //192.168.0.102 -N
```

### Phase 6: Deploy DNS Stack

```bash
# Dry-run DNS deployment
ansible-playbook ansible/playbooks/dns.yml --check

# Deploy
ansible-playbook ansible/playbooks/dns.yml

# Verify DNS works
dig @192.168.0.150 esweiss.com
kdig @192.168.0.150 -p 853 +tls esweiss.com
```

### Phase 7: Deploy Full Stack

```bash
# Full stack deployment
task infra:deploy

# Monitor for errors
# This runs all playbooks against all hosts
```

### Phase 8: Initialize Terraform

```bash
# Initialize Terraform (handles state backend auth via 1Password)
task terraform:init

# Plan changes
task terraform:plan

# Review the plan
# If it looks correct, apply
task terraform:apply
```

> **Note**: The `task terraform:*` commands are preferred because they inject Cloudflare API credentials and GitLab HTTP state backend auth via `op run`. For manual `terraform` commands, you must export `TF_VAR_cloudflare_api_token`, `TF_VAR_cloudflare_account_id`, and the `TF_HTTP_*` environment variables yourself.

### Phase 9: Deploy the k3s Platform

Base infrastructure is now complete. The k3s cluster (VMs, k3s itself, kube-vip)
is a separate two-phase deployment — Ansible provisions it, Flux then reconciles
everything inside it:

```bash
task k3s:deploy      # provision the 9 VMs + install k3s + kube-vip
task k3s:status
```

Full procedure, including the Flux bootstrap and the expected post-bootstrap
state: [19-k3s-deployment.md](19-k3s-deployment.md), then
[29-flux-operations.md](29-flux-operations.md) for day-2 operations.

## Testing and Validation

### 1. SSH Access

```bash
# All hosts should be accessible using eric user
ssh eric@192.168.0.102  # pve-nas-01
ssh eric@192.168.0.106  # pve-opt-03
ssh eric@192.168.0.150  # dns-01
ssh eric@192.168.0.160  # dns-02
ssh eric@192.168.0.151  # smtp-relay
```

### 2. DNS Resolution

```bash
# Test DNS
dig @192.168.0.150 esweiss.com
dig @192.168.0.150 dns-01.esweiss.com

# Test DoT
kdig @192.168.0.150 -p 853 +tls esweiss.com

# Test from another machine
dig @192.168.0.150 google.com
```

### 3. AdGuard Home

```bash
# Access web UI (or http://192.168.0.150:3000 for direct access — port 3000 is plain HTTP)
open https://dns-01.esweiss.com

# Verify custom rules are present
# Check DNS rewrites
# Verify sync to dns-02
```

### 4. TLS Certificates

```bash
# Check cert expiry
ssh eric@192.168.0.150 "sudo openssl x509 -in /opt/AdGuardHome/certs/fullchain.pem -noout -dates"

# Test DoT with TLS
kdig @192.168.0.150 -p 853 +tls esweiss.com

# Test SMTP TLS
openssl s_client -connect 192.168.0.151:587 -starttls smtp
```

### 5. NFS Exports

```bash
# List exports (from a host in one of the allowed client sets)
showmount -e 192.168.0.102

# Expect the pseudo-root plus the child exports defined by
# nas_storage_exports in host_vars/pve-nas-01.yml — /export, /export/appdata,
# /export/share, /export/media, /export/tank-proxmox, /export/k3s-etcd and the
# six /export/backups-apps/* targets, each listed against its own client set.
# Most child exports require xprtsec=tls (docs/07); showmount itself only lists
# them, it does not prove the TLS handshake works.
```

### 6. Firewall

```bash
# Check firewall status
ssh eric@192.168.0.102 "sudo pve-firewall status"

# View rules
ssh eric@192.168.0.102 "sudo cat /etc/pve/firewall/cluster.fw"

# Test connectivity to allowed ports
nc -zv 192.168.0.150 53    # DNS
nc -zv 192.168.0.150 853   # DoT
nc -zv 192.168.0.150 3000  # AdGuard admin
```

### 7. Collect Current State

```bash
# Generate current state snapshot
task collect-state

# Review output (collect-state copies successful runs here)
cat CLUSTER_STATUS.txt
```

## Troubleshooting

### Ansible Connection Issues

**Problem**: `ansible all -m ping` fails

**Solutions**:
1. **Check SSH manually**:
   ```bash
   ssh eric@192.168.0.102
   ```

2. **Verify inventory**:
   ```bash
   cat ansible/inventories/prod/hosts.yml
   ```

3. **Check Ansible config**:
   ```bash
   ansible-config dump | grep SSH
   ```

4. **Test with verbose**:
   ```bash
   ansible pve-nas-01 -m ping -vvv
   ```

### 1Password CLI Not Working

**Problem**: `op read` fails with "not signed in"

**Solutions**:
1. **Re-sign in**:
   ```bash
   op signout
   eval $(op signin)
   ```

2. **Check desktop app running**:
   - Ensure 1Password desktop app is open
   - Check Settings → Developer → CLI integration enabled

3. **Verify account**:
   ```bash
   op account list
   ```

### Terraform Plan Fails

**Problem**: Terraform cannot authenticate to Cloudflare

**Solutions**:
1. **Check 1Password secret**:
   ```bash
   op read "op://Homelab/Cloudflare Terraform Token/credential"
   ```

2. **Export manually** (if not using `task terraform:plan`):
   ```bash
   export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare Terraform Token/credential")
   export CLOUDFLARE_ACCOUNT_ID=$(op read "op://Homelab/Cloudflare Terraform Token/username")
   cd terraform/cloudflare
   terraform plan
   ```

3. **Verify Cloudflare token permissions** (Terraform token):
   - Zone: DNS: Edit
   - Zone: Zone: Read
   - Zone: Zone Settings: Edit (required for `cloudflare_zone_settings_override`)

   The separate `Cloudflare DNS Token` item (in-cluster ESO consumers +
   acme_certs) needs only DNS:Edit + Zone:Read.

### Deployment Changes Nothing

**Problem**: Ansible runs but shows "ok" for everything, even though config is wrong

**Solutions**:
1. **Check idempotency**:
   - Ansible tasks are designed to be idempotent
   - "ok" means current state matches desired state

2. **Force handlers**:
   ```bash
   ansible-playbook ansible/playbooks/site.yml --force-handlers
   ```

3. **Check specific task**:
   ```bash
   ansible-playbook ansible/playbooks/site.yml --start-at-task="Task name"
   ```

### Service Not Starting

**Problem**: Service fails to start after deployment

**Solutions**:
1. **Check logs**:
   ```bash
   ssh host "sudo journalctl -u service-name -n 50"
   ```

2. **Check service status**:
   ```bash
   ansible host -m systemd -a "name=service-name state=started"
   ```

3. **Manually restart**:
   ```bash
   ssh host "sudo systemctl restart service-name"
   ```

### Firewall Blocking Access

**Problem**: Services unreachable after firewall deployment

**Solutions**:

```bash
# Check Proxmox firewall status
ssh eric@192.168.0.102 "sudo pve-firewall status"

# View active firewall rules
ssh eric@192.168.0.102 "sudo iptables -S PVEFW-HOST-IN"

# Verify security group assignments
ssh eric@192.168.0.102 "sudo cat /etc/pve/nodes/\$(hostname)/host.fw"

# Temporarily disable firewall for debugging (CAREFUL)
ssh eric@192.168.0.102 "sudo pve-firewall stop"

# Re-enable when fixed
ssh eric@192.168.0.102 "sudo pve-firewall start"
```

## Quick Reference

```bash
# Daily operations
task ansible:ping          # Check connectivity
task infra:check           # Dry-run to detect drift
task collect-state         # Generate state snapshot

# Deployments
task infra:deploy          # Full deployment
task infra:base            # Base config only
task dns:deploy            # DNS stack only
task storage:deploy        # NAS services only

# Terraform
task terraform:plan        # Show changes
task terraform:apply       # Apply changes

# Troubleshooting
ansible all -m ping -vvv   # Verbose ping test
ansible host -m shell -a "command"  # Run command on host
ssh eric@host              # Manual SSH
```

## Next Steps

After successful deployment:

1. Review [03-ssh-users.md](03-ssh-users.md) for SSH and user management
2. Configure quality of life improvements: [04-qol.md](04-qol.md)
3. Set up Tailscale VPN: [05-tailscale.md](05-tailscale.md)
4. Deploy k3s: [19-k3s-deployment.md](19-k3s-deployment.md) (the k3s layer;
   [14-post-base-plan.md](14-post-base-plan.md) is the superseded historical plan)

Your homelab is now fully operational and managed via GitOps.
