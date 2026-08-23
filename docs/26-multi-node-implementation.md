# Multi-Node Implementation Plan (Completed)

> **Status: completed — retained for rebuild reference.** This document was
> the step-by-step plan for integrating 4 new Proxmox hosts, forming the
> 6-node Proxmox cluster, and expanding the k3s cluster from 3 to 9 nodes.
> The expansion is done; the procedures below remain useful for bootstrapping
> replacement hardware. For the current topology see `docs/01-overview.md`;
> for current HA operations see `docs/25-multi-node-expansion.md`.

## Overview

### Resulting State (6-Node Cluster)

All 6 Proxmox hosts (`weisssrv` cluster, quorate) and all 9 k3s nodes
(3 servers + 6 agents) are active. The canonical host-by-host and
node-by-node topology lives in `docs/01-overview.md`.

### Architecture

**6-node Proxmox cluster** (`weisssrv`) with quorum (4+ nodes required for majority):
- All nodes carry ZFS pools for HA replication: pve-nas-01 uses `ssd`, the
  five compute hosts use `local-ssd` (see `docs/06-zfs.md` for pool details)

---

## Phase 1: Bootstrap New Proxmox Hosts

### Prerequisites

Before starting, ensure:
- [ ] All 4 new hosts have Proxmox VE 9+ installed
- [ ] All hosts are reachable at their expected IPs
- [ ] 1TB Samsung 870 EVO SSD installed in each host (not yet formatted)
- [ ] You have SSH access to your workstation

### Step 1.1: Bootstrap Script Overview

The bootstrap script (`scripts/bootstrap-proxmox-host.sh`) automates the creation of the `eric` user on fresh Proxmox hosts. It:

1. Prompts for a password for the `eric` user (hidden input with confirmation)
2. Creates the `eric` user with that password
3. Configures passwordless sudo for `eric`
4. Deploys your SSH public key
5. Verifies SSH access works

The password allows local console access while SSH keys provide secure remote access.

### Step 1.2: Bootstrap Each Host

Run the bootstrap script for each new host. You'll be prompted to:
1. Enter a password for the `eric` user (same password for all hosts recommended)
2. Enter the root password for SSH access to the target host

```bash
# Get your SSH public key from 1Password
SSH_KEY=$(op read "op://Homelab/SSH Key/public key")

# Bootstrap each host
# You will be prompted for:
#   1. Password for eric user (enter twice for confirmation)
#   2. Root password for the target host
./scripts/bootstrap-proxmox-host.sh 10.0.10.103 "$SSH_KEY"  # pve-laptop-01
./scripts/bootstrap-proxmox-host.sh 10.0.10.104 "$SSH_KEY"  # pve-opt-01
./scripts/bootstrap-proxmox-host.sh 10.0.10.105 "$SSH_KEY"  # pve-opt-02
./scripts/bootstrap-proxmox-host.sh 10.0.10.107 "$SSH_KEY"  # pve-prec-01
```

**Note**: Using the same password for `eric` on all hosts makes management easier. The password is for local console access only - SSH authentication uses keys.

### Step 1.3: Create local-ssd ZFS Pools

SSH to each new host and create the ZFS pool:

```bash
# Template for each host - adjust serial number
ssh eric@<host-ip> << 'EOF'
# Identify the SSD
echo "=== Available disks ==="
lsblk -d -o NAME,SIZE,MODEL,SERIAL,ROTA

echo ""
echo "=== Samsung SSDs by-id paths ==="
ls -la /dev/disk/by-id/ | grep -i samsung

# IMPORTANT: Note the /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_<SERIAL> path
# You'll need this for the zpool create command
EOF
```

For each host, create the pool with the canonical commands in
[docs/25 § Step 2: Create the ZFS Pool](25-multi-node-expansion.md) — do not
hand-transcribe them here — then register it with Proxmox:

```bash
ssh eric@<host-ip> << 'EOF'
sudo pvesm add zfspool local-ssd --pool local-ssd --content images,rootdir
sudo zpool status local-ssd
sudo pvesm status
EOF
```

**Host-specific SSD serials** (collect during identification step):
- pve-laptop-01: `ata-Samsung_SSD_870_EVO_1TB_<SERIAL>`
- pve-opt-01: `ata-Samsung_SSD_870_EVO_1TB_<SERIAL>`
- pve-opt-02: `ata-Samsung_SSD_870_EVO_1TB_<SERIAL>`
- pve-prec-01: `ata-Samsung_SSD_870_EVO_1TB_<SERIAL>`

