# Proxmox LXC Role

Provisions unprivileged LXC containers on Proxmox VE. Supports bind mounts, GPU passthrough, UID/GID mapping, and bootstraps the eric user for Ansible access.

## What This Role Manages

- Resource pool creation and validation
- LXC container creation (Debian Trixie)
- Unprivileged containers with security
- Bind mounts (media directories)
- GPU passthrough (/dev/dri for transcoding)
- UID/GID mapping for host file access
- Eric user bootstrap for Ansible
- Autostart configuration

## Configuration

```yaml
# In hosts.yml
plex:
  vmid: 152
  proxmox_host: pve-nas-01
  lxc_cores: 4
  lxc_memory: 4096
  lxc_disk_size: 32G
  lxc_bind_mounts:
    - "/tank/media/unified,mp=/mnt/media,ro=1"
  lxc_gpu_passthrough: true
  proxmox_autostart_enabled: true
```

## Deployment

```bash
# Provision Plex LXC
ansible-playbook ansible/playbooks/plex.yml --tags provision

# Provision DNS LXCs
task deploy:dns --tags provision
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
