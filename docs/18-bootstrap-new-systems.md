# Bootstrapping New Systems for Ansible Automation

This guide covers preparing new LXC containers and VMs for Ansible automation. Follow these procedures to bring a fresh system from initial creation to fully automated management.

---

## Overview

### Bootstrap vs Normal Deployment

**Bootstrap** is the one-time manual setup required before Ansible can manage a system:
- Install OS/create container
- Configure network
- Enable SSH access
- Deploy initial SSH key

**Normal Deployment** is fully automated via Ansible after bootstrap:
- User management
- Package installation
- Service configuration
- Security hardening

### System Types

This homelab uses two types of managed systems:

| Type | User | SSH Method | Bootstrap Required |
|------|------|------------|-------------------|
| **Proxmox Hosts** | `eric` | Pubkey auth | Manual (one-time) |
| **LXC Containers** | `eric` | Pubkey auth | Manual (documented below) |
| **VMs** | `eric` | Pubkey auth | Cloud-init (minimal manual) |

Note: All hosts use the `eric` user for SSH. On smtp-relay, while we SSH as `eric`, Postfix itself runs as root (which is normal for mail servers).

---

## Prerequisites

Before bootstrapping any system, ensure you have:

### Network Planning

- **IP Address**: Static IP allocated (avoid DHCP conflicts)
- **Gateway**: 192.168.0.1 (router)
- **DNS Servers**: 192.168.0.150, 192.168.0.160 (dns-01, dns-02)
- **Hostname**: Follows naming convention (e.g., `app-01`; k3s nodes use `k3s-srv-*` / `k3s-agt-*`)

### Access Requirements

- **SSH Key**: Retrieved from 1Password
  ```bash
  op read "op://Homelab/SSH Key/public key"
  ```
- **Proxmox Access**: Web UI or SSH to Proxmox host
- **Firewall Planning**: Know which security groups to attach

### Template/Image

- **LXC**: Debian 13 (trixie) template downloaded to Proxmox
- **VM**: Debian cloud image or ISO

---

## LXC Container Bootstrap

### Step 1: Download LXC Template

On the Proxmox host where you'll create the container:

`<proxmox_lxc_template>` throughout this doc is the value of
`proxmox_lxc_template` in `ansible/inventories/prod/group_vars/all.yml` — the
single pin, never a literal copied into a runbook. Upstream silently rotates the
point release (a 13.1-2 → 13.6-1 rotation once broke a cached-template
recreate), so a hardcoded name goes stale and the download fails outright; see
docs/12 for the rotation warning.

```bash
# Resolve the pin, then download it
grep proxmox_lxc_template ansible/inventories/prod/group_vars/all.yml

# On the Proxmox host
pveam update
pveam available --section system | grep debian-13

pveam download local <proxmox_lxc_template>
```

Verify download:
```bash
ls -lh /var/lib/vz/template/cache/
```

### Step 2: Create LXC Container

Choose a VMID (container ID) that doesn't conflict with existing containers. The
allocated VMIDs are 150-158 (infrastructure + app guests) and 202-207 / 222 /
223 / 227 (the k3s fleet) — the examples below use 240 to stay clear of both.
Check what is live:

```bash
pct list
```

`<storage>` is the Proxmox storage id for the host you are creating on, derived
the same way the `proxmox_lxc` / `proxmox_vm` roles derive it: **`local-ssd` on
the five compute hosts, `ssd` on pve-nas-01** (which has no `local-ssd` pool).
Both are ZFS, so the guest gets snapshots, replication and — on `ssd` — at-rest
encryption. Do **not** use `local-lvm`: it is an LVM-thin pool outside ZFS
entirely, so a guest there has no snapshots, no replication and no encryption.
Four guests do sit on pve-nas-01's `local-lvm` deliberately (the two NAS k3s VMs
plus the plex and immich-ml LXC rootfs — see docs/32); that is a documented
exception for guests that must never wait on an unlock, not the default for a
new guest.

**Create unprivileged container** (recommended for security):

```bash
pct create <VMID> \
  local:vztmpl/<proxmox_lxc_template> \
  --hostname <hostname> \
  --net0 name=eth0,bridge=vmbr0,ip=<IP>/24,gw=192.168.0.1 \
  --nameserver 192.168.0.150 \
  --nameserver 192.168.0.160 \
  --storage <storage> \
  --rootfs <storage>:8 \
  --cores 2 \
  --memory 2048 \
  --swap 512 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 1
```