### Step 1.4: Update Ansible Inventory

Move hosts from `proxmox_unmanaged` to `proxmox` group in `ansible/inventories/prod/hosts.yml`:

```yaml
    # Proxmox VE hypervisors (managed by Ansible)
    proxmox:
      hosts:
        pve-nas-01:
          ansible_host: 10.0.10.102
          proxmox_role: nas
          firewall_ipsets:
            - pve_hosts
            - core-cluster
            - nfs_clients
        pve-laptop-01:
          ansible_host: 10.0.10.103
          proxmox_role: compute
          firewall_ipsets:
            - pve_hosts
            - core-cluster
            - nfs_clients
        pve-opt-01:
          ansible_host: 10.0.10.104
          proxmox_role: compute
          firewall_ipsets:
            - pve_hosts
            - core-cluster
            - nfs_clients
        pve-opt-02:
          ansible_host: 10.0.10.105
          proxmox_role: compute
          firewall_ipsets:
            - pve_hosts
            - core-cluster
            - nfs_clients
        pve-opt-03:
          ansible_host: 10.0.10.106
          proxmox_role: compute
          firewall_ipsets:
            - pve_hosts
            - core-cluster
            - nfs_clients
        pve-prec-01:
          ansible_host: 10.0.10.107
          proxmox_role: compute
          firewall_ipsets:
            - pve_hosts
            - core-cluster
            - nfs_clients

    # Remove proxmox_unmanaged section entirely (all hosts now managed)
```

### Step 1.5: Test Ansible Connectivity

```bash
# Verify all hosts are reachable
task ansible:ping

# Expected output - all 6 hosts should respond with pong
# pve-nas-01 | SUCCESS => {"changed": false, "ping": "pong"}
# pve-laptop-01 | SUCCESS => {"changed": false, "ping": "pong"}
# pve-opt-01 | SUCCESS => {"changed": false, "ping": "pong"}
# pve-opt-02 | SUCCESS => {"changed": false, "ping": "pong"}
# pve-opt-03 | SUCCESS => {"changed": false, "ping": "pong"}
# pve-prec-01 | SUCCESS => {"changed": false, "ping": "pong"}
```

### Step 1.6: Deploy Base Infrastructure to New Hosts

```bash
# Deploy base configuration to all new hosts
task infra:deploy -- --limit pve-laptop-01,pve-opt-01,pve-opt-02,pve-prec-01

# This deploys:
# - base role (packages, SSH hardening, users, timezone)
# - qol role (Oh My Zsh, neovim, fzf, ripgrep)
# - tailscale role (VPN client)
# - proxmox_firewall role (IPSets, security groups, host.fw)
# - postfix_null_client (mail relay to smtp-relay)
```

### Step 1.7: Complete Tailscale Setup

After deployment, manually authenticate each host with Tailscale:

```bash
# SSH to each host and run tailscale up. --accept-routes MUST be false: every
# Proxmox host is a subnet router now (tag:subnet-router, tag-only
# auto-approval), and a host that accepts routes while sitting ON the LAN
# consumes its peers' advertisement of its own subnet — a routing loop. The
# current canonical re-join command (with the advertise flags) is in
# terraform/tailscale/README.md § Recovery.
ssh eric@10.0.10.103 "sudo tailscale up --accept-routes=false --accept-dns=false"
ssh eric@10.0.10.104 "sudo tailscale up --accept-routes=false --accept-dns=false"
ssh eric@10.0.10.105 "sudo tailscale up --accept-routes=false --accept-dns=false"
ssh eric@10.0.10.107 "sudo tailscale up --accept-routes=false --accept-dns=false"
```

This will display a URL for each host - open in browser to authenticate.

---

## Phase 2: Form Proxmox Cluster

### Important: Cluster Formation Order

**Cluster formation must happen BEFORE deploying any VMs on new hosts.**

Proxmox cluster join requires:
1. The node must be "clean" (no VMs or containers)
2. All nodes must have the same Proxmox VE version
3. Network connectivity between all nodes on port 8006 (already configured via firewall)

### Step 2.1: Create the Cluster (One-Time, on pve-nas-01)

```bash
# SSH to pve-nas-01 (will be the cluster creator)
ssh eric@10.0.10.102

# Create the cluster
sudo pvecm create weisssrv

# Verify cluster creation
sudo pvecm status
# Should show: Cluster information, Quorum: 1, Nodes: 1
```

