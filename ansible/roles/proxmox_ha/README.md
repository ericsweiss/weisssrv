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

Variables are defined in `group_vars/all.yml`:

```yaml
# HA Rules - Node affinity (Proxmox 9+)
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
    strict: false  # Allow pve-nas-01 only if ALL other nodes unavailable
    comment: "Exclude pve-nas-01 to avoid I/O contention"
    enabled: true

# HA Resources - VMs/containers managed by HA
ha_resources:
  - type: ct
    vmid: 150
    state: started
    comment: "dns-01 (AdGuard Home primary)"
    enabled: true

# Storage Replication - Multi-target ZFS replication
storage_replication_jobs:
  - id: "150-0"
    source_node: pve-laptop-01
    target_node: pve-opt-01
    schedule: "*/15"  # Every 15 minutes
    comment: "dns-01 -> pve-opt-01"
    enabled: true
```

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