**Parameter Notes**:
- `<VMID>`: Container ID (e.g. 240, 241 — outside the allocated ranges above)
- `<hostname>`: DNS name (e.g. app-01)
- `<IP>`: Static IP (e.g. 192.168.0.240 — outside the allocated `.99-.161`,
  `.202-.207` and `.222/.223/.227` bands; see docs/01 for the full map)
- `--rootfs <storage>:8`: 8GB root filesystem (adjust as needed)
- `--unprivileged 1`: Run as unprivileged (security best practice)
- `--features nesting=1`: Enable nested containers (required for Docker/K8s)
- `--onboot 1`: Start on Proxmox boot
- `--start 1`: Start immediately after creation

**Example**:
```bash
pct create 240 \
  local:vztmpl/<proxmox_lxc_template> \
  --hostname app-01 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.0.240/24,gw=192.168.0.1 \
  --nameserver 192.168.0.150 \
  --nameserver 192.168.0.160 \
  --storage <storage> \
  --rootfs <storage>:16 \
  --cores 4 \
  --memory 4096 \
  --swap 1024 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 1
```

### Step 3: Configure SSH Access

The container starts automatically. Enter it to configure SSH:

```bash
pct enter <VMID>
```

**Inside the container**:

```bash
# Update package list
apt update

# Install OpenSSH server and sudo
apt install -y openssh-server sudo

# Create eric user with sudo privileges
useradd -m -s /bin/bash -G sudo eric

# Create SSH directory for eric
mkdir -p /home/eric/.ssh
chmod 700 /home/eric/.ssh

# Add your SSH public key
cat > /home/eric/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... eric@MacBookPro.esweiss.com
EOF

# Set proper permissions
chmod 600 /home/eric/.ssh/authorized_keys
chown -R eric:eric /home/eric/.ssh

# Configure passwordless sudo
echo 'eric ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric

# Enable and start SSH
systemctl enable ssh
systemctl start ssh

# Exit container
exit
```

**Retrieve SSH key from 1Password**:
```bash
# On your laptop
op read "op://Homelab/SSH Key/public key"
```

Copy the output and paste it in the authorized_keys file.

### Step 4: Verify SSH Access

From your laptop:

```bash
ssh eric@<IP>
```

If successful, you should connect without a password prompt.

### Step 5: Add to Ansible Inventory

Edit the inventory file to add the new container:

```bash
# ansible/inventories/prod/hosts.yml
```

Add to appropriate group:

```yaml
# Example: a k3s agent node. NOTE: the k3s VM fleet (3 servers + 6 agents) is
# normally created and provisioned by Ansible (`task k3s:provision-vms`, the
# weisssrv.infra.proxmox_vm role + cloud-init), not bootstrapped by hand — this snippet just
# shows the inventory shape. Groups are k3s_servers / k3s_agents with
# k3s_role: server|agent.
k3s_agents:
  hosts:
    k3s-agt-opt-01:
      ansible_host: 192.168.0.204
      ansible_user: eric
```

Or for a standalone service:

```yaml
# Example: Adding a standalone service on the bootstrapped container above
monitoring:
  hosts:
    app-01:
      ansible_host: 192.168.0.240
      ansible_user: eric
```

### Step 6: Create Host Variables (Optional)

If the host needs specific configuration:

```bash
# ansible/inventories/prod/host_vars/k3s-agt-opt-01.yml
---
# Host-specific variables
k3s_role: agent
```

### Step 7: Configure Firewall

Create firewall rules for the container:

```bash
# /etc/pve/firewall/<VMID>.fw
```

Example for a web service:

```
[OPTIONS]

enable: 1

[RULES]

GROUP sg-host-admin
GROUP sg-web-server

# Allow HTTP/HTTPS from LAN
IN ACCEPT -source 192.168.0.0/24 -p tcp -dport 80 -log nolog
IN ACCEPT -source 192.168.0.0/24 -p tcp -dport 443 -log nolog
```

Create this file on the Proxmox host:

```bash
# On Proxmox host
cat > /etc/pve/firewall/<VMID>.fw <<'EOF'
[OPTIONS]
enable: 1

[RULES]
GROUP sg-host-admin
EOF
```

### Step 8: Test Ansible Connectivity

Verify Ansible can reach the new container:

```bash
ansible app-01 -m ping
```

