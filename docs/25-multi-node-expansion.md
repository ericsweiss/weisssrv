# Multi-Node Expansion and Proxmox HA Guide

This document covers the architecture and procedures for the 6-node Proxmox HA cluster with k3s running across all hosts. It includes reference material for storage configuration, host setup procedures, and HA management.

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

**Proxmox Cluster (weisssrv)** - 6 nodes, fully quorate:

| Host | IP | Role | Storage | Status |
|------|-----|------|---------|--------|
| pve-nas-01 | .102 | NAS + storage | tank/ssd/nvme/archive | Active |
| pve-laptop-01 | .103 | Compute | local-ssd (1TB) | Active |
| pve-opt-01 | .104 | Compute | local-ssd (1TB) | Active |
| pve-opt-02 | .105 | Compute | local-ssd (1TB) | Active |
| pve-opt-03 | .106 | Compute | local-ssd (1TB) | Active |
| pve-prec-01 | .107 | Compute | local-ssd (1TB) | Active |

**K3s Cluster** - 9 nodes (3 servers + 6 agents):

| Node | IP | VMID | Host | Role | Status |
|------|-----|------|------|------|--------|
| k3s-srv-nas-01 | .222 | 222 | pve-nas-01 | Server (first) | Active |
| k3s-srv-laptop-01 | .223 | 223 | pve-laptop-01 | Server | Active |
| k3s-srv-prec-01 | .227 | 227 | pve-prec-01 | Server | Active |
| k3s-agt-nas-01 | .202 | 202 | pve-nas-01 | Agent (NAS) | Active |
| k3s-agt-laptop-01 | .203 | 203 | pve-laptop-01 | Agent (ingress) | Active |
| k3s-agt-opt-01 | .204 | 204 | pve-opt-01 | Agent (ingress) | Active |
| k3s-agt-opt-02 | .205 | 205 | pve-opt-02 | Agent (ingress) | Active |
| k3s-agt-opt-03 | .206 | 206 | pve-opt-03 | Agent (ingress) | Active |
| k3s-agt-prec-01 | .207 | 207 | pve-prec-01 | Agent (compute) | Active |

**Infrastructure Services (HA-managed)**:

These services float between nodes via Proxmox HA - there is no fixed "preferred" or "current" host. To check actual runtime locations, run `task proxmox:ha-status` or `ha-manager status` on any cluster node.

| Service | VMID | Type | HA State | Eligible Hosts (have replicated data) |
|---------|------|------|----------|---------------------------------------|
| dns-01 | 150 | LXC | started | pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 |
| smtp-relay | 151 | LXC | started | pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 |
| dns-02 | 160 | LXC | started | pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 |
| home-assistant | 154 | VM | started | pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 |

**Non-HA Services (NAS-bound)**:

| Service | VMID | Type | Host | Notes |
|---------|------|------|------|-------|
| plex | 152 | LXC | pve-nas-01 | Requires NAS bind mounts |
| k3s-agt-nas-01 | 202 | VM | pve-nas-01 | Requires local NFS access |

**HA Configuration**:
- Node-affinity rule `critical-services-no-nas` excludes pve-nas-01 from HA resources
- Multi-target ZFS replication (every 15 minutes) to 4 nodes per service
- Services can failover to any node with replicated data

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
| pve-laptop-01 | .103 | Active | MSI GS60 2QD | srv-laptop-01 (.223), agt-laptop-01 (.203) |
| pve-opt-01 | .104 | Active | Dell OptiPlex 780 | agt-opt-01 (.204) -- agent only |
| pve-opt-02 | .105 | Active | Dell OptiPlex 780 | agt-opt-02 (.205) -- agent only |
| pve-opt-03 | .106 | Active | Dell OptiPlex 780 | agt-opt-03 (.206) |
| pve-prec-01 | .107 | Active | Dell Precision 3630 | srv-prec-01 (.227), agt-prec-01 (.207) |

### K3s Server Nodes (.22X -- Control Plane)

