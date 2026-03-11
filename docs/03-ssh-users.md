# SSH and User Management

This document covers SSH configuration, user management, and authentication for the homelab.

## User Strategy

The homelab uses a consistent user management approach:

- **Proxmox Hosts**: User `eric` with passwordless sudo
- **LXC Containers**: User `eric` with passwordless sudo
  - Containers are **unprivileged** (mapped UIDs for security)
  - Services run as dedicated non-root users (adguard, unbound)
  - Postfix on smtp-relay runs as root (normal for mail servers)
- **VMs**: User `eric` via cloud-init

## SSH Configuration

### Proxmox Hosts

Applies to all 6 cluster nodes: pve-nas-01, pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01

**User**: `eric`

SSH configuration managed by the `base` role:

```yaml
ssh_port: 22
ssh_permit_root_login: "no"  # Default for most hosts
ssh_password_authentication: false
ssh_pubkey_authentication: true
```

**Key Features**:
- Root login disabled via SSH (with exception below)
- User `eric` has passwordless sudo
- SSH keys stored in 1Password and deployed via Ansible
- Restricted by source IP (from="192.168.0.0/24,100.64.0.0/10")

**Proxmox Host Exception**: Root SSH with key authentication is enabled on Proxmox cluster hosts (`ssh_permit_root_login: "prohibit-password"` in `group_vars/proxmox.yml`). This is required for:
- **Live VM/CT migrations** between cluster nodes
- **ZFS storage replication** for HA failover
- **Proxmox cluster operations** (corosync, pve-cluster)

This is a standard Proxmox requirement - the cluster cannot function without root-level access between nodes. Password authentication remains disabled; only key-based authentication is allowed.

### LXC Containers (dns-01, dns-02, smtp-relay)

**User**: `eric` (all containers)

```bash
ssh eric@192.168.0.150  # dns-01
ssh eric@192.168.0.160  # dns-02
ssh eric@192.168.0.151  # smtp-relay
```

Services run as dedicated non-root users with appropriate capabilities:
- AdGuard Home: `adguard:adguard`
- Unbound: `unbound:unbound`
- Postfix: runs as root (standard for mail servers)

### SSH Keys

SSH public keys are managed via 1Password:

```yaml
secrets:
  ssh_public_key: "op://Homelab/SSH Key/public key"
```

The key is deployed to `~/.ssh/authorized_keys` with IP restrictions:

```
from="192.168.0.0/24,100.64.0.0/10" ssh-ed25519 AAAAC3Nza... eric@MacBookPro.esweiss.com
```

## Connecting to Hosts

### From Local Network

```bash
# Proxmox hosts (examples - all 6 hosts use same pattern)
ssh eric@192.168.0.102  # pve-nas-01
ssh eric@192.168.0.103  # pve-laptop-01
ssh eric@192.168.0.104  # pve-opt-01
ssh eric@192.168.0.105  # pve-opt-02
ssh eric@192.168.0.106  # pve-opt-03
ssh eric@192.168.0.107  # pve-prec-01

# LXC containers
ssh eric@192.168.0.150  # dns-01
ssh eric@192.168.0.160  # dns-02
ssh eric@192.168.0.151  # smtp-relay
ssh eric@192.168.0.152  # plex
```

### Via Tailscale VPN

```bash
# Proxmox hosts via Tailscale
ssh eric@pve-nas-01.tail-scale.ts.net
ssh eric@pve-opt-03.tail-scale.ts.net

# Or via Tailscale IP
tailscale status  # Get Tailscale IPs
ssh eric@100.x.x.x
```

## Ansible Connection Configuration

Ansible uses different connection methods for different host types:

**Proxmox Hosts** (in `inventories/prod/hosts.yml`):
```yaml
proxmox:
  hosts:
    pve-nas-01:
      ansible_host: 192.168.0.102
      ansible_user: eric
      ansible_become: true
```

**LXC Containers**:
```yaml
dns:
  vars:
    ansible_user: eric
  hosts:
    dns-01:
      ansible_host: 192.168.0.150
```

## Security Hardening

### SSH Daemon Configuration

Applied by the `base` role via direct modification of `/etc/ssh/sshd_config` using `lineinfile`. This approach ensures compatibility across Debian versions and validates the configuration before applying changes.