### Step 2.2: Join Each New Node to the Cluster

**Join in this order** (existing infra first, then new nodes):

```bash
# 1. Join pve-opt-03 (existing compute node)
ssh eric@10.0.10.106
sudo pvecm add 10.0.10.102
# Enter root password of pve-nas-01 when prompted
# Node will restart services and join cluster
```

Wait for join to complete, then verify:

```bash
# On any cluster member
sudo pvecm status
sudo pvecm nodes
# Should show 2 nodes now
```

**Then join remaining nodes one by one:**

```bash
# 2. Join pve-laptop-01
ssh eric@10.0.10.103
sudo pvecm add 10.0.10.102
# Wait for completion...

# 3. Join pve-opt-01
ssh eric@10.0.10.104
sudo pvecm add 10.0.10.102
# Wait for completion...

# 4. Join pve-opt-02
ssh eric@10.0.10.105
sudo pvecm add 10.0.10.102
# Wait for completion...

# 5. Join pve-prec-01
ssh eric@10.0.10.107
sudo pvecm add 10.0.10.102
# Wait for completion...
```

### Step 2.3: Verify Cluster Formation

```bash
# From any cluster member
ssh eric@10.0.10.102

# Check cluster status
sudo pvecm status
# Expected output:
# Cluster information
# -------------------
# Name:             weisssrv
# Config Version:   X
# Transport:        knet
# Secure auth:      on
#
# Quorum information
# ------------------
# Date:             <date>
# Quorum provider:  corosync_votequorum
# Nodes:            6
# Node ID:          0x00000001
# Ring ID:          1/X
# Quorate:          Yes

# Check all nodes
sudo pvecm nodes
# Should list all 6 nodes with Online status

# Check from Proxmox Web UI
# Datacenter > Cluster should show all 6 nodes
```

### Quorum Information

With 6 nodes:
- **Quorum requires 4 votes** (majority)
- Cluster survives loss of up to 2 nodes
- If exactly 3 nodes remain and split, neither partition has quorum

---

## Phase 3: Expand k3s Cluster

### K3s Expansion Order Rationale

**Order matters for etcd:**
1. **Add server nodes first** - Establishes 3-node etcd quorum before adding agents
2. **Add one server at a time** - Allows etcd to reach consensus before adding more members
3. **Add agents after all servers** - Agents just connect, no etcd membership concerns

### Step 3.1: Update Inventory with New k3s Nodes

The full k3s node definitions are **not reproduced here** — they live in
`ansible/inventories/prod/hosts.yml`, which is the machine-readable source of
truth (and from which `scripts/hosts.env` is generated). Uncomment/edit the
`k3s_servers` and `k3s_agents` groups there; per-node `k3s_labels`, `k3s_taints`,
`vm_memory`, `proxmox_vm_cores`, `proxmox_storage`, and `guest_security_groups` all live
inline in that file. Current label/taint placement is summarised in
[docs/25-multi-node-expansion.md](25-multi-node-expansion.md).

> Note: `k3s-agt-nas-01`'s four data zvols live on the **encrypted** `ssd` pool,
> so that host sets `proxmox_autostart_enabled: false` and is started by
> `pve-start-encrypted-guests.service` after the pool unlocks (see hosts.yml and
> [docs/32-zfs-encryption.md](32-zfs-encryption.md)).

### Step 3.2: Update DNS Records

Verify DNS records exist for new k3s nodes in `ansible/inventories/prod/group_vars/dns.yml`. The records should already be present per docs/08-dns.md.

Deploy DNS configuration:

```bash
task dns:deploy -- --limit dns-01
# dns-02 syncs automatically via adguardhome-sync
```

### Step 3.3: Create etcd Snapshot (Safety Backup)

Before expanding, back up the existing cluster:

```bash
task k3s:backup
# Creates snapshot at /var/lib/rancher/k3s/server/db/snapshots/

# Verify snapshot was created
ssh eric@10.0.10.222 "sudo k3s etcd-snapshot ls"
```

### Step 3.4: Add Server Nodes (One at a Time)

**Add k3s-srv-laptop-01 first:**

```bash
# Provision VM on pve-laptop-01
task k3s:provision-vms -- --limit k3s-srv-laptop-01

# Deploy k3s server (joins existing cluster)
task k3s:deploy -- --limit k3s-srv-laptop-01

# Verify it joined
export KUBECONFIG=~/.kube/config-k3s
kubectl get nodes
# Should show 4 nodes now (3 original + 1 new server)

# Verify etcd health
kubectl get pods -n kube-system | grep etcd
```

