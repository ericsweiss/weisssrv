# Base Role

Foundational system configuration applied to all managed hosts. Provides essential packages, Proxmox repository setup, SSH hardening, fail2ban intrusion prevention, user management, timezone configuration, and DNS settings.

## What This Role Manages

### Package Management
- Proxmox repositories on PVE hosts (enterprise repos disabled, community
  `pve-no-subscription` enabled as a deb822 `.sources` stanza pinned to the
  Proxmox archive keyring via `Signed-By`)
- Core system packages (curl, wget, neovim, htop, tmux, git, jq, unzip, rsync, net-tools, dnsutils, ca-certificates, gnupg, lsb-release, sudo)
- VM-specific packages (qemu-guest-agent) -- automatically detected and installed only on KVM guests
- Apt cache updates with 1-hour validity window
- unattended-upgrades disabled on VMs and containers (updates are managed via
  controlled Task/Ansible workflows instead)

### User Management
- Admin user creation and configuration
- Sudo group membership
- Passwordless sudo via `/etc/sudoers.d/` (validated with visudo)
- SSH authorized keys with network restrictions (LAN + Tailscale + k3s pod CIDR)
- Home directory and `.ssh` directory creation with correct permissions

### SSH Hardening
- Disable root login
- Disable password authentication (key-based only)
- Enable pubkey authentication
- Disable challenge-response authentication
- Disable X11 forwarding
- MaxAuthTries set to 3
- ClientAlive keepalive (300s interval, 2 max)
- Written as a `00-hardening.conf` drop-in under `/etc/ssh/sshd_config.d/`
  (first-match-wins, so it beats cloud-init drop-ins); the merged config is
  validated (`sshd -t`) before install and asserted effective via `sshd -T`

### Fail2ban
- sshd jail enabled on all hosts (aggressive mode, systemd backend)
- pveproxy jail on Proxmox hosts (`fail2ban_pveproxy_enabled`)
- Recidive jail for repeat offenders on physical/VM hosts
- LXC containers use `banaction = route` (blackhole routes) because
  unprivileged containers lack CAP_NET_ADMIN for iptables/nftables; the
  recidive jail is disabled there — trade-offs documented in
  `tasks/fail2ban.yml`
- LAN (192.168.0.0/24) and Tailscale (100.64.0.0/10) are never banned

### DNS Configuration
- DNS servers configured via `/etc/resolv.conf` (rendered by the shared
  `resolv_conf` role)
- Smart DNS selection:
  - DNS containers: `127.0.0.1` when the local resolver stack already answers
    (probed with `dig @127.0.0.1`); external bootstrap DNS (1.1.1.1/8.8.8.8)
    only while it does not — first deploy chicken-and-egg. The unbound and
    adguard_home roles switch to localhost once the stack is verified.
  - All other hosts use homelab DNS servers (192.168.0.150, 192.168.0.160)
- Immutable resolv.conf (`chattr +i`, managed inside the `resolv_conf` role
  via `resolv_conf_immutable: true`) to prevent overwrites by DHCP/systemd.
  On unprivileged LXC containers the immutable flag cannot be set (no
  CAP_LINUX_IMMUTABLE); the role warns and relies on the file being
  Ansible-managed there.

### System Configuration
- Timezone (America/Los_Angeles by default; symlink-only method in containers)
- VM guest agent enablement (qemu-guest-agent service)
- openipmi.service masked on hosts without IPMI hardware (its LSB init script
  otherwise fails at boot and leaves systemd degraded)
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
# Fails with a hint when ROOT_EMAIL_ALIAS is unset (run via the task wrapper / op run)
admin_email: "{{ lookup('ansible.builtin.env', 'ROOT_EMAIL_ALIAS') or undef(hint='ROOT_EMAIL_ALIAS env var must be set') }}"

# SSH configuration
ssh_port: 22
ssh_permit_root_login: "no"      # "prohibit-password" on Proxmox hosts
ssh_password_authentication: false
ssh_pubkey_authentication: true

# SSH authorized keys with network restrictions (LAN, Tailscale, k3s pod CIDR)
ssh_authorized_keys:
  - >-
    from="192.168.0.0/24,100.64.0.0/10,10.42.0.0/16"
    {{ lookup('ansible.builtin.env', 'SSH_PUBLIC_KEY', default='') }}