Settings applied:

```
# Authentication
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
ChallengeResponseAuthentication no
UsePAM yes

# Security hardening
X11Forwarding no
MaxAuthTries 3

# Connection keepalive
ClientAliveInterval 300
ClientAliveCountMax 2
```

**Note**: The role uses `lineinfile` with `validate: "sshd -t -f %s"` to ensure the SSH config remains valid before applying changes. Settings can be customized via variables in `ansible/roles/base/defaults/main.yml`.

### Sudo Configuration

User `eric` has passwordless sudo on Proxmox hosts and VMs:

```bash
# /etc/sudoers.d/eric
eric ALL=(ALL) NOPASSWD: ALL
```

**Bootstrap Setup**: For existing Proxmox hosts, this must be configured manually once before Ansible can run. After the first Ansible deployment, the `base` role automatically maintains this configuration.

**New VMs**: The `base` role configures this automatically via cloud-init or first-run playbook, so manual setup is not required.

### IP Restrictions

SSH keys include `from=` restrictions limiting access to:
- Local LAN: `192.168.0.0/24`
- Tailscale VPN: `100.64.0.0/10`

## Firewall Integration

SSH access is controlled by Proxmox firewall security groups:

- **`sg-host-admin`**: Used for Proxmox hosts (pve-nas-01, pve-laptop-01, etc.)
- **`sg-vm-admin`**: Used for VMs and LXC containers (dns-01, smtp-relay, gitlab, plex, k3s nodes, etc.)

Both groups allow SSH from admin networks:

```
IN ACCEPT -source +dc/admin_ts -p tcp -dport 22 -log nolog
IN ACCEPT -source +dc/admin_lan -p tcp -dport 22 -log nolog
```

Check which security group applies in `ansible/inventories/prod/hosts.yml` under `guest_security_groups` for each host.

See [11-firewall.md](11-firewall.md) for details.

## Fail2ban Protection

All hosts are protected by fail2ban, which automatically bans IPs after repeated failed authentication attempts.

### Trusted Networks

The following networks are whitelisted and will never be banned:

- **Loopback**: `127.0.0.0/8` - Localhost connections
- **LAN**: `192.168.0.0/24` - Local network
- **Tailscale**: `100.64.0.0/10` - VPN network

### Common Commands

**Check fail2ban status**:
```bash
sudo fail2ban-client status
```

**Check SSH jail status** (shows banned IPs and ban count):
```bash
sudo fail2ban-client status sshd
```

**Unban an IP address**:
```bash
sudo fail2ban-client set sshd unbanip <IP>
```

### Proxmox-Specific Jails

Proxmox hosts have an additional `proxmox` jail that protects the web UI (port 8006) from brute-force attacks:

```bash
# Check proxmox jail status
sudo fail2ban-client status proxmox
```

### Configuration

Fail2ban is deployed via the `base` Ansible role with the following settings:

**Standard Jails (sshd, proxmox)**:
- **Ban time**: 1 hour (3600 seconds)
- **Find time**: 10 minutes (600 seconds)
- **Max retries**: 5 failed attempts before ban
- **Backend**: systemd (uses journald for log parsing)

**Recidive Jail** (repeat offenders):
The recidive jail monitors fail2ban's own log for IPs that get banned repeatedly. If an IP is banned multiple times within 24 hours, the recidive jail issues a much longer ban:
- **Ban time**: 1 week (`fail2ban_recidive_bantime: 1w`)
- **Find time**: 1 day (`fail2ban_recidive_findtime: 1d`)
- **Max retries**: 3 bans before recidive ban (`fail2ban_recidive_maxretry: 3`)

This provides escalating protection: occasional failed logins result in a 1-hour ban, but persistent attackers get banned for a week after just 3 short bans.

**LXC Container Exception**: The recidive jail is disabled on LXC containers (dns-01, dns-02, smtp-relay). This is because the `route` banaction used in containers does not support `banaction_allports`, and duplicate route entries cause errors when multiple jails ban the same IP. The base sshd jail with extended bantime (1 hour) provides sufficient protection for these low-exposure services.

## Adding a New SSH Key

To add or update SSH keys:

1. **Update 1Password**:
   - Store new public key in `op://Homelab/SSH Key/public key`

