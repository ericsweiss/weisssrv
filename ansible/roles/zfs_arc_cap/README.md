# zfs_arc_cap

Caps the ZFS ARC on the **compute** Proxmox hosts (`proxmox_role: compute`).

This is the compute-host counterpart to the ARC cap `nas_storage` applies on
`pve-nas-01` (via its own `zfs_arc_max_bytes`, on the storage playbook
lifecycle). The compute hosts run the `local-ssd` pool but had no codified ARC
cap. That became load-bearing when `pve-prec-01` gained a VFIO GPU-passthrough
guest whose 30 GiB is **host-pinned/mlock'd** (non-reclaimable): on a 62 GiB
host an uncapped ARC (~½ RAM ≈ 31 GiB) would collide with the pinned VM and
thrash. See `docs/43-gpu-passthrough.md`.

## What it does (only when `zfs_arc_cap_max_bytes` is set)

1. Renders `/etc/modprobe.d/zfs.conf` with `options zfs zfs_arc_max=<bytes>`.
   (Same filename `nas_storage` uses — the two roles never run on the same host,
   and on `pve-prec-01` this cleanly supersedes any prior manual cap.)
2. Notifies a **Rebuild initramfs** handler so the load-time module parameter is
   baked into the initramfs (the `local-ssd` pool imports at early boot).
3. Writes the running kernel's `/sys/module/zfs/parameters/zfs_arc_max`
   (compare-then-set) so a changed cap is live immediately, no reboot.

Empty (`zfs_arc_cap_max_bytes: ""`, the default) → the role manages nothing.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `zfs_arc_cap_max_bytes` | `""` | Byte count for `zfs_arc_max`. Empty = no-op. Set on the compute group in `group_vars/proxmox.yml`. |
| `zfs_arc_cap_skip_initramfs` | `false` | Molecule/check-mode: render the modprobe.d file but skip `update-initramfs` (no real `/boot`). |

## Where it runs

The `proxmox` play in `playbooks/site.yml` includes this role gated
`when: proxmox_role != 'nas'`, so it targets only the five compute hosts.
`pve-nas-01`'s ARC is owned by `nas_storage`. Deployed via
`deploy-ansible-proxmox` (CI) or `task infra:deploy`.

## Runtime cleanup note

If a host carried a manual `zfs_arc_max` in a **differently-named**
`/etc/modprobe.d/*.conf`, remove it by hand once this role owns `zfs.conf` —
otherwise two files set the parameter and the *load-time* value is ambiguous
(the running value is still made authoritative by the sysfs task).
