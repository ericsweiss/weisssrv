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

### Proxmox Hosts (pve-nas-01, pve-opt-03)

**User**: `eric`

SSH configuration managed by the `base` role:

```yaml
ssh_port: 22
ssh_permit_root_login: "no"
ssh_password_authentication: false
ssh_pubkey_authentication: true
```

**Key Features**:
- Root login disabled via SSH
- User `eric` has passwordless sudo
- SSH keys stored in 1Password and deployed via Ansible
- Restricted by source IP (from="192.168.0.0/24,100.64.0.0/10")

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
# Proxmox hosts
ssh eric@192.168.0.102  # pve-nas-01
ssh eric@192.168.0.106  # pve-opt-03

# LXC containers
ssh eric@192.168.0.150  # dns-01
ssh eric@192.168.0.160  # dns-02
ssh eric@192.168.0.151  # smtp-relay
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

Applied by the `base` role via `/etc/ssh/sshd_config.d/hardening.conf`:

```
# Disable password authentication
PasswordAuthentication no
PubkeyAuthentication yes

# Disable root login entirely
PermitRootLogin no

# Disable empty passwords
PermitEmptyPasswords no

# Disable challenge-response
ChallengeResponseAuthentication no

# Disable X11 forwarding
X11Forwarding no

# Limit auth attempts
MaxAuthTries 3

# Require public key auth
AuthenticationMethods publickey
```

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

SSH access is controlled by the Proxmox firewall `sg-host-admin` security group:

```
IN ACCEPT -source +dc/admin_ts -p tcp -dport 22 -log nolog
IN ACCEPT -source +dc/admin_lan -p tcp -dport 22 -log nolog
```

See [11-firewall.md](11-firewall.md) for details.

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

### Initial Setup for Existing Hosts

Before Ansible can manage a host, you need to:

1. **Ensure user `eric` exists with sudo group**:
   ```bash
   # Already exists on pve-nas-01 and pve-opt-03
   ```

2. **Configure passwordless sudo** (one-time manual setup):
   ```bash
   # On each Proxmox host
   ssh eric@192.168.0.102
   echo 'eric ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/eric
   sudo chmod 440 /etc/sudoers.d/eric
   exit

   # Repeat for pve-opt-03
   ```

3. **Deploy SSH key** (if not already present):
   ```bash
   # The base role will handle this, but for bootstrap you can manually:
   ssh-copy-id eric@192.168.0.102
   ```

After this one-time setup, Ansible can manage everything else.

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
   sudo systemctl status sshd
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