| Node | IP | VMID | Host | Storage | Status |
|------|-----|------|------|---------|--------|
| k3s-srv-nas-01 | .222 | 222 | pve-nas-01 | local-lvm | Active |
| k3s-srv-laptop-01 | .223 | 223 | pve-laptop-01 | local-ssd | Active |
| k3s-srv-prec-01 | .227 | 227 | pve-prec-01 | local-ssd | Active |
| (reserved) | .224 | 224 | - | - | Reserved for 5-node HA |
| (reserved) | .225 | 225 | - | - | Reserved for 5-node HA |

### K3s Agent Nodes (.20X -- Workers)

| Node | IP | VMID | Host | Role | Status |
|------|-----|------|------|------|--------|
| k3s-agt-nas-01 | .202 | 202 | pve-nas-01 | NAS workloads | Active |
| k3s-agt-laptop-01 | .203 | 203 | pve-laptop-01 | Ingress + general | Active |
| k3s-agt-opt-01 | .204 | 204 | pve-opt-01 | Ingress + general | Active |
| k3s-agt-opt-02 | .205 | 205 | pve-opt-02 | Ingress + general | Active |
| k3s-agt-opt-03 | .206 | 206 | pve-opt-03 | Ingress + general | Active |
| k3s-agt-prec-01 | .207 | 207 | pve-prec-01 | General + compute | Active |

---

## Node Labels and Taints

### Labels

All labels use the `esweiss.com/` prefix:

| Label | Purpose | Nodes |
|-------|---------|-------|
| `esweiss.com/nas=true` | Fast NAS storage access (local NFS) | k3s-agt-nas-01 |
| `esweiss.com/general=true` | General workloads | All agents |
| `esweiss.com/ingress=true` | Ingress controller eligible | k3s-agt-laptop-01, k3s-agt-opt-01, k3s-agt-opt-02, k3s-agt-opt-03 |
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

## Section 1: Adding local-ssd ZFS Pool (Reference)

**Status**: **Complete** - All compute nodes now have local-ssd configured.

This section documents the setup of a 1TB SSD with a `local-ssd` ZFS pool, which provides snapshots, compression, and enables Proxmox HA replication. Use this as a reference when adding new compute nodes.

### Storage Strategy for Multi-Node Setup

**Automated Storage Selection**: The `proxmox_vm` and `proxmox_lxc` Ansible roles now automatically select storage based on the Proxmox host's role:

| Proxmox Host Role | Default Storage | Details |
|-------------------|-----------------|---------|
| `nas` (pve-nas-01) | `ssd` | 3x 4TB Samsung SSDs (raidz1) - App data and databases |
| `compute` / `general` (all others) | `local-ssd` | 1TB Samsung 870 EVO per host - VM/container workloads |

Storage can be overridden per-VM/container by setting `proxmox_storage` or `lxc_storage` in the inventory.

**Why local-ssd for compute nodes?**
1. **Proxmox HA**: ZFS pools required on all nodes for replication and failover
2. **Stateless workloads**: K3s agents, DNS, SMTP have redundancy via k8s or multiple instances
3. **ZFS benefits**: Compression (lz4), snapshots, checksumming, atomic operations
4. **lz4 for VMs**: Low-latency compression (~10x faster decompression than zstd, near-zero CPU overhead)

**Current Storage Layout**:
- pve-nas-01: `local-lvm` holds the Plex container root and the k3s VM roots (server/agent); the `ssd` pool holds the GitLab VM root, the k3s-agt-nas-01 passthrough zvols (postgres/mealie/prometheus/loki), and the Plex `/config` bind mount
- All compute nodes (pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01): Use `local-ssd` for all VM/container workloads

### Prerequisites

- 1x 1TB Samsung 870 EVO SSD (or equivalent) installed in the target compute node
- Physical access to install the drive (or hot-swap if chassis supports it)

### Step 1: Identify the Disk

```bash
# SSH to the target compute node (example: pve-opt-03)
ssh eric@<node-ip>

# List all block devices
sudo lsblk -d -o NAME,SIZE,MODEL,SERIAL,ROTA
# The new SSD should show ROTA=0 (non-rotational)

# Get the by-id path (required for ZFS)
ls -la /dev/disk/by-id/ | grep -i samsung
# Example output: ata-Samsung_SSD_870_EVO_1TB_S6PENX0T123456A -> ../../sdb
```

