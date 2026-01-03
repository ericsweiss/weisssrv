# Bootstrapping New Systems for Ansible Automation

This guide covers preparing new LXC containers and VMs for Ansible automation. Follow these procedures to bring a fresh system from initial creation to fully automated management.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [LXC Container Bootstrap](#lxc-container-bootstrap)
4. [VM Bootstrap (Future)](#vm-bootstrap-future)
5. [What the Base Role Expects](#what-the-base-role-expects)
6. [Validation](#validation)
7. [Troubleshooting](#troubleshooting)

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
| **LXC Containers** | `root` | Pubkey auth | Manual (documented below) |
| **VMs (future)** | `eric` | Pubkey auth | Cloud-init (minimal manual) |

---

## Prerequisites

Before bootstrapping any system, ensure you have:

### Network Planning

- **IP Address**: Static IP allocated (avoid DHCP conflicts)
- **Gateway**: 192.168.0.1 (router)
- **DNS Servers**: 192.168.0.150, 192.168.0.160 (dns-01, dns-02)
- **Hostname**: Follows naming convention (e.g., `app-01`, `k3s-master-01`)

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

```bash
# Download Debian 13 (trixie) template
pveam update
pveam available | grep debian-13

# Download the template
pveam download local debian-13-standard_13.0-1_amd64.tar.zst
```

Verify download:
```bash
ls -lh /var/lib/vz/template/cache/
```

### Step 2: Create LXC Container

Choose a VMID (container ID) that doesn't conflict with existing containers. Check existing IDs:

```bash
pct list
```

**Create unprivileged container** (recommended for security):

```bash
pct create <VMID> \
  local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname <hostname> \
  --net0 name=eth0,bridge=vmbr0,ip=<IP>/24,gw=192.168.0.1 \
  --nameserver 192.168.0.150 \
  --nameserver 192.168.0.160 \
  --storage local-lvm \
  --rootfs local-lvm:8 \
  --cores 2 \
  --memory 2048 \
  --swap 512 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 1
```

**Parameter Notes**:
- `<VMID>`: Container ID (e.g., 201, 202)
- `<hostname>`: DNS name (e.g., k3s-master-01)
- `<IP>`: Static IP (e.g., 192.168.0.201)
- `--rootfs local-lvm:8`: 8GB root filesystem (adjust as needed)
- `--unprivileged 1`: Run as unprivileged (security best practice)
- `--features nesting=1`: Enable nested containers (required for Docker/K8s)
- `--onboot 1`: Start on Proxmox boot
- `--start 1`: Start immediately after creation

**Example**:
```bash
pct create 201 \
  local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname k3s-master-01 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.0.201/24,gw=192.168.0.1 \
  --nameserver 192.168.0.150 \
  --nameserver 192.168.0.160 \
  --storage local-lvm \
  --rootfs local-lvm:16 \
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

# Install OpenSSH server
apt install -y openssh-server

# Create SSH directory for root
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Add your SSH public key
cat > /root/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... eric@MacBookPro.esweiss.com
EOF

# Set proper permissions
chmod 600 /root/.ssh/authorized_keys

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
ssh root@<IP>
```

If successful, you should connect without a password prompt.

### Step 5: Add to Ansible Inventory

Edit the inventory file to add the new container:

```bash
# ansible/inventories/prod/hosts.yml
```

Add to appropriate group:

```yaml
# Example: Adding a k3s master node
k3s_masters:
  hosts:
    k3s-master-01:
      ansible_host: 192.168.0.201
      ansible_user: root
```

Or for a standalone service:

```yaml
# Example: Adding a monitoring service
monitoring:
  hosts:
    grafana-01:
      ansible_host: 192.168.0.210
      ansible_user: root
```

### Step 6: Create Host Variables (Optional)

If the host needs specific configuration:

```bash
# ansible/inventories/prod/host_vars/k3s-master-01.yml
---
# Host-specific variables
k3s_role: master
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
ansible k3s-master-01 -m ping
```

Expected output:
```
k3s-master-01 | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

### Step 9: Deploy Base Configuration

Run Ansible to configure the system:

```bash
# Check what would change (dry run)
task deploy:check -- --limit k3s-master-01

# Deploy base configuration
ansible-playbook ansible/playbooks/base.yml --limit k3s-master-01

# Or deploy everything for this host
ansible-playbook ansible/playbooks/site.yml --limit k3s-master-01
```

### Step 10: Verify Deployment

Run post-deployment verification:

```bash
# Full verification playbook
ansible-playbook ansible/playbooks/postflight.yml --limit k3s-master-01
```

Or manually verify:

```bash
# SSH should still work
ssh root@192.168.0.201

# Check services
systemctl status ssh
systemctl status tailscale  # If configured

# Verify timezone
timedatectl

# Check common packages
which htop neovim git
```

---

## VM Bootstrap (Future)

When deploying VMs (planned for k3s nodes):

### Cloud-Init Method (Recommended)

1. **Prepare Cloud Image**:
   ```bash
   # Download Debian cloud image
   wget https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2

   # Move to Proxmox storage
   qm importdisk <VMID> debian-13-generic-amd64.qcow2 local-lvm
   ```

2. **Configure Cloud-Init**:
   ```bash
   qm set <VMID> --ide2 local-lvm:cloudinit
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

The `base` role (first role applied to all systems) expects these minimum prerequisites:

### Must Exist Before Base Role

1. **SSH Server Running**:
   - Package `openssh-server` installed
   - Service `ssh` enabled and started

2. **Network Configured**:
   - Static IP assigned and reachable
   - Default gateway configured
   - DNS servers set

3. **Initial User Access**:
   - **LXC**: Root SSH access with key authentication
   - **VM/Proxmox**: User `eric` exists with SSH key and sudo access

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
- [ ] SSH key deployed to root (LXC) or eric (VM)
- [ ] Can SSH from laptop without password
- [ ] Added to Ansible inventory
- [ ] Firewall rules created (`/etc/pve/firewall/<VMID>.fw`)

### Post-Bootstrap Verification

After running the base role:

```bash
# 1. Test Ansible connectivity
ansible <hostname> -m ping

# 2. Test SSH access
ssh root@<IP>  # For LXC
ssh eric@<IP>  # For VM

# 3. Verify sudo (VMs only)
ansible <hostname> -m shell -a "sudo whoami"  # Should return 'root'

# 4. Check base packages installed
ansible <hostname> -m shell -a "which htop neovim git"

# 5. Verify timezone
ansible <hostname> -m shell -a "timedatectl | grep 'Los_Angeles'"

# 6. Check SSH hardening
ansible <hostname> -m shell -a "grep PasswordAuthentication /etc/ssh/sshd_config"
# Should show: PasswordAuthentication no

# 7. Run full verification
ansible-playbook ansible/playbooks/postflight.yml --limit <hostname>
```

### Common Issues Checklist

If verification fails:

- [ ] Can ping the IP address?
- [ ] SSH service running? (`systemctl status ssh`)
- [ ] Correct SSH key deployed?
- [ ] Firewall blocking access?
- [ ] Correct user in inventory (root for LXC, eric for VM)?
- [ ] Sudo configured (VMs only)?
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
cat /root/.ssh/authorized_keys

# Verify permissions
ls -la /root/.ssh
# Should show: drwx------ (700) for .ssh
#              -rw------- (600) for authorized_keys

# Fix if needed
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
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
# LXC should use: ansible_user: root
# VM should use: ansible_user: eric

# Missing ansible_become for VMs
# Add: ansible_become: true
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

### User 'eric' Cannot Sudo (VMs)

**Symptom**: `eric is not in the sudoers file`

**Fix**:
```bash
# SSH as eric
ssh eric@<IP>

# Temporarily become root (if you have root password)
su -

# Or use console access via Proxmox UI

# Add sudo access
echo 'eric ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric

# Test
exit  # Back to eric user
sudo whoami  # Should return 'root'
```

---

## Quick Reference

### LXC Bootstrap (Minimal Steps)

```bash
# 1. Create container
pct create <VMID> local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname <name> --net0 name=eth0,bridge=vmbr0,ip=<IP>/24,gw=192.168.0.1 \
  --nameserver 192.168.0.150 --unprivileged 1 --start 1

# 2. Configure SSH
pct enter <VMID>
apt update && apt install -y openssh-server
mkdir -p /root/.ssh && chmod 700 /root/.ssh
cat > /root/.ssh/authorized_keys <<EOF
<paste SSH public key>
EOF
chmod 600 /root/.ssh/authorized_keys
exit

# 3. Test SSH
ssh root@<IP>

# 4. Add to inventory and deploy
ansible <hostname> -m ping
ansible-playbook ansible/playbooks/base.yml --limit <hostname>
```

### VM Bootstrap with Cloud-Init (Minimal Steps)

```bash
# 1. Import cloud image
qm create <VMID> --name <hostname> --memory 2048 --cores 2 --net0 virtio,bridge=vmbr0
qm importdisk <VMID> debian-13-generic-amd64.qcow2 local-lvm
qm set <VMID> --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-<VMID>-disk-0

# 2. Configure cloud-init
qm set <VMID> --ide2 local-lvm:cloudinit
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
   - Example: `ansible-playbook ansible/playbooks/k3s.yml --limit k3s-master-01`

2. **Configure Application**:
   - Deploy application-specific roles
   - Configure service settings

3. **Verify Everything Works**:
   - Run `task deploy:verify`
   - Test service-specific functionality

4. **Document in Cluster Status**:
   - Update relevant documentation
   - Record in inventory
   - Note any special configuration

---

## References

- [Proxmox LXC Documentation](https://pve.proxmox.com/wiki/Linux_Container)
- [Proxmox Cloud-Init Support](https://pve.proxmox.com/wiki/Cloud-Init_Support)
- [Ansible Connection Methods](https://docs.ansible.com/ansible/latest/user_guide/connection_details.html)
- [docs/03-ssh-users.md](03-ssh-users.md) - SSH and user management details
- [docs/12-runbooks.md](12-runbooks.md) - Operational procedures