2. **Deploy via Ansible**:
   ```bash
   ansible-playbook ansible/playbooks/base.yml --tags ssh
   ```

3. **Verify access**:
   ```bash
   ssh eric@192.168.0.102
   ```

## LXC User Management

LXC containers use the `eric` user with passwordless sudo, consistent with Proxmox hosts and VMs:

1. **Unprivileged Containers**: UIDs are mapped (e.g., container root = host UID 100000)
2. **Service Isolation**: All services run as dedicated non-root users:
   - AdGuard Home: `adguard:adguard`
   - Unbound: `unbound:unbound`
   - Postfix: runs as root (standard for mail servers)
3. **Consistent Automation**: Same user across all hosts simplifies Ansible playbooks

Note: While we SSH as `eric` to smtp-relay, Postfix itself runs as root. This is normal and expected for mail servers.

## Bootstrap Configuration

### Automated Bootstrap (Recommended)

Use the bootstrap script to prepare new Proxmox hosts for Ansible management:

```bash
# From your laptop
./scripts/bootstrap-proxmox-host.sh <host-ip> <your-ssh-public-key>

# Example:
./scripts/bootstrap-proxmox-host.sh 192.168.0.107 "ssh-ed25519 AAAA... eric@laptop"
```

The script handles:
- Creating user `eric` with sudo group membership
- Deploying SSH authorized keys with proper permissions
- Configuring passwordless sudo via `/etc/sudoers.d/eric`
- Installing `sudo` package if not present (common on fresh Proxmox)
- Temporarily disabling enterprise repos during package install

After bootstrap, verify: `ssh eric@<host-ip> sudo whoami` should return `root`.

### Manual Setup (Alternative)

If you prefer manual setup or the bootstrap script doesn't work:

1. **Ensure user `eric` exists with sudo group**:
   ```bash
   # SSH as root to new host
   ssh root@<host-ip>
   useradd -m -s /bin/bash -G sudo eric
   ```

2. **Configure passwordless sudo**:
   ```bash
   echo 'eric ALL=(ALL) NOPASSWD: ALL' | tee /etc/sudoers.d/eric
   chmod 440 /etc/sudoers.d/eric
   ```

3. **Deploy SSH key**:
   ```bash
   mkdir -p /home/eric/.ssh
   echo "your-ssh-public-key" >> /home/eric/.ssh/authorized_keys
   chown -R eric:eric /home/eric/.ssh
   chmod 700 /home/eric/.ssh
   chmod 600 /home/eric/.ssh/authorized_keys
   ```

After this setup, Ansible can manage everything else.

### Automated Setup for New VMs

For new VMs (like future k3s nodes), the `base` role handles everything automatically:

- User creation
- SSH key deployment
- NOPASSWD sudo configuration
- SSH hardening

No manual bootstrap required for new VMs provisioned via Ansible.

## Troubleshooting

### Cannot SSH to Host

1. **Verify firewall allows SSH**:
   ```bash
   sudo iptables -L PVEFW-HOST-IN -v -n | grep "dpt:22"
   ```

2. **Check SSH service status**:
   ```bash
   sudo systemctl status ssh
   ```

3. **Verify SSH key is deployed**:
   ```bash
   cat ~/.ssh/authorized_keys
   ```

4. **Test from different source IP**:
   - Ensure you're connecting from `192.168.0.0/24` or Tailscale

### "Permission denied (publickey)"

1. **Ensure SSH agent is running**:
   ```bash
   ssh-add -l
   ```

2. **Add your key to agent**:
   ```bash
   ssh-add ~/.ssh/id_ed25519
   ```

3. **Verify key matches authorized_keys**:
   ```bash
   ssh-keygen -lf ~/.ssh/id_ed25519.pub
   ```

### Sudo Requires Password

If `eric` user is prompted for password:

1. **Check sudoers file**:
   ```bash
   sudo cat /etc/sudoers.d/eric
   ```

2. **Re-apply base role**:
   ```bash
   ansible-playbook ansible/playbooks/base.yml --tags users
   ```

## References

- [SSH Hardening Guide](https://www.ssh.com/academy/ssh/sshd_config)
- [Ansible Connection Methods](https://docs.ansible.com/ansible/latest/user_guide/connection_details.html)
