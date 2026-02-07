# Proxmox VM Role

Provisions Debian VMs on Proxmox VE using cloud-init. Handles networking, storage, resource pool assignment, and optional persistent ZFS zvol disks.

## What This Role Manages

- Resource pool creation and validation
- Cloud-init template download (Debian Trixie)
- VM creation with proper VMID, CPU, memory, disk
- Cloud-init configuration (user, SSH keys, networking)
- Additional persistent disks (ZFS zvols for databases)
- Autostart configuration (order, delay)
- VM start after provisioning

## Configuration

```yaml
# In hosts.yml
k3s-agt-nas-01:
  vmid: 202
  proxmox_host: pve-nas-01
  proxmox_storage: local-lvm
  proxmox_resource_pool: platform
  vm_cpu_type: host
  vm_cores: 4
  vm_memory: 8192
  vm_disk_size: 64G
  vm_additional_disks:
    - name: postgres-data
      size: 10G
      zvol: ssd/appdata/authentik/postgres
      mount_point: /mnt/postgres-data
      fstype: ext4
  proxmox_autostart_enabled: true
  proxmox_startup_order: 40
  proxmox_startup_delay: 10
```

## Deployment

```bash
# Provision k3s VMs
task k3s:provision-vms

# Provision specific VM
ansible-playbook ansible/playbooks/k3s-provision-vms.yml --limit k3s-srv-nas-01
```

## Files

- `tasks/main.yml` - Main orchestration
- `defaults/main.yml` - Default values

## Dependencies

- Proxmox host must be accessible
- Cloud-init template must be downloadable

## Notes

- Cloud-init user: eric (with SSH key)
- Network: DHCP by default, then set static via cloud-init
- Persistent zvols survive VM recreation
