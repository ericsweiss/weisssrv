# Multi-Node Expansion and Proxmox HA Guide

This document covers the full path from the current 2-host setup to a multi-node Proxmox HA cluster with k3s running across all hosts. It is organized into three phases: adding local storage to pve-opt-03, bringing up new Proxmox hosts with k3s nodes, and enabling Proxmox HA for infrastructure services.

## Table of Contents

1. [Current State](#current-state)
2. [IP and VMID Allocation Scheme](#ip-and-vmid-allocation-scheme)
3. [Node Labels and Taints](#node-labels-and-taints)
4. [Section 1: Adding local-ssd to pve-opt-03](#section-1-adding-local-ssd-to-pve-opt-03)
5. [Section 2: Setting Up New Proxmox Hosts](#section-2-setting-up-new-proxmox-hosts)
6. [Section 3: Enabling Proxmox HA](#section-3-enabling-proxmox-ha)
7. [Network Diagram](#network-diagram)
8. [Related Documentation](#related-documentation)

---

## Current State

**Active Proxmox hosts**:
- pve-nas-01 (192.168.0.102) -- NAS + storage, ZFS pools (tank/ssd/nvme/archive)
- pve-opt-03 (192.168.0.106) -- Compute only, local-lvm storage

**Active k3s nodes**:
- k3s-srv-nas-01 (.222, VMID 222) -- Server + etcd on pve-nas-01
- k3s-agt-nas-01 (.202, VMID 202) -- Agent on pve-nas-01 (NAS workloads)
- k3s-agt-opt-03 (.206, VMID 206) -- Agent on pve-opt-03 (ingress + general)

**Infrastructure LXC containers** (all on pve-nas-01):
- dns-01 (VMID 150, .150) -- Primary DNS
- dns-02 (VMID 160, .160) -- Secondary DNS
- smtp-relay (VMID 151, .151) -- Mail relay
- plex (VMID 152, .152) -- Plex Media Server

**VMs**:
- home-assistant (VMID 154, .154) -- HAOS VM on pve-nas-01

---

## IP and VMID Allocation Scheme

### Design Principles

- **Servers (.22X range)**: Control plane nodes in 192.168.0.220/29, VMIDs match last octet
- **Agents (.20X range)**: Worker nodes in 192.168.0.200/29, VMIDs match last octet
- **Proxmox hosts (.10X range)**: Physical hosts use 192.168.0.102-109

### Proxmox Host Allocation

| Host | IP | Status | Hardware | K3s VMs |
|------|-----|--------|----------|---------|
| pve-nas-01 | .102 | Active | NAS + Storage | srv-nas-01 (.222), agt-nas-01 (.202) |
| pve-laptop-01 | .103 | Future | MSI GS60 2QD | srv-laptop-01 (.223), agt-laptop-01 (.203) |
| pve-opt-01 | .104 | Future | Dell OptiPlex 780 | agt-opt-01 (.204) -- agent only |
| pve-opt-02 | .105 | Future | Dell OptiPlex 780 | agt-opt-02 (.205) -- agent only |
| pve-opt-03 | .106 | Active | Dell OptiPlex 780 | agt-opt-03 (.206) |
| pve-prec-01 | .107 | Future | Dell Precision 3630 | srv-prec-01 (.227), agt-prec-01 (.207) |

### K3s Server Nodes (.22X -- Control Plane)

| Node | IP | VMID | Host | Storage | Status |
|------|-----|------|------|---------|--------|
| k3s-srv-nas-01 | .222 | 222 | pve-nas-01 | local-lvm | Active |
| k3s-srv-laptop-01 | .223 | 223 | pve-laptop-01 | local-ssd | Future |
| k3s-srv-prec-01 | .227 | 227 | pve-prec-01 | TBD | Future |
| (reserved) | .224 | 224 | TBD | TBD | Future (5-node HA) |
| (reserved) | .225 | 225 | TBD | TBD | Future (5-node HA) |

### K3s Agent Nodes (.20X -- Workers)

| Node | IP | VMID | Host | Role | Status |
|------|-----|------|------|------|--------|
| k3s-agt-nas-01 | .202 | 202 | pve-nas-01 | NAS workloads | Active |
| k3s-agt-laptop-01 | .203 | 203 | pve-laptop-01 | Ingress + general | Future |
| k3s-agt-opt-01 | .204 | 204 | pve-opt-01 | General | Future |
| k3s-agt-opt-02 | .205 | 205 | pve-opt-02 | General | Future |
| k3s-agt-opt-03 | .206 | 206 | pve-opt-03 | Ingress + general | Active |
| k3s-agt-prec-01 | .207 | 207 | pve-prec-01 | General + compute | Future |

---

## Node Labels and Taints

### Labels

All labels use the `esweiss.com/` prefix:

| Label | Purpose | Nodes |
|-------|---------|-------|
| `esweiss.com/nas=true` | Fast NAS storage access (local NFS) | k3s-agt-nas-01 |
| `esweiss.com/general=true` | General workloads | All agents |
| `esweiss.com/ingress=true` | Ingress controller eligible | agt-opt-03, agt-laptop-01 |
| `esweiss.com/compute=true` | High-computation tasks (ML, transcoding) | k3s-agt-prec-01 |
| `esweiss.com/control-plane=true` | Informational: control plane node | All servers |

### Taints

| Node | Taint Key | Value | Effect | Purpose |
|------|-----------|-------|--------|---------|
| All servers | `node-role.kubernetes.io/control-plane` | `true` | NoSchedule | Reserve for etcd + API |
| k3s-agt-nas-01 | `esweiss.com/nas` | `true` | PreferNoSchedule | Prefer NAS workloads, allow overflow |
| k3s-agt-laptop-01 | `esweiss.com/ingress` | `true` | PreferNoSchedule | Prefer ingress, allow general overflow |
| k3s-agt-prec-01 | `esweiss.com/compute` | `true` | PreferNoSchedule | Prefer compute workloads, allow general overflow |

### Using the "compute" Label

The `esweiss.com/compute=true` label on k3s-agt-prec-01 is for workloads that benefit from higher CPU/RAM. Because the node has a `PreferNoSchedule` taint, compute workloads should include a toleration:

```yaml
# Hard requirement: must run on compute node
spec:
  template:
    spec:
      nodeSelector:
        esweiss.com/compute: "true"
      tolerations:
        - key: esweiss.com/compute
          operator: Equal
          value: "true"
          effect: PreferNoSchedule
```

```yaml
# Soft preference: prefer compute node, fall back to general
spec:
  template:
    spec:
      affinity:
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: esweiss.com/compute
                    operator: In
                    values:
                      - "true"
      tolerations:
        - key: esweiss.com/compute
          operator: Equal
          value: "true"
          effect: PreferNoSchedule
```

**Candidate compute workloads**: Immich ML/face recognition, video transcoding jobs, database-intensive operations, CI/CD runners, batch processing, ML inference.

General workloads without the toleration will still schedule on prec-01 because the taint is `PreferNoSchedule` (soft), not `NoSchedule` (hard). The scheduler will prefer other nodes first but will use prec-01 as overflow when other agents are full.

---

## Section 1: Adding local-ssd to pve-opt-03

pve-opt-03 currently uses `local-lvm` for VM storage. Adding a 1TB SSD with a `local-ssd` ZFS pool provides better snapshots, compression, and prepares the node for Proxmox HA replication.

### Prerequisites

- 1x 1TB Samsung 870 EVO SSD installed in pve-opt-03
- Physical access to install the drive (or hot-swap if chassis supports it)

### Step 1: Identify the Disk

```bash
# SSH to pve-opt-03
ssh eric@192.168.0.106

# List all block devices
sudo lsblk -d -o NAME,SIZE,MODEL,SERIAL,ROTA
# The new SSD should show ROTA=0 (non-rotational)

# Get the by-id path (required for ZFS)
ls -la /dev/disk/by-id/ | grep -i samsung
# Example output: ata-Samsung_SSD_870_EVO_1TB_S6PENX0T123456A -> ../../sdb
```

**IMPORTANT**: Note the full `/dev/disk/by-id/` path. Never use `/dev/sdX` names directly -- they can change between reboots.

### Step 2: Create the ZFS Pool

Follow the same property conventions as the NAS pools (see `docs/06-zfs.md`):

```bash
# Create local-ssd pool (single device, no redundancy)
sudo zpool create -o ashift=12 \
    -O acltype=posixacl \
    -O compression=zstd \
    -O normalization=formD \
    -O relatime=on \
    -O xattr=sa \
    -m /mnt/local-ssd \
    local-ssd \
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER

# Verify pool creation
sudo zpool status local-ssd
sudo zpool list -v local-ssd
```

**Pool properties match the established pattern**:
- `ashift=12`: 4K sector alignment
- `compression=zstd`: Same as all other pools
- `relatime=on`: Reduced atime writes
- `xattr=sa`: Extended attributes in system attribute table

**WARNING**: Single device pool has no redundancy. This is acceptable for compute nodes because VM data is replicated via Proxmox HA and k3s workloads are stateless or use NFS-backed PVs from the NAS.

### Step 3: Register as Proxmox Storage

```bash
# Add ZFS pool as Proxmox storage
sudo pvesm add zfspool local-ssd --pool local-ssd --content images,rootdir

# Verify it appears in storage list
sudo pvesm status
```

You can also verify in the Proxmox web UI: **Datacenter > Storage** -- `local-ssd` should appear with content types "Disk image, Container".

### Step 4: Test the Pool

```bash
# Create a test dataset and destroy it
sudo zfs create local-ssd/test
sudo zfs list local-ssd/test
sudo zfs destroy local-ssd/test

# Check I/O performance (quick benchmark)
sudo dd if=/dev/zero of=/mnt/local-ssd/testfile bs=1M count=1024 oflag=direct
sudo rm /mnt/local-ssd/testfile

# Verify compression is active
sudo zfs get compression,compressratio local-ssd
```

### Step 5: Migrate Existing VMs (Optional)

If you want to move k3s-agt-opt-03 from `local-lvm` to `local-ssd`:

```bash
# From the Proxmox web UI:
# 1. Select VM 206 (k3s-agt-opt-03)
# 2. Hardware > Hard Disk > Disk Action > Move Storage
# 3. Target Storage: local-ssd
# 4. Check "Delete Source"

# Or via CLI:
sudo qm move-disk 206 scsi0 local-ssd --delete
```

After migration, update the inventory to reflect the new storage:

```yaml
# ansible/inventories/prod/hosts.yml
k3s-agt-opt-03:
  proxmox_storage: local-ssd  # was: local-lvm
```

---

## Section 2: Setting Up New Proxmox Hosts

This section covers bringing each future host from bare hardware to a fully integrated Proxmox cluster member with k3s nodes.

### General Procedure (All Hosts)

Each new host follows the same workflow. Host-specific details are in the subsections below.

#### Step 1: Proxmox Installation

Follow `docs/00-hardware-setup.md` for the full Proxmox installation procedure. The key steps are:

1. Flash Proxmox VE ISO to USB
2. Configure BIOS (enable VT-x/VT-d, UEFI boot, Wake on LAN, power-on after failure)
3. Install Proxmox VE with the host's assigned static IP
4. Configure no-subscription repositories
5. Create user `eric` with SSH key and passwordless sudo
6. Verify SSH access: `ssh eric@<host-ip>`

**Network configuration during install**:

| Host | IP | FQDN |
|------|-----|------|
| pve-laptop-01 | 192.168.0.103/24 | pve-laptop-01.esweiss.com |
| pve-opt-01 | 192.168.0.104/24 | pve-opt-01.esweiss.com |
| pve-opt-02 | 192.168.0.105/24 | pve-opt-02.esweiss.com |
| pve-prec-01 | 192.168.0.107/24 | pve-prec-01.esweiss.com |

Gateway: 192.168.0.1, DNS: 192.168.0.150 (use 192.168.0.1 if DNS stack is not yet deployed).

#### Step 2: Create local-ssd ZFS Pool

For hosts with a dedicated 1TB SSD (all except pve-prec-01 where storage is TBD):

```bash
# Identify the SSD
ls -la /dev/disk/by-id/ | grep -i samsung

# Create pool (replace SERIALNUMBER with actual serial)
sudo zpool create -o ashift=12 \
    -O acltype=posixacl \
    -O compression=zstd \
    -O normalization=formD \
    -O relatime=on \
    -O xattr=sa \
    -m /mnt/local-ssd \
    local-ssd \
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER

# Register as Proxmox storage
sudo pvesm add zfspool local-ssd --pool local-ssd --content images,rootdir

# Verify
sudo zpool status local-ssd
sudo pvesm status
```

#### Step 3: Join the Proxmox Cluster

**IMPORTANT**: Join the cluster BEFORE deploying any VMs. Cluster join requires a fresh node with no VMs/containers.

```bash
# On the NEW node, join the existing cluster
# Get the join command from an existing cluster member first:
ssh eric@192.168.0.102 "sudo pvecm create weisssrv-cluster"  # only if cluster does not exist yet

# On an existing member, get the join info:
ssh eric@192.168.0.102 "sudo pvecm status"

# On the NEW node:
sudo pvecm add 192.168.0.102
# This will prompt for the root password of the existing node
# The node will reboot into the cluster
```

After joining, verify from any cluster member:

```bash
sudo pvecm status
sudo pvecm nodes
```

The new node should appear in the Proxmox web UI under **Datacenter > Cluster**.

#### Step 4: Ansible Integration

```bash
# 1. Move the host from proxmox_unmanaged to proxmox group in hosts.yml
# Edit ansible/inventories/prod/hosts.yml

# 2. Deploy base configuration
task deploy:base -- --limit pve-laptop-01

# 3. Deploy full stack (firewall, tailscale, etc.)
task deploy:all -- --limit pve-laptop-01

# 4. Verify
task ansible:ping
ssh eric@<host-ip> "systemctl status tailscaled"
```

#### Step 5: K3s VM Provisioning

```bash
# 1. Uncomment the k3s nodes in hosts.yml for this host

# 2. Provision VMs
task k3s:provision-vms -- --limit k3s-srv-laptop-01,k3s-agt-laptop-01

# 3. Deploy k3s (joins existing cluster)
task k3s:deploy -- --limit k3s-srv-laptop-01,k3s-agt-laptop-01

# 4. Verify
kubectl get nodes
kubectl get nodes --show-labels
kubectl describe node k3s-agt-laptop-01 | grep -A 5 Taints
```

#### Step 6: Verification

```bash
# Full cluster health check
task k3s:status

# Verify the new node is scheduling pods
kubectl get pods -A -o wide | grep <new-node>

# Check etcd health (if server node was added)
kubectl get pods -n kube-system -l component=etcd

# Test API VIP is still working
curl -sk https://192.168.0.161:6443/healthz
```

### Host-Specific Notes

#### pve-laptop-01 (MSI GS60 2QD)

- **IP**: 192.168.0.103
- **K3s nodes**: k3s-srv-laptop-01 (.223/223) + k3s-agt-laptop-01 (.203/203)
- **Storage**: 1TB SSD as `local-ssd`
- **Agent role**: Ingress + general with `PreferNoSchedule` taint
- **Notes**:
  - Ensure laptop is configured for "lid closed = do nothing" in power settings
  - Set BIOS to power on after AC restore
  - WiFi should be disabled; use wired Ethernet only
  - Battery may need to be removed for long-term server use to prevent swelling

#### pve-opt-01 and pve-opt-02 (Dell OptiPlex 780)

- **IPs**: .104 and .105
- **K3s nodes**: Agent only (k3s-agt-opt-01 at .204/204, k3s-agt-opt-02 at .205/205)
- **Storage**: 1TB SSD as `local-ssd`
- **Agent role**: General workloads (no ingress, no NAS, no compute)
- **Notes**:
  - No server VM on these hosts -- too resource-constrained for etcd overhead
  - 16GB DDR3 RAM limits VM sizes; allocate conservatively
  - These are the simplest nodes to add -- single agent VM each

#### pve-prec-01 (Dell Precision 3630 MT)

- **IP**: 192.168.0.107
- **K3s nodes**: k3s-srv-prec-01 (.227/227) + k3s-agt-prec-01 (.207/207)
- **Storage**: TBD (depends on available drives)
- **Agent role**: General + compute with `PreferNoSchedule` taint
- **Agent specs**: Higher than standard (6 vCPU, 16GB RAM -- exact values TBD based on hardware)
- **Notes**:
  - Workstation-class hardware with more capable CPU than the OptiPlex nodes
  - Good candidate for GPU passthrough if the Precision has a discrete GPU
  - The compute taint means general workloads will prefer other agents first

### Recommended Expansion Order

1. **pve-opt-03 local-ssd** -- Minimal effort, immediate benefit (Section 1)
2. **pve-prec-01** -- Adds server #2 for etcd quorum progress + compute agent
3. **pve-laptop-01** -- Adds server #3 for full 3-node HA quorum + ingress agent
4. **pve-opt-01** -- Additional general capacity
5. **pve-opt-02** -- Additional general capacity

After steps 2-3, you have 3 etcd servers (tolerates 1 server failure). After all 5, you can consider adding 2 more servers at .224/.225 for full 5-node HA (tolerates 2 server failures).

---

## Section 3: Enabling Proxmox HA

Proxmox HA (High Availability) automatically restarts VMs/CTs on surviving nodes when a host fails. This requires a Proxmox cluster with at least 3 nodes (for corosync quorum).

### Prerequisites

- **Minimum 3 Proxmox hosts** in the cluster (for quorum). With 2 hosts, a single failure loses quorum and HA cannot function.
- **Shared or replicated storage**: VMs must be on storage accessible from multiple nodes. Options:
  - ZFS replication between `local-ssd` pools (preferred for this homelab)
  - Shared NFS from pve-nas-01 (already available but NAS is a single point of failure)
  - Ceph (overkill for this setup)
- **Fencing configured**: Proxmox uses corosync for fencing. With 3+ nodes, the surviving majority forms quorum and can fence the failed node.

### Important Limitation: LXC Containers and HA

**Proxmox HA works with VMs, but LXC containers require special consideration.**

LXC containers CAN be configured as HA resources in Proxmox, but with significant limitations:
- **Live migration is not supported** for LXC -- only offline migration (stop on source, start on target)
- LXC migration requires the container rootfs to be on **shared storage** (NFS, Ceph) or **ZFS replication** must be configured
- During HA failover, LXC containers are stopped on the failed node and started on the surviving node, resulting in brief downtime

For this homelab, the brief downtime of LXC migration during failover is acceptable. The services (DNS, SMTP, Plex) are not latency-critical enough to require live migration, and redundancy is handled at the application layer (dns-01/dns-02, clients retry SMTP).

**Decision**: Keep dns-01, dns-02, smtp-relay, and plex as LXC containers. Converting to VMs adds complexity (higher resource overhead, loss of bind mount simplicity for Plex) without meaningful benefit since:
- DNS already has 2 instances (dns-01 + dns-02) -- application-level redundancy
- SMTP relay has retry queues built into the protocol
- Plex tolerates brief outages (clients reconnect automatically)

If you later decide VMs are needed (e.g., for live migration), the conversion procedure is in the appendix at the end of this section.

### Step 1: Configure ZFS Replication

ZFS replication copies VM/CT disk data between hosts, enabling HA failover to a node that already has the data.

```bash
# On the Proxmox web UI:
# 1. Select a VM (e.g., VMID 222 / k3s-srv-nas-01)
# 2. Go to Replication tab
# 3. Click Add
# 4. Target: pve-laptop-01 (or another cluster member)
# 5. Schedule: */15 (every 15 minutes) -- adjust based on RPO needs
# 6. Rate limit: optional, prevents saturating the network

# Or via CLI on the source node:
sudo pvesr create-local-job 222-0 pve-laptop-01 --schedule '*/15' --comment 'k3s-srv-nas-01 to laptop'
```

**Replication targets by host** (suggested):

| Source Host | VM/CT | VMID | Replication Target | Notes |
|-------------|-------|------|--------------------|-------|
| pve-nas-01 | k3s-srv-nas-01 | 222 | pve-laptop-01 or pve-prec-01 | Server node |
| pve-nas-01 | k3s-agt-nas-01 | 202 | pve-laptop-01 or pve-prec-01 | NAS agent -- but NFS mounts break on other hosts |
| pve-nas-01 | dns-01 | 150 | pve-laptop-01 or pve-prec-01 | Primary DNS |
| pve-nas-01 | dns-02 | 160 | pve-opt-03 | Secondary DNS on different host |
| pve-nas-01 | smtp-relay | 151 | pve-laptop-01 or pve-prec-01 | Mail relay |
| pve-nas-01 | home-assistant | 154 | pve-laptop-01 or pve-prec-01 | HAOS VM |
| pve-nas-01 | plex | 152 | -- | Plex depends on NAS bind mounts; HA migration impractical |

**Note on k3s-agt-nas-01**: This agent uses NFS mounts from the NAS. If pve-nas-01 fails, the NFS server is also down, making HA migration of this specific agent pointless. It should be excluded from HA or configured to only run on pve-nas-01.

**Note on plex**: Plex uses bind mounts to the NAS ZFS pools. It can only run on pve-nas-01 and should be excluded from HA.

```bash
# Check replication status
sudo pvesr status
sudo pvesr list

# Manually trigger a replication
sudo pvesr schedule-now 222-0

# View replication logs
sudo journalctl -u pvesr -n 50
```

### Step 2: Enable HA on the Cluster

HA is enabled at the Proxmox cluster level and requires no additional software -- it is built into Proxmox VE.

```bash
# Verify cluster has quorum (need 3+ nodes)
sudo pvecm status | grep -E 'Quorate:|Nodes:'
# "Quorate: Yes" and Nodes >= 3

# HA manager status
sudo ha-manager status
```

If the cluster is quorate, HA is ready to be configured per-resource.

### Step 3: Create HA Groups

HA groups define which nodes can run specific resources. This controls where VMs/CTs migrate during failover.

```bash
# Group for services that should prefer pve-nas-01 (NAS-dependent)
sudo ha-manager groupadd nas-services \
    --nodes pve-nas-01:2,pve-laptop-01:1,pve-prec-01:1 \
    --restricted 1 \
    --nofailback 0 \
    --comment "Services preferring NAS host"

# Group for services that can run anywhere
sudo ha-manager groupadd general \
    --nodes pve-nas-01:1,pve-laptop-01:1,pve-opt-03:1,pve-prec-01:1 \
    --restricted 0 \
    --nofailback 0 \
    --comment "General services - any host"

# Group for DNS - spread across hosts for redundancy
sudo ha-manager groupadd dns-primary \
    --nodes pve-nas-01:2,pve-laptop-01:1,pve-prec-01:1 \
    --restricted 1 \
    --nofailback 0 \
    --comment "Primary DNS server"

sudo ha-manager groupadd dns-secondary \
    --nodes pve-opt-03:2,pve-laptop-01:1,pve-prec-01:1 \
    --restricted 1 \
    --nofailback 0 \
    --comment "Secondary DNS on different host from primary"
```

**Parameter explanation**:
- `--nodes host:priority` -- Higher priority = preferred node. During failover, the surviving node with the highest priority is chosen.
- `--restricted 1` -- Only migrate to nodes listed in the group. Use this when a service has host-specific dependencies.
- `--nofailback 0` -- When the preferred node recovers, migrate back to it. Set to 1 if you want to avoid unnecessary migrations.

### Step 4: Add HA Resources

Configure each VM/CT as an HA-managed resource with its group and restart policy.

```bash
# DNS servers
sudo ha-manager add ct:150 --group dns-primary --state started \
    --max_restart 3 --max_relocate 2 \
    --comment "dns-01 primary DNS"

sudo ha-manager add ct:160 --group dns-secondary --state started \
    --max_restart 3 --max_relocate 2 \
    --comment "dns-02 secondary DNS"

# SMTP relay
sudo ha-manager add ct:151 --group nas-services --state started \
    --max_restart 3 --max_relocate 2 \
    --comment "smtp-relay mail relay"

# Home Assistant (already a VM)
sudo ha-manager add vm:154 --group nas-services --state started \
    --max_restart 3 --max_relocate 2 \
    --comment "home-assistant HAOS"

# K3s server (critical for cluster API)
sudo ha-manager add vm:222 --group general --state started \
    --max_restart 3 --max_relocate 2 \
    --comment "k3s-srv-nas-01 control plane"
```

**Parameter explanation**:
- `--state started` -- HA manager ensures this resource is running. If it stops unexpectedly, HA restarts it.
- `--max_restart 3` -- Try restarting on the same node up to 3 times before relocating.
- `--max_relocate 2` -- Try migrating to another node up to 2 times.
- `ct:150` vs `vm:154` -- Use `ct:` prefix for LXC containers, `vm:` for VMs.

**Resources NOT to add to HA**:
- `plex` (VMID 152) -- Depends on NAS bind mounts, cannot run elsewhere
- `k3s-agt-nas-01` (VMID 202) -- Depends on NFS from NAS, pointless to migrate
- k3s agents on opt nodes -- k3s handles agent failure at the application layer; pods reschedule to other agents automatically

### Step 5: Floating VIPs

The key requirement for HA is that services maintain their IP addresses during migration. Proxmox handles this automatically -- when a VM/CT is migrated to another node, it retains its network configuration including its static IP.

**How it works**:
- Each VM/CT has its IP configured via cloud-init (VMs) or static config (LXCs)
- The IP is part of the VM/CT configuration, not the host configuration
- When Proxmox migrates the resource, it starts on the new host with the same network config
- The LAN switch learns the new MAC-to-port mapping via gratuitous ARP
- Clients see a brief outage (seconds for VMs, potentially longer for LXC offline migration) then reconnect to the same IP

**No additional VIP configuration is needed.** The IPs .150, .151, .154, .160 will follow their respective containers/VMs to whichever host they are running on.

**For kube-vip (.161)**: The k3s API VIP is managed by kube-vip inside the cluster, not by Proxmox HA. If a k3s server node fails, kube-vip reassigns the VIP to another server node. This is independent of Proxmox HA.

**For MetalLB (.100, .101)**: MetalLB VIPs are managed by MetalLB inside k3s. If an agent node running the MetalLB speaker fails, MetalLB moves the VIP to another agent. This is also independent of Proxmox HA.

### Step 6: Testing HA Failover

**WARNING**: These tests intentionally cause downtime. Perform during a maintenance window.

#### Test 1: Simulate Host Failure (Non-Destructive)

```bash
# On the host you want to simulate failing (e.g., pve-nas-01):
# Option A: Graceful -- stop the HA resource and let HA relocate it
sudo ha-manager set ct:160 --state disabled  # Disable dns-02
# Wait, then re-enable
sudo ha-manager set ct:160 --state started

# Option B: Simulate crash -- fence the node (DANGEROUS, causes reboot)
# Only do this if you are prepared for all VMs on that node to be relocated
# ssh to ANOTHER cluster node and run:
sudo ha-manager fence pve-nas-01
```

#### Test 2: Verify DNS Failover

```bash
# Before test: Note which node dns-01 is on
sudo ha-manager status | grep 150

# Trigger migration of dns-01 to another node
sudo ha-manager migrate ct:150 pve-laptop-01

# Wait for migration to complete (monitor in web UI or:)
watch 'sudo ha-manager status'

# Test DNS is still working from your workstation
dig google.com @192.168.0.150
# Should still resolve (same IP, different host)

# Migrate back
sudo ha-manager migrate ct:150 pve-nas-01
```

#### Test 3: Verify Service IP Persistence

```bash
# From your workstation, start a continuous ping to each service
ping -i 1 192.168.0.150 &  # dns-01
ping -i 1 192.168.0.160 &  # dns-02
ping -i 1 192.168.0.151 &  # smtp-relay
ping -i 1 192.168.0.154 &  # home-assistant

# Trigger a migration (e.g., dns-01)
# On a cluster member:
sudo ha-manager migrate ct:150 pve-laptop-01

# Observe: You should see a few lost pings, then the service resumes
# at the same IP address
```

#### Test 4: Full Host Failure Simulation

**Only perform this with 3+ cluster nodes and after all other tests pass.**

```bash
# On the host to "fail" (e.g., pve-nas-01):
sudo systemctl stop pve-cluster corosync
# This simulates a complete host failure
# The remaining cluster members should detect the failure and relocate HA resources

# Monitor from another cluster member:
watch 'sudo ha-manager status'

# After verification, restore the "failed" host:
sudo systemctl start corosync pve-cluster
```

### Firewall Considerations

When VMs/CTs migrate between hosts, firewall rules follow them because:
- Guest firewall rules are stored in `/etc/pve/firewall/<VMID>.fw` on the cluster filesystem (pmxcfs)
- IPSets are stored in `/etc/pve/firewall/cluster.fw`
- Both are shared across all cluster nodes automatically

**No firewall changes are needed for HA.** The rules are already cluster-wide.

However, ensure that:
- All potential target hosts have the bridge interface (`vmbr0`) configured
- The hosts are on the same broadcast domain (same switch/VLAN)
- NFS client access from the new host IPs is in the export list (already handled by CIDR subnets .102-.107)

### Appendix: Converting LXC to VM (If Needed Later)

If you decide to convert an LXC container to a VM for live migration support, here is the procedure. **This is NOT recommended for the current setup** -- LXC with offline HA migration is sufficient.

```bash
# 1. Stop the container
sudo pct stop <VMID>

# 2. Backup the container
sudo vzdump <VMID> --storage local --compress zstd

# 3. Export container filesystem
sudo pct mount <VMID>
sudo tar czf /tmp/lxc-<VMID>-rootfs.tar.gz -C /var/lib/lxc/<VMID>/rootfs .
sudo pct unmount <VMID>

# 4. Create a new VM with the next available VMID
# (or reuse the same VMID after destroying the LXC)
# Use cloud-init Debian image as base, then restore data

# 5. Install services inside the VM (same Ansible roles apply)
# For dns-01: Ansible roles adguard_home + unbound
# For smtp-relay: Ansible role smtp_relay
# For plex: Ansible role plex (but lose bind mount efficiency)

# 6. Update inventory: change VMID or host references if needed
# 7. Update firewall: same VMID keeps same rules
# 8. Test thoroughly before destroying the LXC backup
```

---

## Network Diagram

```
Internet
    |
[Router 192.168.0.1]
    |
[192.168.0.0/24] ---- Core LAN
    |
    +-- Proxmox Cluster (HA with 3+ nodes)
    |   +-- pve-nas-01    (.102) -- NAS + Storage [active]
    |   +-- pve-laptop-01 (.103) -- Compute [future]
    |   +-- pve-opt-01    (.104) -- Compute [future]
    |   +-- pve-opt-02    (.105) -- Compute [future]
    |   +-- pve-opt-03    (.106) -- Compute [active]
    |   +-- pve-prec-01   (.107) -- Compute [future]
    |
    +-- Infrastructure (HA-managed, floating IPs)
    |   +-- dns-01     (.150)  -- Primary DNS
    |   +-- smtp-relay (.151)  -- Mail relay
    |   +-- plex       (.152)  -- Plex (NAS-bound, no HA)
    |   +-- home       (.154)  -- Home Assistant VM
    |   +-- dns-02     (.160)  -- Secondary DNS
    |
    +-- K3s Servers (.22X) -- control plane only
    |   +-- k3s-srv-nas-01    (.222) on pve-nas-01     [active]
    |   +-- k3s-srv-laptop-01 (.223) on pve-laptop-01  [future]
    |   +-- k3s-srv-prec-01   (.227) on pve-prec-01    [future]
    |
    +-- K3s Agents (.20X) -- workloads
    |   +-- k3s-agt-nas-01    (.202) on pve-nas-01     [nas, general]
    |   +-- k3s-agt-laptop-01 (.203) on pve-laptop-01  [ingress, general] [future]
    |   +-- k3s-agt-opt-01    (.204) on pve-opt-01     [general] [future]
    |   +-- k3s-agt-opt-02    (.205) on pve-opt-02     [general] [future]
    |   +-- k3s-agt-opt-03    (.206) on pve-opt-03     [ingress, general]
    |   +-- k3s-agt-prec-01   (.207) on pve-prec-01    [general, compute] [future]
    |
    +-- Virtual IPs
        +-- vip-public    (.100) -- MetalLB (managed by MetalLB)
        +-- vip-internal  (.101) -- MetalLB (managed by MetalLB)
        +-- k3s-api       (.161) -- kube-vip (managed by kube-vip)
```

---

## Related Documentation

- `docs/00-hardware-setup.md` -- Bare metal to Proxmox ready for Ansible
- `docs/01-overview.md` -- Architecture and network topology
- `docs/06-zfs.md` -- ZFS pool creation commands and properties
- `docs/08-dns.md` -- DNS records for all nodes
- `docs/11-firewall.md` -- Firewall IPSets and security groups
- `docs/14-post-base-plan.md` -- K3s platform roadmap and scheduling model
- `docs/17-disaster-recovery.md` -- Storage bootstrap and disaster recovery
- `docs/18-bootstrap-new-systems.md` -- Bootstrapping new LXC/VM systems
- `docs/19-k3s-deployment.md` -- K3s cluster deployment workflow
- [Proxmox HA Manager](https://pve.proxmox.com/wiki/High_Availability) -- Official Proxmox HA documentation
- [Proxmox ZFS Replication](https://pve.proxmox.com/wiki/Storage_Replication) -- ZFS replication between cluster nodes
- [Proxmox Cluster Manager](https://pve.proxmox.com/wiki/Cluster_Manager) -- Corosync and cluster setup
