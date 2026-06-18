# Base Role

Foundational system configuration applied to all managed hosts. Provides essential packages, SSH hardening, user management, timezone configuration, and DNS settings.

## What This Role Manages

### Package Management
- Core system packages (curl, wget, neovim, htop, tmux, git, jq, unzip, rsync, net-tools, dnsutils, ca-certificates, gnupg, lsb-release, sudo)
- VM-specific packages (qemu-guest-agent) -- automatically detected and installed only on KVM guests
- Apt cache updates with 1-hour validity window

### User Management
- Admin user creation and configuration
- Sudo group membership
- Passwordless sudo via `/etc/sudoers.d/` (validated with visudo)
- SSH authorized keys with network restrictions (LAN + Tailscale)
- Home directory and `.ssh` directory creation with correct permissions

### SSH Hardening
- Disable root login
- Disable password authentication (key-based only)
- Enable pubkey authentication
- Disable challenge-response authentication
- Disable X11 forwarding
- MaxAuthTries set to 3
- ClientAlive keepalive (300s interval, 2 max)
- All changes validated before applying (`sshd -t -f`)

### DNS Configuration
- DNS servers configured via `/etc/resolv.conf`
- Smart DNS selection:
  - DNS containers use `127.0.0.1` (localhost)
  - All other hosts use homelab DNS servers (192.168.0.150, 192.168.0.160)
- Immutable resolv.conf (`chattr +i`) to prevent overwrites by DHCP/systemd

### System Configuration
- Timezone (America/Los_Angeles by default)
- VM guest agent enablement (qemu-guest-agent service)
- Intel e1000e NIC workaround (disables TSO/GSO/GRO via a oneshot unit on
  bare-metal hosts with an affected I219/I218/I217 NIC, to prevent driver hangs)

> NIC offload workarounds for the Aquantia/Marvell AQC113 (and any future
> per-host offload tuning) are owned by the **`nic_tuning`** role, not this one.
> The base role only carries the auto-detected e1000e fix.

## Configuration

### Required Variables

```yaml
# Admin user (defined in group_vars/all.yml)
admin_user: eric
admin_email: "{{ lookup('ansible.builtin.env', 'ROOT_EMAIL_ALIAS', default='root@localhost') }}"

# SSH configuration
ssh_port: 22
ssh_permit_root_login: "no"
ssh_password_authentication: false
ssh_pubkey_authentication: true

# SSH authorized keys with network restrictions
ssh_authorized_keys:
  - >-
    from="192.168.0.0/24,100.64.0.0/10"
    {{ lookup('ansible.builtin.env', 'SSH_PUBLIC_KEY', default='') }}

# DNS servers
dns_servers:
  - 192.168.0.150
  - 192.168.0.160

# Timezone
timezone: America/Los_Angeles

# Packages
common_packages:
  - curl
  - wget
  - neovim
  - htop
  - tmux
  - git
  - jq
  - unzip
  - rsync
  - net-tools
  - dnsutils
  - ca-certificates
  - gnupg
  - lsb-release
  - sudo

vm_packages:
  - qemu-guest-agent
```

### 1Password Secrets

```yaml
secrets:
  ssh_public_key: "op://Homelab/SSH Key/public key"
  root_email_alias: "op://Homelab/Email Config/root_alias"
```

## Deployment

```bash
# Deploy to all managed hosts
task infra:base

# Deploy to specific host
ansible-playbook ansible/playbooks/base.yml --limit pve-nas-01

# Dry-run to see changes
task infra:check
```

## Architecture

The base role is applied to the `base_managed` inventory group, which includes:
- Proxmox hosts (`proxmox` group)
- DNS servers (`dns` group)
- Mail relay (`mail` group)

This ensures all infrastructure hosts have:
1. Consistent package sets
2. Hardened SSH configuration
3. Correct admin user setup
4. Proper DNS resolution
5. Correct timezone

## Task Flow

```
1. Update apt cache (1-hour validity)
2. Install common packages
3. Detect if running in VM
   ├─ Yes: Install qemu-guest-agent, enable service
   └─ No: Skip VM packages
4. Check if admin user exists
5. Create admin user (if needed)
6. Create .ssh directory
7. Deploy authorized_keys
8. Configure passwordless sudo
9. Include SSH hardening tasks
   ├─ Configure sshd_config (validated)
   └─ Restart sshd if changed
10. Set timezone
11. Include DNS configuration tasks
    ├─ Determine DNS servers (localhost for dns hosts, homelab DNS for others)
    ├─ Deploy /etc/resolv.conf
    ├─ Check immutability status
    └─ Set immutable flag if needed
```

## Files

- `tasks/main.yml` - Main task orchestration
- `tasks/ssh.yml` - SSH hardening configuration
- `tasks/dns.yml` - DNS server configuration
- `../../templates/resolv.conf.j2` - DNS resolver configuration template (shared)
- `defaults/main.yml` - Default variable values
- `handlers/main.yml` - Service restart handlers

## Dependencies

None - this is the foundational role.

## Security

- SSH password authentication disabled by default
- Root login disabled
- SSH keys restricted to LAN (192.168.0.0/24) and Tailscale (100.64.0.0/10) networks
- Sudoers configuration validated before applying
- SSH configuration validated before reloading service
- Resolv.conf made immutable to prevent tampering

## Idempotency

- Package installation is idempotent
- User creation checks for existence first
- SSH configuration changes only trigger restart if modified
- DNS immutability only set if not already immutable
- Apt cache update uses `cache_valid_time` to avoid unnecessary refreshes