**IMPORTANT**: Note the full `/dev/disk/by-id/` path. Never use `/dev/sdX` names directly -- they can change between reboots.

### Step 2: Create the ZFS Pool

See `docs/06-zfs.md` for complete details. **Key difference from NAS pools**: Use `lz4` compression instead of `zstd` for VM workloads (lower latency).

```bash
# Create local-ssd pool (single device, no redundancy)
sudo zpool create -f -o ashift=12 \
    -O acltype=posixacl \
    -O compression=lz4 \
    -O normalization=formD \
    -O atime=off \
    -O xattr=sa \
    local-ssd \
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER

# Enable autotrim for SSD longevity
sudo zpool set autotrim=on local-ssd

# Verify pool creation
sudo zpool status local-ssd
sudo zpool list local-ssd
sudo zfs list local-ssd
```

**Pool properties optimized for VM workloads**:
- `ashift=12`: 4K sector alignment
- `compression=lz4`: Low-latency compression (better than zstd for VMs)
- `atime=off`: Reduces write amplification (vs relatime on NAS pools)
- `autotrim=on`: SSD longevity and performance
- `xattr=sa`: Extended attributes in system attribute table (shows as "on" in ZFS 2.3+)

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

## Section 2: Setting Up Proxmox Hosts (Reference)

This section documents the procedure for bringing a host from bare hardware to a fully integrated Proxmox cluster member with k3s nodes. Use this as a reference when adding new hosts in the future.

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

For hosts with a dedicated 1TB SSD (all compute nodes):

```bash
# Identify the SSD
ls -la /dev/disk/by-id/ | grep -i samsung

# Create pool (replace SERIALNUMBER with actual serial)
# NOTE: Use lz4 (not zstd) and atime=off for VM workloads
sudo zpool create -f -o ashift=12 \
    -O acltype=posixacl \
    -O compression=lz4 \
    -O normalization=formD \
    -O atime=off \
    -O xattr=sa \
    local-ssd \
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_SERIALNUMBER

# Enable autotrim for SSD longevity
sudo zpool set autotrim=on local-ssd

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
task infra:base -- --limit pve-laptop-01

# 3. Deploy full stack (firewall, tailscale, etc.)
task infra:deploy -- --limit pve-laptop-01

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
- **Storage**: 1TB Samsung 870 EVO as `local-ssd`
- **Agent role**: General + compute with `PreferNoSchedule` taint
- **Agent specs**: 6 vCPU, 16GB RAM (workstation-class hardware)
- **Notes**:
  - Workstation-class hardware with more capable CPU than the OptiPlex nodes
  - Good candidate for GPU passthrough if the Precision has a discrete GPU
  - The compute taint means general workloads will prefer other agents first

### Expansion History

The cluster was expanded in the following order (all complete):

1. **pve-opt-03 local-ssd** -- First compute node with local storage
2. **pve-prec-01** -- Added server #2 for etcd quorum progress + compute agent
3. **pve-laptop-01** -- Added server #3 for full 3-node HA quorum + ingress agent
4. **pve-opt-01** -- Additional general/ingress capacity
5. **pve-opt-02** -- Additional general/ingress capacity

**Current state**: 3 etcd servers (tolerates 1 server failure), 6 agents for workloads.

**Future expansion**: Consider adding 2 more servers at .224/.225 for 5-node HA (tolerates 2 server failures).

---

## Section 3: Proxmox HA Configuration (Reference)

**Status**: **Complete** - HA is fully configured and active on the 6-node cluster.

Proxmox HA (High Availability) automatically restarts VMs/CTs on surviving nodes when a host fails. This section documents the HA architecture and procedures for reference.

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

**Best Practice: Multi-Target Replication**

Proxmox supports replicating a VM/CT to MULTIPLE target nodes (but not twice to the same node). This allows services to failover to ANY available node, not just a single backup. This is the recommended approach for true high availability.

**Current Configuration** (managed by Ansible):

Services are distributed across 5 nodes with `local-ssd` storage (excluding pve-nas-01 which has no local-ssd). Services have been migrated OFF pve-nas-01 to nodes with local-ssd pools to enable HA replication.

| Service | VMID | Primary Node | Replication Targets |
|---------|------|---------------------|---------------------|
| dns-01 | 150 | pve-laptop-01 | pve-opt-01, pve-opt-02, pve-opt-03, pve-prec-01 |
| smtp-relay | 151 | pve-opt-01 | pve-laptop-01, pve-opt-02, pve-opt-03, pve-prec-01 |
| dns-02 | 160 | pve-opt-03 | pve-laptop-01, pve-opt-01, pve-opt-02, pve-prec-01 |
| home-assistant | 154 | pve-prec-01 | pve-laptop-01, pve-opt-01, pve-opt-02, pve-opt-03 |

Each service replicates every 15 minutes to ALL 4 other nodes. When any node fails, HA can restart the service on ANY surviving node that has replicated data.

**Why pve-nas-01 is excluded**:
- No `local-ssd` storage (services were migrated OFF to avoid I/O contention with NAS workloads)
- Services with NAS dependencies (Plex, k3s-agt-nas-01) cannot run elsewhere anyway

**Managing Replication via Ansible** (recommended):

```bash
# Deploy HA rules, resources, and multi-target replication
task proxmox:ha