Expected output:
```
app-01 | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

### Step 9: Deploy Base Configuration

Run Ansible to configure the system:

The roles live in the `weisssrv.infra` collection, so install it first if this
is a fresh checkout (`task ansible:install-collections`, docs/02 § 4).

```bash
# Check what would change (dry run)
task infra:check -- --limit app-01

# Deploy base configuration
ansible-playbook ansible/playbooks/base.yml --limit app-01

# Or deploy everything for this host
ansible-playbook ansible/playbooks/site.yml --limit app-01
```

### Step 10: Verify Deployment

Run post-deployment verification:

```bash
# Full verification playbook
ansible-playbook ansible/playbooks/postflight.yml --limit app-01
```

Or manually verify:

```bash
# SSH should still work
ssh eric@192.168.0.240

# Check services
systemctl status ssh
systemctl status tailscale  # If configured

# Verify timezone
timedatectl

# Check common packages
which htop neovim git
```

---

## VM Bootstrap

VMs (including the entire 9-node k3s fleet and the GitLab/HAOS VMs) are normally
created and provisioned by Ansible — the `weisssrv.infra.proxmox_vm` role builds them from the
Debian cloud image with cloud-init, driven by the inventory (`task k3s:provision-vms`,
`task gitlab:deploy`). The manual `qm` steps below are retained for reference and
for bootstrapping a VM outside that flow; prefer the automated path for k3s nodes.

### Cloud-Init Method (Recommended)

`<storage>` follows the same rule as the LXC section above: `local-ssd` on the
compute hosts, `ssd` on pve-nas-01 — never `local-lvm`.

1. **Prepare Cloud Image**:
   ```bash
   # Download Debian cloud image
   wget https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2

   # Move to Proxmox storage
   qm importdisk <VMID> debian-13-generic-amd64.qcow2 <storage>
   ```

2. **Configure Cloud-Init**:
   ```bash
   qm set <VMID> --ide2 <storage>:cloudinit
   qm set <VMID> --boot c --bootdisk scsi0
   qm set <VMID> --serial0 socket --vga serial0

   # Network config
   qm set <VMID> --ipconfig0 ip=192.168.0.XXX/24,gw=192.168.0.1

   # SSH key
   qm set <VMID> --sshkey ~/.ssh/id_ed25519.pub

   # User
   qm set <VMID> --ciuser eric
   ```

3. **Start VM**:
   ```bash
   qm start <VMID>
   ```

Cloud-init automatically configures:
- User `eric` with SSH key
- Network settings
- Hostname
- Package updates

4. **Verify and Deploy**:
   ```bash
   # Test SSH
   ssh eric@192.168.0.XXX

   # Add to inventory (same as LXC, but with ansible_user: eric)

   # Deploy base config
   ansible-playbook ansible/playbooks/base.yml --limit new-vm
   ```

### Manual VM Bootstrap

If not using cloud-init:

1. Install Debian from ISO
2. During installation:
   - Create user `eric`
   - Configure network with static IP
   - Install SSH server
3. After first boot:
   - SSH as eric: `ssh eric@<IP>`
   - Copy SSH key: `ssh-copy-id eric@<IP>`
   - Configure sudo: `echo 'eric ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/eric`
4. Add to Ansible inventory and deploy

---

## What the Base Role Expects

The `weisssrv.infra.base` role (first role applied to all systems) expects these
minimum prerequisites:

### Must Exist Before Base Role

1. **SSH Server Running**:
   - Package `openssh-server` installed
   - Service `ssh` enabled and started

2. **Network Configured**:
   - Static IP assigned and reachable
   - Default gateway configured
   - DNS servers set

3. **Initial User Access**:
   - **LXC/VM/Proxmox**: User `eric` exists with SSH key and passwordless sudo

4. **Python Installed**:
   - Python 3 (usually included in Debian base)
   - Ansible requires Python on target systems

### Base Role Configures

After bootstrap, the base role handles:

- User management (creates/configures `eric` user)
- SSH hardening (disables password auth, sets security options)
- Common packages (htop, neovim, git, etc.)
- Timezone configuration
- Sudo configuration
- Authorized keys deployment from 1Password

### Later Roles Configure

Subsequent roles configure:

- QoL packages (Oh My Zsh, fzf, ripgrep)
- Tailscale VPN
- Service-specific configurations
- Firewall rules
- Application deployment

---

## Validation

