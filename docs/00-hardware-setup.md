# Hardware Setup and Proxmox Installation

This guide walks you through setting up bare metal hardware to a production-ready Proxmox cluster, ready for Ansible automation.

## Table of Contents

1. [Hardware Requirements](#hardware-requirements)
2. [Hardware Inventory](#hardware-inventory)
3. [UEFI/BIOS Configuration](#uefibios-configuration)
4. [Proxmox VE Installation](#proxmox-ve-installation)
5. [Initial Proxmox Configuration](#initial-proxmox-configuration)
6. [Storage Pool Setup](#storage-pool-setup)
7. [Pre-Ansible Checklist](#pre-ansible-checklist)

## Hardware Requirements

### Minimum Requirements (per node)

**Proxmox Host**:
- CPU: 64-bit x86 processor with virtualization support (Intel VT-x or AMD-V)
- RAM: 4GB minimum, 8GB+ recommended
- Storage: 32GB for OS, additional storage for VMs/containers
- Network: Gigabit Ethernet minimum

**NAS Node** (pve-nas-01 equivalent):
- CPU: Modern multi-core processor (i7 or better recommended)
- RAM: 32GB+ for ZFS (64GB recommended)
- Storage:
  - 1x NVMe for fast tier/downloads
  - 1x SSD for app data
  - Multiple HDDs for bulk storage pool
  - Optional: Additional HDDs for archive pool
- Network: Gigabit Ethernet (10GbE recommended for production)

**Compute Nodes** (OptiPlex-class):
- CPU: Quad-core or better
- RAM: 16GB minimum (32GB for k3s workloads)
- Storage: 120GB SSD minimum for OS
- Network: Gigabit Ethernet

### Recommended Hardware Configuration

The optimal homelab setup includes:
- 1x NAS node (high RAM, multiple storage devices)
- 3-5x Compute nodes (for HA and workload distribution)
- Managed network switch with VLAN support
- UPS for clean shutdowns during power events

## Hardware Inventory

This homelab currently consists of:

### NAS Node: pve-nas-01

- **Hostname**: pve-nas-01
- **IP Address**: 192.168.0.102
- **CPU**: Intel i7-12700K (12 cores, 20 threads)
- **RAM**: 64GB DDR5
- **Storage**:
  - NVMe pool (fast tier): Downloads and hot media staging
  - SSD pool: Application data and VM images
  - HDD pool (tank): Bulk media storage
  - HDD pool (archive): Long-term archival storage
- **Network**: Gigabit Ethernet
- **Role**: Primary storage server, NFS/Samba exports, ZFS management

### Compute Nodes: Dell OptiPlex 780 (3 units)

- **Models**: Dell OptiPlex 780 (opt-01, opt-02, opt-03)
- **IP Addresses**:
  - pve-opt-01: 192.168.0.104
  - pve-opt-02: 192.168.0.105
  - pve-opt-03: 192.168.0.106
- **CPU**: Intel Core2Quad
- **RAM**: 16GB DDR3
- **Storage**:
  - OS: 120GB SSD
  - Additional: 1TB Samsung 870 EVO (installed, local-ssd ZFS pool)
- **Network**: Gigabit Ethernet
- **Role**: General compute, k3s workers, LXC containers

### Laptop Node: MSI GS60 2QD

- **Hostname**: pve-laptop-01
- **IP Address**: 192.168.0.103
- **CPU**: Intel Core i7-5700HQ @ 2.70GHz (4 cores / 8 threads, Broadwell)
- **RAM**: 16GB
- **Storage**:
  - OS: 128GB Toshiba THNSNJ128G8NU SSD
  - Additional: 1TB Samsung 870 EVO (installed, local-ssd ZFS pool)
- **Network**: Qualcomm Atheros Killer E220x Gigabit Ethernet (WiFi available but unused)
- **Role**: Compute node, k3s server + agent

### Compute Node: Dell Precision 3630

- **Hostname**: pve-prec-01
- **IP Address**: 192.168.0.107
- **CPU**: Intel Core i7-8700K @ 3.70GHz (6 cores / 12 threads, Coffee Lake)
- **RAM**: 32GB
- **Storage**:
  - OS: 512GB Toshiba KXG60ZNV512G NVMe SSD
  - Additional: 1TB Samsung 870 EVO (installed, local-ssd ZFS pool)
- **Network**: Intel I219-LM Gigabit Ethernet
- **Role**: General compute, k3s server + agent, Home Assistant host

## UEFI/BIOS Configuration

Before installing Proxmox, configure BIOS/UEFI settings for optimal virtualization performance.

### Enable Virtualization Extensions

**Intel Systems**:
1. Enter BIOS/UEFI (usually F2, F12, or Del during boot)
2. Navigate to Advanced → CPU Configuration
3. Enable: **Intel Virtualization Technology (VT-x)**
4. Enable: **Intel VT-d** (for PCI passthrough)

**AMD Systems**:
1. Enter BIOS/UEFI
2. Navigate to Advanced → CPU Configuration
3. Enable: **AMD-V** (AMD Virtualization)
4. Enable: **IOMMU** (for PCI passthrough)

### Additional BIOS Settings

**Boot Configuration**:
- Boot mode: **UEFI** (not Legacy/CSM)
- Secure Boot: **Disabled** (can cause issues with Proxmox)
- Boot order: Set installation media first

**Power Management**:
- Enable: **Wake on LAN** (for remote power management)
- Disable: **Deep sleep states** if experiencing VM instability
- Set: **Power on after power failure** to "Last State" or "On"

**Storage Controller**:
- SATA mode: **AHCI** (not IDE or RAID)
- NVMe configuration: Default/Auto

### Verify Virtualization Support

After OS installation, verify virtualization is enabled:

```bash
# Check for virtualization support
egrep -c '(vmx|svm)' /proc/cpuinfo
# Should return > 0

# Verify VT-d/IOMMU (for PCI passthrough)
dmesg | grep -e DMAR -e IOMMU
```

## Proxmox VE Installation

### Download Proxmox VE ISO

1. Download latest Proxmox VE ISO from: https://www.proxmox.com/en/downloads
2. Verify checksum (SHA256)
3. Create bootable USB with Rufus (Windows) or dd (Linux/macOS):

```bash
# macOS/Linux example
sudo dd if=proxmox-ve_*.iso of=/dev/sdX bs=1M status=progress
sync
```

Replace `/dev/sdX` with your USB device (use `lsblk` to identify).

### Boot from Installation Media

1. Insert USB drive
2. Boot system and select USB from boot menu (F12, F8, or Esc depending on vendor)
3. Select "Install Proxmox VE" from boot menu

### Installation Wizard

**Target Disk**:
- Select installation target (usually your SSD)
- Filesystem: **ext4** (default) or **ZFS** (for advanced setups)
- For ZFS: Select RAID level (raidz1, mirror, etc.) if multiple disks
- **IMPORTANT**: This will erase the selected disk(s)

**Location and Time Zone**:
- Country: United States
- Time zone: America/Los_Angeles (or your local timezone)
- Keyboard layout: en-us

**Password and Email**:
- Root password: Use a strong password (store in 1Password)
- Email: your-email@example.com (for system notifications)

**Network Configuration**:
- Management interface: Select primary NIC (usually first Ethernet port)
- Hostname (FQDN): `pve-nas-01.esweiss.com`
- IP address: `192.168.0.102/24`
- Gateway: `192.168.0.1`
- DNS server: `192.168.0.1` (will change to internal DNS later)

**Confirm and Install**:
- Review settings
- Click "Install"
- Wait 5-10 minutes for installation to complete
- Remove installation media when prompted
- Reboot

### First Boot Verification

After reboot, you should see:

```
Welcome to the Proxmox Virtual Environment. Please use your web browser to configure this server.

  https://192.168.0.102:8006/

Login with user 'root' and the password you configured during installation.
```

Access the web UI from your workstation:
- URL: https://192.168.0.102:8006/
- Username: `root`
- Password: (password from installation)

## Initial Proxmox Configuration

### Update Package Repositories

Proxmox includes enterprise repositories by default. For homelab use, switch to no-subscription repositories.

**Via Web UI**:
1. Select node → Updates → Repositories
2. Disable `pve-enterprise` repository
3. Add `pve-no-subscription` repository

**Via SSH** (Proxmox 9+ / Debian Trixie uses DEB822 `.sources` format):

```bash
# Remove legacy .list files if they exist
rm -f /etc/apt/sources.list.d/pve-enterprise.list
rm -f /etc/apt/sources.list.d/ceph.list

# Remove the Ceph enterprise repo (also requires a subscription)
rm -f /etc/apt/sources.list.d/ceph.sources

# Disable enterprise repo (comment out Enabled line or set to no)
cat > /etc/apt/sources.list.d/pve-enterprise.sources << 'EOF'
# Proxmox VE Enterprise Repository (disabled - requires subscription)
Types: deb
URIs: https://enterprise.proxmox.com/debian/pve
Suites: trixie
Components: pve-enterprise
Enabled: no
EOF

# Add no-subscription repo
cat > /etc/apt/sources.list.d/pve-no-subscription.sources << 'EOF'
# Proxmox VE No-Subscription Repository
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
EOF

# Update and upgrade
apt update
apt full-upgrade -y
```

### Configure Network

**Check current network configuration**:

```bash
# View interfaces
ip addr show

# View routes
ip route show
```

**Edit network interfaces** (if needed):

```bash
nano /etc/network/interfaces
```

Basic configuration for single NIC:

```
auto lo
iface lo inet loopback

auto eno1
iface eno1 inet static
    address 192.168.0.102/24
    gateway 192.168.0.1

iface eno1 inet6 static
    address fe80::1/64
```

For Proxmox with bridge (for VMs/containers):

```
auto lo
iface lo inet loopback

auto eno1
iface eno1 inet manual

auto vmbr0
iface vmbr0 inet static
    address 192.168.0.102/24
    gateway 192.168.0.1
    bridge-ports eno1
    bridge-stp off
    bridge-fd 0
```

Apply changes:

```bash
systemctl restart networking
```

### SSH Configuration (Pre-Ansible)

**Enable root SSH access temporarily** (Ansible will harden this later):

```bash
# Edit SSH config
nano /etc/ssh/sshd_config

# Ensure these settings:
PermitRootLogin yes
PubkeyAuthentication yes
PasswordAuthentication yes  # Temporary, will be disabled by Ansible

# Restart SSH
systemctl restart sshd
```

**Copy your public key** (from workstation):

```bash
# From your laptop
ssh-copy-id root@192.168.0.102

# Test passwordless access
ssh root@192.168.0.102
```

### Create Admin User

Ansible playbooks expect a non-root user with sudo access. Create user `eric`:

```bash
# Create user
useradd -m -s /bin/bash eric

# Set password (temporary - SSH key auth will be enforced)
passwd eric

# Add to sudo group
usermod -aG sudo eric

# Configure passwordless sudo (temporary)
echo "eric ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/eric
chmod 440 /etc/sudoers.d/eric

# Copy SSH keys
mkdir -p /home/eric/.ssh
cp /root/.ssh/authorized_keys /home/eric/.ssh/
chown -R eric:eric /home/eric/.ssh
chmod 700 /home/eric/.ssh
chmod 600 /home/eric/.ssh/authorized_keys
```

**Test user access**:

```bash
# From laptop
ssh eric@192.168.0.102
sudo -v  # Should not prompt for password
```

### Configure Timezone and Locale

```bash
# Set timezone
timedatectl set-timezone America/Los_Angeles

# Verify
timedatectl status

# Configure locale
localectl set-locale LANG=en_US.UTF-8

# Update locale
update-locale LANG=en_US.UTF-8
```

### Update DNS Configuration

Point to internal DNS servers (once dns-01/dns-02 are operational):

```bash
# Edit resolv.conf
nano /etc/resolv.conf

# Set to internal DNS
nameserver 192.168.0.150
nameserver 192.168.0.160
search esweiss.com

# Make it persistent (prevents DHCP overwrite)
chattr +i /etc/resolv.conf
```

**Note**: For new installations, do this after DNS stack is deployed.

## Storage Pool Setup

Pool topology is host-specific — the exact creation commands for pve-nas-01's
pools (tank, ssd, nvme, archive) live in
[docs/06-zfs.md](06-zfs.md#current-cluster-configuration), and the compute-node
`local-ssd` pool is covered in the same doc. Do not improvise topology here;
recreate from docs/06.

General guidance that applies to all pools:

```bash
# Identify available disks
lsblk
fdisk -l

# Always reference disks by stable path, never /dev/sdX
ls -l /dev/disk/by-id/

# Always set ashift=12 at creation (4K sectors); it cannot be changed later
zpool create -o ashift=12 ...
```

After pool creation, register each pool as Proxmox storage (Web UI:
Datacenter → Storage → Add → ZFS, or `pvesm add zfspool`); the exact storage
IDs in use are listed in docs/06.

## Pre-Ansible Checklist

Before running Ansible automation, verify the following on each node.

### Automated Bootstrap (Recommended)

Use the bootstrap script to automate user creation, SSH key deployment, and sudo configuration:

```bash
# From your laptop, run the bootstrap script against the new host
./scripts/bootstrap-proxmox-host.sh <host-ip> <your-ssh-public-key>

# Example:
./scripts/bootstrap-proxmox-host.sh 192.168.0.107 "ssh-ed25519 AAAA... eric@laptop"
```

The script handles:
- Creating user `eric` with sudo group
- Deploying SSH authorized keys
- Configuring passwordless sudo
- Installing required packages (sudo if missing)

After bootstrap, verify SSH access: `ssh eric@<host-ip>`

### Manual Checklist (if not using bootstrap script)

#### Network and Connectivity

- [ ] Static IP configured and persistent
- [ ] Default gateway reachable (`ping 192.168.0.1`)
- [ ] Internet connectivity (`ping 8.8.8.8`)
- [ ] DNS resolution working (`nslookup google.com`)
- [ ] Hostname set correctly (`hostnamectl`)
- [ ] Hostname resolves to IP (`hostname -f`)

#### SSH Access

- [ ] SSH service running (`systemctl status sshd`)
- [ ] User `eric` created
- [ ] SSH key deployed for `eric`
- [ ] Passwordless SSH working from laptop (`ssh eric@<host>`)
- [ ] Passwordless sudo configured (`sudo -v`)
- [ ] SSH access available for `eric` user with passwordless sudo

### System Updates

- [ ] Package repositories configured (no-subscription repo enabled)
- [ ] System updated (`apt update && apt full-upgrade`)
- [ ] System rebooted to latest kernel

### Storage (NAS node only)

- [ ] ZFS pools created and healthy (`zpool status`)
- [ ] Datasets created (`zfs list`)
- [ ] Pools added to Proxmox storage (Web UI → Datacenter → Storage)

### Proxmox Cluster

- [ ] Proxmox VE version 9.0+ (`pveversion`)
- [ ] Cluster created (if HA) or node standalone
- [ ] Web UI accessible (https://<node-ip>:8006)
- [ ] Time synchronized across nodes (`timedatectl`)

### Verification Commands

Run these commands to verify readiness:

```bash
# On each node
ssh eric@192.168.0.102 << 'EOF'
echo "=== Node: $(hostname -f) ==="
echo "IP: $(hostname -I)"
echo "Timezone: $(timedatectl | grep 'Time zone')"
echo "DNS: $(grep nameserver /etc/resolv.conf | head -2)"
echo "Internet: $(ping -c 1 8.8.8.8 &>/dev/null && echo OK || echo FAIL)"
echo "Proxmox: $(pveversion -v | head -1)"
echo "ZFS: $(zpool list 2>/dev/null | wc -l) pools"
echo ""
EOF
```

### Ansible Connectivity Test

From your laptop:

```bash
cd /path/to/weisssrv
task ansible:ping
```

Expected output:

```
pve-nas-01 | SUCCESS => {"changed": false, "ping": "pong"}
pve-opt-03 | SUCCESS => {"changed": false, "ping": "pong"}
...
```

## Next Steps

Once all nodes pass the pre-Ansible checklist:

1. Proceed to [02-install.md](02-install.md) for Ansible deployment
2. Configure base services (SSH hardening, packages, QoL)
3. Deploy infrastructure services (DNS, storage, mail relay)
4. Configure Proxmox firewall and Tailscale VPN

Your bare metal hardware is now ready for full GitOps automation.