# DNS servers
dns_servers:
  - 192.168.0.150
  - 192.168.0.160

# Timezone
timezone: America/Los_Angeles
```

Package lists (`common_packages`, `vm_packages`) and the full fail2ban knob set
(`fail2ban_*`: jail toggles, ban/find times, retry counts, ignoreip, optional
email notifications) live in `defaults/main.yml` with per-group overrides in
inventory (e.g. `fail2ban_pveproxy_enabled: true` in `group_vars/proxmox.yml`).

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

# Deploy a tagged subset (tags propagate into the included task files)
ansible-playbook ansible/playbooks/base.yml --tags ssh
ansible-playbook ansible/playbooks/base.yml --tags fail2ban

# Dry-run to see changes
task infra:check
```

## Architecture

The base role is applied to the `base_managed` inventory group, which includes:
- Proxmox hosts (`proxmox` group)
- DNS servers (`dns` group)
- Mail relay (`mail` group)

(The plex playbook applies it to the Plex LXC as well.)

This ensures all infrastructure hosts have:
1. Consistent package sets
2. Hardened SSH configuration
3. Fail2ban intrusion prevention
4. Correct admin user setup
5. Proper DNS resolution
6. Correct timezone

## Task Flow

```
1. Configure Proxmox repositories (PVE hosts only)
2. Update apt cache (1-hour validity)
3. Install common packages
4. Detect virtualization (KVM guest / container facts)
   └─ KVM: install qemu-guest-agent, enable service
5. Disable unattended-upgrades (VMs and containers)
6. Mask openipmi.service (no-IPMI hosts)
7. Create admin user, .ssh directory, authorized_keys, passwordless sudo
8. Include SSH hardening tasks
   ├─ Render drop-in candidate + validate merged config (sshd -t)
   ├─ Install /etc/ssh/sshd_config.d/00-hardening.conf (restart sshd)
   └─ Assert effective values via sshd -T
9. Set timezone (hwclock method, or symlink in containers)
10. Include DNS configuration tasks
    ├─ Probe local resolver on DNS containers (keep 127.0.0.1 when healthy)
    ├─ Determine DNS servers
    └─ Include resolv_conf role (writes file, manages immutable flag)
11. e1000e TSO fix (auto-detected bare-metal hosts)
12. Include fail2ban tasks (install, jail.local, filters, service)
```

## Files

- `tasks/main.yml` - Main task orchestration
- `tasks/proxmox-repos.yml` - Proxmox repository configuration
- `tasks/ssh.yml` - SSH hardening configuration
- `tasks/dns.yml` - DNS server selection (delegates to the `resolv_conf` role)
- `tasks/fail2ban.yml` - Fail2ban installation and configuration
- `templates/sshd-hardening.conf.j2` - SSH hardening drop-in
- `templates/jail.local.j2` / `templates/proxmox.conf.j2` - Fail2ban config
- `../resolv_conf/templates/resolv.conf.j2` - DNS resolver template (shared role)
- `defaults/main.yml` - Default variable values
- `handlers/main.yml` - Service restart handlers

## Dependencies

None - this is the foundational role. (It includes the shared `resolv_conf`
helper role for /etc/resolv.conf.)

## Security

- SSH password authentication disabled by default
- Root login disabled (key-only on Proxmox for migration/replication)
- SSH keys restricted to LAN (192.168.0.0/24), Tailscale (100.64.0.0/10), and the k3s pod CIDR (10.42.0.0/16)
- Fail2ban bans brute-force sources on SSH (and pveproxy on Proxmox hosts)
- Sudoers configuration validated before applying
- SSH configuration validated before install and asserted effective after
- Proxmox community repo pinned to the archive keyring (`Signed-By`)
- resolv.conf made immutable to prevent tampering where the platform allows it
  (not enforceable in unprivileged LXC containers — the resolv_conf role warns
  there instead)

## Idempotency

- Package installation is idempotent
- User creation checks for existence first
- SSH configuration changes only trigger restart if modified
- DNS immutability only reports changed on a real absent-to-present transition
- Healthy DNS containers keep their localhost resolver on re-runs
- Apt cache update uses `cache_valid_time` to avoid unnecessary refreshes