# Check what would change
task proxmox:ha-check

# View current status
task proxmox:ha-status
```

**Manual CLI commands** (for reference):

```bash
# Create a multi-target replication job (repeat for each target)
sudo pvesr create-local-job 150-0 pve-opt-01 --schedule '*/15' --comment 'dns-01 -> pve-opt-01'
sudo pvesr create-local-job 150-1 pve-opt-02 --schedule '*/15' --comment 'dns-01 -> pve-opt-02'
sudo pvesr create-local-job 150-2 pve-opt-03 --schedule '*/15' --comment 'dns-01 -> pve-opt-03'
sudo pvesr create-local-job 150-3 pve-prec-01 --schedule '*/15' --comment 'dns-01 -> pve-prec-01'

# Check replication status
sudo pvesr status
sudo pvesr list

# Manually trigger a replication
sudo pvesr schedule-now 150-0

# View replication logs
sudo journalctl -u pvesr -n 50
```

**Services NOT replicated**:
- `plex` (VMID 152) -- Depends on NAS bind mounts; can only run on pve-nas-01
- `k3s-agt-nas-01` (VMID 202) -- Depends on NFS from NAS; pointless to migrate

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

### Step 3: Create Node Affinity Rules

Node-affinity rules provide flexible control over which nodes can run specific resources.

**Current Configuration** (managed by Ansible):

We use a single node-affinity rule that excludes pve-nas-01 from all critical services:

```yaml
# From ansible/inventories/prod/group_vars/all.yml
ha_rules:
  - name: critical-services-no-nas
    type: node-affinity
    resources:
      - ct:150  # dns-01
      - ct:160  # dns-02
      - ct:151  # smtp-relay
      - vm:154  # home-assistant
    nodes:
      - pve-laptop-01
      - pve-opt-01
      - pve-opt-02
      - pve-opt-03
      - pve-prec-01
    strict: false  # Allow NAS only if ALL other nodes unavailable
```

**Why exclude pve-nas-01**:
- Avoids I/O contention between critical services and NAS workloads
- Services can float freely among the 5 `local-ssd` nodes
- Multi-target replication ensures data is available on all eligible nodes

**Manual CLI commands** (for reference):

```bash
# Create node-affinity rule (Proxmox 9+)
sudo ha-manager rules add node-affinity critical-services-no-nas \
    --resources ct:150,ct:151,ct:160,vm:154 \
    --nodes pve-laptop-01,pve-opt-01,pve-opt-02,pve-opt-03,pve-prec-01 \
    --comment "Prevent critical services from running on pve-nas-01"

# List rules
sudo ha-manager rules list

