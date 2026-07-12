# Proxmox HA Role

Configures Proxmox VE High Availability for VMs and containers. Manages HA rules (node affinity), HA resources, and ZFS storage replication.

## What This Role Manages

- **HA Rules** (Proxmox 9+): Node-affinity rules that restrict which nodes can run specific VMs/containers
- **HA Resources**: Registers VMs/containers with the HA manager for automatic restart and migration on failure
- **Storage Replication**: ZFS replication jobs for multi-target data replication (fast failover)

## Requirements

- Proxmox VE cluster must be configured and quorate
- Run from any cluster member (typically pve-nas-01)
- For replication: All target nodes must have matching ZFS storage (e.g., local-ssd)

## Configuration

Variables are defined in `group_vars/all.yml` — the `ha_rules` /
`ha_resources` / `storage_replication_jobs` blocks there are the canonical,
current examples. Representative shapes:

```yaml
# HA Rules - one node-affinity rule PER SERVICE (Proxmox 9+).
# Nodes carry explicit priorities: the ":2" home wins whenever it's available
# (fail-back after an outage), the ":1" entries are ranked fallbacks.
ha_rules:
  - name: affinity-dns-01
    type: node-affinity
    resources:
      - ct:150  # dns-01
    nodes:
      - "pve-opt-01:2"  # home — service fails back here when available
      - "pve-opt-02:1"
      - "pve-opt-03:1"
      - "pve-prec-01:1"
    strict: false  # Allow pve-nas-01 only if ALL listed nodes are unavailable
    comment: "dns-01 home pve-opt-01; never pve-nas-01"
    enabled: true

# HA Resources - VMs/containers managed by HA
ha_resources:
  - type: ct
    vmid: 150
    state: started
    comment: "dns-01 (AdGuard Home primary)"
    enabled: true

# Storage Replication - Multi-target ZFS replication.
# Schedules are explicit staggered minute lists (not "*/15") so each service
# gets a deterministic offset and the four services never replicate at once.
storage_replication_jobs:
  - id: "150-0"
    source_node: pve-laptop-01
    target_node: pve-opt-01
    schedule: "0,15,30,45"   # dns-01 slots; the next service uses "3,18,33,48"
    comment: "dns-01 -> pve-opt-01"
    enabled: true
```

Only `type: node-affinity` rules are supported — the role asserts this and
fails loud on any other rule type.

## Deployment

```bash
# Check what would change
task proxmox:ha-check

# Apply HA configuration
task proxmox:ha

# View current HA status
task proxmox:ha-status
```

## Files

- `tasks/main.yml` - Main orchestration (validates cluster, includes sub-tasks)
- `tasks/rules.yml` - Manages HA rules (node-affinity)
- `tasks/resources.yml` - Manages HA resources (VMs/containers)
- `tasks/replication.yml` - Manages storage replication jobs
- `defaults/main.yml` - Default empty lists for all variables

## Manual Commands

For troubleshooting or manual operations:

```bash
# Check HA status
ssh pve-nas-01 "sudo ha-manager status"

# List HA rules
ssh pve-nas-01 "sudo ha-manager rules list"

# View HA config
ssh pve-nas-01 "sudo ha-manager config"

# Manual migration
ssh pve-nas-01 "sudo ha-manager migrate ct:150 pve-laptop-01"

# Check replication
ssh pve-nas-01 "sudo pvesr status"
```

## Design Decisions

**Why multi-target replication?**
- Each service replicates to ALL other nodes with local-ssd storage
- HA can failover to ANY available node, not just one designated backup
- 15-minute schedule balances data freshness vs. overhead

**Why exclude pve-nas-01?**
- NAS workloads (ZFS, NFS, Samba) cause I/O contention
- Critical services (DNS, SMTP, HA) run better on dedicated compute nodes
- `strict: false` allows NAS as last resort if all compute nodes fail

**Why not HA for Plex or k3s VMs?**
- Plex depends on NAS bind mounts (cannot run elsewhere)
- k3s handles node failures at the application layer (pod rescheduling)

## Dependencies

- Proxmox VE cluster must be quorate
- ZFS storage pools must exist on all replication targets