### Pre-Bootstrap Checklist

Before running Ansible, verify:

- [ ] Container/VM created and running
- [ ] Network configured with static IP
- [ ] DNS resolution works (`ping google.com`)
- [ ] SSH server installed and running
- [ ] SSH key deployed to eric user
- [ ] Can SSH from laptop without password
- [ ] Added to Ansible inventory
- [ ] Firewall rules created (`/etc/pve/firewall/<VMID>.fw`)

### Post-Bootstrap Verification

After running the base role:

```bash
# 1. Test Ansible connectivity
ansible <hostname> -m ping

# 2. Test SSH access
ssh eric@<IP>

# 3. Verify sudo
ansible <hostname> -m shell -a "sudo whoami"  # Should return 'root'

# 4. Check base packages installed
ansible <hostname> -m shell -a "which htop neovim git"

# 5. Verify timezone
ansible <hostname> -m shell -a "timedatectl | grep 'Los_Angeles'"

# 6. Check SSH hardening — ask sshd for its effective config, not the monolithic
#    file: the base role writes a drop-in at /etc/ssh/sshd_config.d/00-hardening.conf
#    and never edits /etc/ssh/sshd_config (docs/03).
ansible <hostname> -b -m shell -a "sshd -T | grep -E 'passwordauthentication|permitrootlogin'"
# Should show: passwordauthentication no / permitrootlogin no
# (Proxmox hosts set prohibit-password, which sshd -T prints as without-password.)

# 7. Run full verification
ansible-playbook ansible/playbooks/postflight.yml --limit <hostname>
```

### Common Issues Checklist

If verification fails:

- [ ] Can ping the IP address?
- [ ] SSH service running? (`systemctl status ssh`)
- [ ] Correct SSH key deployed?
- [ ] Firewall blocking access?
- [ ] Correct user in inventory (eric for all hosts)?
- [ ] Sudo configured?
- [ ] Python installed on target?

---

## Troubleshooting

### Cannot SSH to New Container

**Symptom**: `ssh: connect to host <IP> port 22: Connection refused`

**Check**:
```bash
# On Proxmox host, enter container
pct enter <VMID>

# Verify SSH is running
systemctl status ssh

# If not running
systemctl start ssh
systemctl enable ssh

# Check if SSH is listening
netstat -tlnp | grep :22
```

**Firewall Issue**:
```bash
# On Proxmox host, verify firewall allows SSH
cat /etc/pve/firewall/<VMID>.fw

# Ensure it includes
GROUP sg-host-admin
```

### SSH Key Authentication Fails

**Symptom**: `Permission denied (publickey)`

**Check**:
```bash
# On container
cat /home/eric/.ssh/authorized_keys

# Verify permissions
ls -la /home/eric/.ssh
# Should show: drwx------ (700) for .ssh
#              -rw------- (600) for authorized_keys

# Fix if needed
chmod 700 /home/eric/.ssh
chmod 600 /home/eric/.ssh/authorized_keys
chown -R eric:eric /home/eric/.ssh
```

**Verify correct key**:
```bash
# On laptop
ssh-keygen -lf ~/.ssh/id_ed25519.pub

# Compare with 1Password
op read "op://Homelab/SSH Key/public key" | ssh-keygen -lf /dev/stdin
```

### Ansible Cannot Connect

**Symptom**: `UNREACHABLE! => {"changed": false, "msg": "Failed to connect", "unreachable": true}`

**Check inventory**:
```bash
# Verify hostname, IP, and user are correct
cat ansible/inventories/prod/hosts.yml

# Test connection
ansible <hostname> -m ping -vvv
```

**Common fixes**:
```bash
# Wrong user in inventory
# All hosts should use: ansible_user: eric

# Ensure ansible_become is set (usually inherited from group_vars/all.yml)
```

### Python Not Found

**Symptom**: `/bin/sh: 1: /usr/bin/python3: not found`

**Fix**:
```bash
# On container
apt update
apt install -y python3

# Verify
python3 --version
```

### Container Won't Start

**Symptom**: `pct start <VMID>` fails

**Check**:
```bash
# View container config
pct config <VMID>

# Check Proxmox logs
journalctl -u pve-container@<VMID> -n 50

# Common issue: Storage full
pvesm status
```

### Network Not Working in Container

**Symptom**: Cannot ping gateway or DNS

