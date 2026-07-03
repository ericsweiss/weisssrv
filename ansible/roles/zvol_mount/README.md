# Role: zvol_mount

Mounts ZFS zvol-backed block devices inside Proxmox guests via UUID-based
fstab entries. Used by guests that need persistent ZFS-backed storage
attached as a virtual disk (k3s VMs that host hostPath PVs, the GitLab VM's
repo storage, the Authentik/Mealie Postgres zvols).

Linux device naming (`/dev/sdX`) is not deterministic across reboots — this
role discovers each disk by its stable QEMU SCSI ID
(`/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_drive-scsi{N}`), formats once
(only when no filesystem exists) using `lsblk -no FSTYPE` to detect prior
formatting, then writes `UUID=<id>` entries to `/etc/fstab` so mounts
survive device renumbering. The role also detects + corrects disks mounted
at the wrong location (controlled by `zvol_mount_fix_wrong_locations`,
default `true`).

## Inputs

The role consumes a single per-host list `zvol_mount_disks`, typically
populated alongside `vm_additional_disks` in `host_vars`:

```yaml
# Per-host: declared in inventory (host_vars), consumed here.
zvol_mount_disks:
  - name: authentik-postgres
    mount_point: /mnt/postgres-data
    fstype: ext4
    # scsi_slot: 1   # optional; defaults to loop index + 1 (so first entry
                     # maps to scsi1, second to scsi2, ...). Set explicitly
                     # when VM hardware edits could reorder slots.
  - name: mealie-postgres
    mount_point: /mnt/mealie-postgres-data
    fstype: ext4
```

Per entry:

| Key          | Required | Meaning                                                          |
|--------------|----------|------------------------------------------------------------------|
| `name`       | yes      | Human label used in task output                                  |
| `mount_point`| yes      | Absolute path to mount under                                     |
| `fstype`     | yes      | Expected filesystem (ext4, xfs, ...); enforced by assert         |
| `scsi_slot`  | no       | Explicit QEMU SCSI slot; defaults to list index + 1              |

> **Production contract:** here `zvol_mount_disks` is always aliased to
> `vm_additional_disks` (k3s.yml, gitlab.yml), where every entry carries an
> explicit `scsi_slot`. proxmox_vm requires and asserts a unique slot per disk
> because attaching by list position can remap a live zvol. The `idx + 1`
> fallback above is only for standalone / molecule use with sequential slots —
> do not rely on it for guests holding persistent data.

## Deployment

The role is included by the playbooks that own the hosts (`k3s.yml`,
`gitlab.yml`), neither of which uses `tags:` — scope by host:

```bash
task k3s:deploy -- --limit k3s-agt-nas-01
task gitlab:deploy
```

## Safety

- Refuses to format a disk that already has a filesystem (unless overridden)
- Writes fstab entries by UUID, never by `/dev/sdX`
- Runs the format step only when not in `--check` mode

## See also

- `docs/06-zfs.md` — ZFS zvol creation on the NAS host
- `ansible/roles/proxmox_vm/` — provisions the zvols + attaches them to VMs