**Wait 2-3 minutes for etcd to stabilize, then add k3s-srv-prec-01:**

```bash
# Provision VM on pve-prec-01
task k3s:provision-vms -- --limit k3s-srv-prec-01

# Deploy k3s server
task k3s:deploy -- --limit k3s-srv-prec-01

# Verify 3-node etcd cluster
kubectl get nodes
# Should show 5 nodes (3 servers + 2 agents)

# Test kube-vip failover
curl -sk https://10.0.10.161:6443/healthz
# Should return "ok"
```

### Step 3.5: Add Agent Nodes

With 3 server nodes running (etcd quorum established), add all agents at once:

```bash
# Provision all new agent VMs
task k3s:provision-vms -- --limit k3s-agt-laptop-01,k3s-agt-opt-01,k3s-agt-opt-02,k3s-agt-prec-01

# Deploy k3s agents
task k3s:deploy -- --limit k3s-agt-laptop-01,k3s-agt-opt-01,k3s-agt-opt-02,k3s-agt-prec-01

# Verify all 9 nodes
kubectl get nodes
# Expected: 3 servers + 6 agents = 9 nodes
```

### Step 3.6: Apply Node Labels and Taints

Labels and taints are applied automatically by `task k3s:deploy`, but verify:

```bash
# Check labels
kubectl get nodes --show-labels

# Check specific taints
kubectl describe node k3s-agt-nas-01 | grep -A 5 Taints
kubectl describe node k3s-agt-laptop-01 | grep -A 5 Taints
kubectl describe node k3s-agt-prec-01 | grep -A 5 Taints
```

### Step 3.7: Verify Full k3s Cluster

```bash
task k3s:status

# Check all workloads are running
kubectl get pods -A

# Verify MetalLB speakers on new nodes
kubectl get pods -n metallb-system -o wide

# Verify Traefik can reach all ingress nodes
kubectl get pods -n traefik -o wide
```

---

## Phase 4: Enable High Availability

HA configuration is managed by Ansible via the `proxmox_ha` role. This automates:
1. Node-affinity rules (Proxmox 9+) - which nodes can run which services
2. HA resources - which VMs/CTs are managed by HA
3. Storage replication - multi-target ZFS replication for fast failover

### Step 4.1: Deploy HA Configuration via Ansible

The HA configuration is defined in `ansible/inventories/prod/group_vars/all.yml`:
- `proxmox_ha_rules`: Per-service node-affinity rules (`affinity-*`) — each service gets a home node (priority 2) plus fallbacks; pve-nas-01 is never listed (avoid I/O contention with NAS workloads)
- `proxmox_ha_resources`: VMs/CTs to be managed by HA (dns-01, dns-02, smtp-relay, home-assistant)
- `proxmox_ha_replication_jobs`: Multi-target replication (each service replicates to all 4 other nodes)

```bash
# Deploy HA configuration (check mode first)
task proxmox:ha-check

# Apply HA configuration
task proxmox:ha

# Verify HA status
task proxmox:ha-status
```

### Step 4.2: What Gets Configured

**Node-Affinity Rules** (per-service `affinity-*` rules — see
`docs/25-multi-node-expansion.md` and `proxmox_ha_rules` in `group_vars/all.yml`):
- One rule per service: a home node at priority 2 (fails back when available)
  plus the other local-ssd nodes at priority 1
- pve-nas-01 is never listed; `strict: false` allows it only if ALL listed
  nodes are unavailable

**HA Resources**:
- `ct:150` (dns-01) - AdGuard Home primary
- `ct:151` (smtp-relay) - Postfix mail relay
- `ct:160` (dns-02) - AdGuard Home secondary
- `vm:154` (home-assistant) - HAOS VM

**Multi-Target Replication** (every 15 minutes):
- Each service replicates to ALL 4 other local-ssd nodes
- When any node fails, HA can restart the service on ANY surviving node

**Resources NOT managed by HA** (intentionally):
- `plex` (VMID 152) - Depends on NAS bind mounts, cannot run elsewhere
- `k3s-agt-nas-01` (VMID 202) - NFS mounts from NAS, pointless to migrate
- Other k3s VMs - k3s handles node failures at the application layer

### Step 4.3: Manual HA Commands (Reference)

For manual operations or troubleshooting:

```bash
# Check HA status
ssh eric@10.0.10.102 "sudo ha-manager status"

# View HA rules
ssh eric@10.0.10.102 "sudo ha-manager rules list"

# View HA resources
ssh eric@10.0.10.102 "sudo ha-manager config"

# Check replication status
ssh eric@10.0.10.102 "sudo pvesr status"

# Manually migrate a service
ssh eric@10.0.10.102 "sudo ha-manager migrate ct:150 pve-laptop-01"
```

### Step 4.4: Test HA Functionality

> Historical — the node names below are the ones this expansion used. The live
> failover test, with the current homes, is `docs/25-multi-node-expansion.md`
> § Step 6: Testing HA Failover; dns-01's home is now pve-prec-01.

```bash
# Test DNS failover
# 1. Note current location (should show pve-laptop-01 after migration)
task proxmox:ha-status | grep 150

# 2. Migrate dns-01 to pve-opt-03 (or any other available node)
ssh pve-nas-01 "sudo ha-manager migrate ct:150 pve-opt-03"

# 3. Wait for migration
sleep 60

# 4. Test DNS still works
dig google.com @10.0.10.150
# Should still resolve

# 5. Migrate back to original node (pve-laptop-01)
ssh pve-nas-01 "sudo ha-manager migrate ct:150 pve-laptop-01"

# 6. Verify it moved back
task proxmox:ha-status | grep 150
```

---

## Validation and Testing

### Full Cluster Validation

```bash
# 1. Verify all Proxmox nodes
ssh eric@10.0.10.102 "sudo pvecm status"
ssh eric@10.0.10.102 "sudo pvecm nodes"

# 2. Verify k3s cluster
task k3s:status
kubectl get nodes -o wide

# 3. Verify workloads
kubectl get pods -A | grep -v Running | grep -v Completed
# Should only show Running or Completed pods

# 4. Verify HA
ssh eric@10.0.10.102 "sudo ha-manager status"

# 5. Test ingress
curl -k https://auth.esweiss.com/
curl -k https://food.esweiss.com/
curl -k https://home.esweiss.com/

# 6. Collect state
task collect-state
```

### Server Node Failure Test

**WARNING: This will cause brief downtime. Do during maintenance window.**

```bash
# 1. Identify current kube-vip leader
kubectl get pods -n kube-system -l app=kube-vip -o wide

# 2. Simulate server failure (stop one server VM)
ssh eric@10.0.10.103 "sudo qm stop 223"  # Stop k3s-srv-laptop-01

# 3. Verify API VIP fails over
sleep 30
curl -sk https://10.0.10.161:6443/healthz
# Should return "ok" (VIP moved to another server)

# 4. Verify cluster still operational
kubectl get nodes
# k3s-srv-laptop-01 should show NotReady

# 5. Restore server
ssh eric@10.0.10.103 "sudo qm start 223"

# 6. Wait for rejoin
sleep 60
kubectl get nodes
# All nodes should be Ready
```

---

## Rollback Procedures

### Rollback k3s Expansion

If k3s expansion fails:

```bash
# 1. Drain and delete problem nodes from k3s
kubectl drain k3s-agt-<name> --ignore-daemonsets --delete-emptydir-data
kubectl delete node k3s-agt-<name>

# 2. Stop and remove VMs
ssh eric@<proxmox-host> "sudo qm stop <vmid> && sudo qm destroy <vmid>"

# 3. Remove from Ansible inventory (comment out entries)

# 4. If etcd is corrupted, restore from snapshot
ssh eric@10.0.10.222 << 'EOF'
sudo systemctl stop k3s
sudo k3s server --cluster-reset --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/<snapshot-name>
sudo systemctl start k3s
EOF
```

### Rollback Proxmox Cluster

If a node causes cluster problems:

```bash
# On the problem node (if accessible)
ssh eric@<problem-node> "sudo pvecm delnode <node-name>"

# If node is not accessible, remove from existing member
ssh eric@10.0.10.102 << 'EOF'
sudo pvecm expected 1  # Temporarily lower expected votes
sudo pvecm delnode <problem-node-name>
sudo pvecm expected 6  # Restore expected votes (or adjust for remaining nodes)
EOF
```

### Revert to Non-HA

If HA causes issues:

```bash
# Remove all HA resources
ssh eric@10.0.10.102 << 'EOF'
sudo ha-manager remove ct:150
sudo ha-manager remove ct:160
sudo ha-manager remove ct:151
sudo ha-manager remove vm:154

# Remove HA rules (Proxmox 9+ with node-affinity rules)
# List current rules first to see what exists
sudo ha-manager rules list
# Remove rules by name (check proxmox_ha_rules in group_vars/all.yml for current rule names)
for r in affinity-dns-01 affinity-smtp-relay affinity-dns-02 affinity-home-assistant; do
  sudo ha-manager rules remove "$r" || true
done
EOF
```

---

## Answers to Design Questions

### 1. Bootstrap Procedure

**Exact sequence for creating eric user and deploying SSH keys:**

1. SSH as root to new host (using password from Proxmox installation)
2. Create user eric: `useradd -m -s /bin/bash -G sudo eric`
3. Configure passwordless sudo: `echo 'eric ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/eric`
4. Create SSH directory: `mkdir -p /home/eric/.ssh && chmod 700 /home/eric/.ssh`
5. Deploy SSH key from 1Password
6. Set permissions: `chmod 600 /home/eric/.ssh/authorized_keys && chown -R eric:eric /home/eric/.ssh`
7. Test SSH as eric from workstation

The bootstrap script automates this entire process.

### 2. Cluster Formation Order

**Create cluster first, then add nodes one at a time.**

- Create cluster on pve-nas-01 (existing, most important node)
- Join existing node pve-opt-03 first (already has VMs, proves join works)
- Join new nodes one by one, verifying cluster health between each
- Never join multiple nodes simultaneously

### 3. k3s Expansion Order

**Yes, servers MUST be added before agents. Order matters for etcd:**

1. Add k3s-srv-laptop-01 (server 2) - wait for etcd sync
2. Add k3s-srv-prec-01 (server 3) - establishes 3-node quorum
3. Add all agents (can be parallel after quorum is established)

The k3s role handles this automatically based on `k3s_is_first_server` flag.

### 4. HA Migration Testing

**Safest approach:**

1. Start with DNS (redundant via dns-01/dns-02)
2. Use `ha-manager migrate` command (not `fence` or host shutdown)
3. Monitor with continuous ping to service IP
4. Test one service at a time
5. Full host failure testing only after all individual migrations work

### 5. Inventory Structure

**Move hosts from `proxmox_unmanaged` to `proxmox` group:**

- Remove the `proxmox_unmanaged` group entirely
- All 6 hosts go in `proxmox` group with `proxmox_role` variable
- `proxmox_role: nas` for pve-nas-01, `proxmox_role: compute` for all others
- `ansible_connection: local` removed (only used for unmanaged hosts)

### 6. Storage Migration for Existing VMs

**Settled — the question this section asked has an answer, recorded here so a
rebuild does not re-open it.**

The HA guests were moved off `local-lvm` onto the compute hosts' `local-ssd`
ZFS pools, which is what makes them replicable: home-assistant (VM 154) runs on
pve-prec-01 with `proxmox_storage: local-ssd` (host_vars/home.yml), as do the
dns and smtp-relay containers.

The two k3s VMs on pve-nas-01 (k3s-srv-nas-01 VM 222, k3s-agt-nas-01 VM 202)
**deliberately stay on `local-lvm`** and are not HA-managed: VM 222 is an etcd
quorum member and pve-nas-01's ZFS `ssd` pool is encrypted, so a root disk there
would put cluster quorum behind a boot-time key unlock. The reasoning and the
blast radius of that choice are in `docs/32-zfs-encryption.md`. Plex (CT 152)
stays on pve-nas-01 for its bind mounts, with its sensitive `/config` bound from
the encrypted `ssd/appdata`.

Migration procedure, if a future guest does need to move:
```bash
# Via Web UI: VM > Hardware > Disk > Disk Action > Move Storage
# Or CLI (target the host's own pool — local-ssd on compute, ssd on the NAS):
qm move-disk <vmid> scsi0 local-ssd --delete
```

---

## Related documentation

- `docs/00-hardware-setup.md` - Initial Proxmox installation
- `docs/18-bootstrap-new-systems.md` - LXC/VM bootstrap procedures
- `docs/19-k3s-deployment.md` - K3s deployment workflow
- `docs/25-multi-node-expansion.md` - Expansion architecture and planning
- [Proxmox Cluster Manager](https://pve.proxmox.com/wiki/Cluster_Manager)
- [Proxmox HA Manager](https://pve.proxmox.com/wiki/High_Availability)
- [Proxmox ZFS Replication](https://pve.proxmox.com/wiki/Storage_Replication)