**Check**:
```bash
# On container
ip addr show
ip route show
cat /etc/resolv.conf

# Verify gateway
ping 192.168.0.1

# Verify DNS
ping 192.168.0.150
```

**Fix network**:
```bash
# On Proxmox host, reconfigure container network
pct set <VMID> --net0 name=eth0,bridge=vmbr0,ip=<IP>/24,gw=192.168.0.1

# Restart container
pct stop <VMID>
pct start <VMID>
```

### User 'eric' Cannot Sudo

**Symptom**: `eric is not in the sudoers file`

**Fix**:
```bash
# Use console access via Proxmox UI (pct enter for LXC)
pct enter <VMID>

# Add sudo access
echo 'eric ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric

# Test
su - eric
sudo whoami  # Should return 'root'
```

---

## Quick Reference

`<proxmox_lxc_template>` is the pin in `group_vars/all.yml`; `<storage>` is
`local-ssd` on the compute hosts and `ssd` on pve-nas-01 (never `local-lvm`).

### LXC Bootstrap (Minimal Steps)

```bash
# 1. Create container
pct create <VMID> local:vztmpl/<proxmox_lxc_template> \
  --hostname <name> --net0 name=eth0,bridge=vmbr0,ip=<IP>/24,gw=192.168.0.1 \
  --nameserver 192.168.0.150 --unprivileged 1 --start 1

# 2. Configure SSH and user
pct enter <VMID>
apt update && apt install -y openssh-server sudo
useradd -m -s /bin/bash -G sudo eric
mkdir -p /home/eric/.ssh && chmod 700 /home/eric/.ssh
cat > /home/eric/.ssh/authorized_keys <<EOF
<paste SSH public key>
EOF
chmod 600 /home/eric/.ssh/authorized_keys
chown -R eric:eric /home/eric/.ssh
echo 'eric ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric
exit

# 3. Test SSH
ssh eric@<IP>

# 4. Add to inventory and deploy
ansible <hostname> -m ping
ansible-playbook ansible/playbooks/base.yml --limit <hostname>
```

### VM Bootstrap with Cloud-Init (Minimal Steps)

```bash
# 1. Import cloud image
qm create <VMID> --name <hostname> --memory 2048 --cores 2 --net0 virtio,bridge=vmbr0
qm importdisk <VMID> debian-13-generic-amd64.qcow2 <storage>
qm set <VMID> --scsihw virtio-scsi-pci --scsi0 <storage>:vm-<VMID>-disk-0

# 2. Configure cloud-init
qm set <VMID> --ide2 <storage>:cloudinit
qm set <VMID> --boot c --bootdisk scsi0
qm set <VMID> --ipconfig0 ip=<IP>/24,gw=192.168.0.1
qm set <VMID> --sshkey ~/.ssh/id_ed25519.pub
qm set <VMID> --ciuser eric

# 3. Start VM
qm start <VMID>

# 4. Wait for cloud-init, then test
ssh eric@<IP>

# 5. Add to inventory and deploy
ansible <hostname> -m ping
ansible-playbook ansible/playbooks/base.yml --limit <hostname>
```

---

## Next Steps

After successfully bootstrapping and deploying base configuration:

1. **Deploy Service-Specific Configuration**:
   - Run appropriate playbooks for the service role
   - Example: `ansible-playbook ansible/playbooks/k3s.yml --limit k3s-srv-nas-01`

2. **Configure Application**:
   - Deploy application-specific roles
   - Configure service settings

3. **Verify Everything Works**:
   - Run `task infra:verify`
   - Test service-specific functionality

4. **Document in Cluster Status**:
   - Update relevant documentation
   - Record in inventory
   - Note any special configuration

---

## Related documentation

- [docs/03 — SSH and users](03-ssh-users.md) (what the base role hardens, and how to verify it)
- [docs/12 — Runbooks](12-runbooks.md) (operational procedures, incl. the LXC template rotation warning)
- [docs/01 — Overview](01-overview.md) (IP allocation before you pick an address)
- [docs/32 — ZFS encryption](32-zfs-encryption.md) (why guest storage choice matters)

## External references

- [Proxmox LXC Documentation](https://pve.proxmox.com/wiki/Linux_Container)
- [Proxmox Cloud-Init Support](https://pve.proxmox.com/wiki/Cloud-Init_Support)
- [Ansible Connection Methods](https://docs.ansible.com/ansible/latest/user_guide/connection_details.html)
