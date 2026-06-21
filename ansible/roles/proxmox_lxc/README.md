# Proxmox LXC Role

Provisions unprivileged LXC containers on Proxmox VE. Supports bind mounts, GPU passthrough, UID/GID mapping, and bootstraps the eric user for Ansible access.

## What This Role Manages

- Automatic storage selection based on Proxmox host role
- Resource pool creation and validation
- LXC container creation (Debian Trixie)
- Unprivileged containers with security
- Bind mounts (media directories)
- GPU passthrough (/dev/dri for transcoding)
- UID/GID mapping for host file access
- Eric user bootstrap for Ansible
- Autostart configuration

## Storage Selection

Storage is automatically selected based on the Proxmox host's role:

| Host Role | Default Storage | Use Case |
|-----------|-----------------|----------|
| `nas` | `ssd` | pve-nas-01: 3x 4TB SSDs (raidz1) |
| `compute` | `local-ssd` | Compute nodes: 1TB SSD per host |
| `general` | `local-ssd` | Same as compute |

Override per-container by setting `lxc_storage` in the inventory.

## Configuration

```yaml
# In hosts.yml
plex:
  vmid: 152
  proxmox_host: pve-nas-01
  # lxc_storage: ssd  # Optional: auto-selected based on host role
  lxc_cores: 4
  lxc_memory: 4096
  lxc_disk_size: 32G
  lxc_bind_mounts:
    - host_path: /mnt/media
      container_path: /media
      options: "mp=/media,ro=0"
    - host_path: /mnt/ssd/appdata/plex
      container_path: /config
      options: "mp=/config,backup=1"
  lxc_gpu_passthrough: true
  proxmox_autostart_enabled: true
```

## Reconciliation vs. create-time-only

| Setting | Behaviour |
|---------|-----------|
| `onboot` / `startup` (order, delay) | **Reconciled** on existing containers — editing `proxmox_autostart_enabled` / `proxmox_startup_order` / `proxmox_startup_delay` and re-running applies them via an idempotent `pct set` (metadata-only, next-boot). |
| `eric` SSH `authorized_keys` | **Reconciled** on every run — a rotated `SSH_PUBLIC_KEY` propagates idempotently (atomic temp-file swap, only rewrites on content change). No longer a one-shot create-time `>` overwrite. |
| NIC `firewall=1` flag | **Reconciled** on existing containers. |
| Bind mounts (`lxc_bind_mounts`), UID/GID `lxc.idmap`, GPU `/dev/dri` passthrough | **Create-time only.** Changing them in inventory does **not** reconcile onto an existing container — live idmap/mount changes are risky and out of scope. Recreate the container, or edit `/etc/pve/lxc/<id>.conf` and `pct restart <id>` manually. |

## Deployment

```bash
# Provision Plex LXC
ansible-playbook ansible/playbooks/plex.yml --tags provision

# Provision DNS LXCs
task dns:deploy --tags provision
```

## Files

- `tasks/main.yml` - Main orchestration
- `defaults/main.yml` - Defaults

## Dependencies

- Proxmox host must be accessible
- For bind mounts: host paths must exist

## Security

- Unprivileged containers (mapped UIDs)
- UID/GID mapping for file access
- Eric user with sudo for Ansible