# Update a rule
sudo ha-manager rules set node-affinity critical-services-no-nas \
    --resources ct:150,ct:151,ct:160,vm:154 \
    --nodes pve-laptop-01,pve-opt-01,pve-opt-02,pve-opt-03,pve-prec-01
```

### Step 4: Add HA Resources

Configure each VM/CT as an HA-managed resource. When using node-affinity rules (Step 3), you don't need to specify a `--group` -- the rules control placement.

**Current Configuration** (managed by Ansible):

```yaml
# From ansible/inventories/prod/group_vars/all.yml
ha_resources:
  - type: ct
    vmid: 150
    state: started
    comment: "dns-01 (AdGuard Home primary)"
    enabled: true

  - type: ct
    vmid: 151
    state: started
    comment: "smtp-relay (Postfix relay)"
    enabled: true

  - type: ct
    vmid: 160
    state: started
    comment: "dns-02 (AdGuard Home secondary)"
    enabled: true

  - type: vm
    vmid: 154
    state: started
    comment: "home-assistant (HAOS VM)"
    enabled: true
```

**Manual CLI commands** (for reference):

```bash
# Add resources to HA (no group needed when using node-affinity rules)
sudo ha-manager add ct:150 --state started --comment "dns-01 primary DNS"
sudo ha-manager add ct:151 --state started --comment "smtp-relay mail relay"
sudo ha-manager add ct:160 --state started --comment "dns-02 secondary DNS"
sudo ha-manager add vm:154 --state started --comment "home-assistant HAOS"

# View current HA resources
sudo ha-manager config

# Update a resource
sudo ha-manager set ct:150 --state started
```

**Parameter explanation**:
- `--state started` -- HA manager ensures this resource is running. If it stops unexpectedly, HA restarts it.
- `ct:150` vs `vm:154` -- Use `ct:` prefix for LXC containers, `vm:` for VMs.

**Resources NOT to add to HA**:
- `plex` (VMID 152) -- Depends on NAS bind mounts, cannot run elsewhere
- `k3s-agt-nas-01` (VMID 202) -- Depends on NFS from NAS, pointless to migrate
- k3s agents on other nodes -- k3s handles agent failure at the application layer; pods reschedule automatically

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
    +-- Proxmox Cluster (6-node HA cluster "weisssrv")
    |   +-- pve-nas-01    (.102) -- NAS + Storage
    |   +-- pve-laptop-01 (.103) -- Compute
    |   +-- pve-opt-01    (.104) -- Compute
    |   +-- pve-opt-02    (.105) -- Compute
    |   +-- pve-opt-03    (.106) -- Compute
    |   +-- pve-prec-01   (.107) -- Compute
    |
    +-- Infrastructure (HA-managed, floating IPs)
    |   +-- dns-01     (.150)  -- Primary DNS (floats via HA)
    |   +-- smtp-relay (.151)  -- Mail relay (floats via HA)
    |   +-- plex       (.152)  -- Plex (NAS-bound, no HA)
    |   +-- home       (.154)  -- Home Assistant VM (floats via HA)
    |   +-- dns-02     (.160)  -- Secondary DNS (floats via HA)
    |
    +-- K3s Servers (.22X) -- 3-node etcd quorum
    |   +-- k3s-srv-nas-01    (.222) on pve-nas-01
    |   +-- k3s-srv-laptop-01 (.223) on pve-laptop-01
    |   +-- k3s-srv-prec-01   (.227) on pve-prec-01
    |
    +-- K3s Agents (.20X) -- workloads
    |   +-- k3s-agt-nas-01    (.202) on pve-nas-01     [nas, general]
    |   +-- k3s-agt-laptop-01 (.203) on pve-laptop-01  [ingress, general]
    |   +-- k3s-agt-opt-01    (.204) on pve-opt-01     [ingress, general]
    |   +-- k3s-agt-opt-02    (.205) on pve-opt-02     [ingress, general]
    |   +-- k3s-agt-opt-03    (.206) on pve-opt-03     [ingress, general]
    |   +-- k3s-agt-prec-01   (.207) on pve-prec-01    [general, compute]
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
