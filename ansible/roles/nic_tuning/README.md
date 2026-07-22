# nic_tuning

Per-NIC tuning for homelab Proxmox hosts and anywhere else a persistent
`ethtool -K` setting or reliable `ip_forward=1` is needed.

## Background

- **pve-nas-01** has an Aquantia AQC113 10G NIC. With GRO enabled on a bridged
  interface (vmbr0 uses nic1), the atlantic driver deadlocks the receive path
  and the host goes network-dead without losing power. Outages observed
  2026-04-04 and 2026-04-07/14; manual `ethtool -K nic1 gro off` fixed it.
- `pve_firewall` on Proxmox occasionally resets `net.ipv4.ip_forward`, which
  breaks Tailscale subnet routing. A sysctl.d drop-in keeps the value sticky.
- **pve-nas-01** is memory-committed: it runs the app VMs
  (Nextcloud/Immich/Windows/GitLab) plus ZFS ARC, and at the kernel default
  `vm.swappiness=60` it thrashed swap under pressure. A sysctl.d drop-in lowers
  swappiness so the host reclaims page cache / balloons guest RAM before
  swapping (pairs with the VM ballooning floors in `hosts.yml`).
- Bonded hosts (`.104/.105/.106`) run `bond-mode active-backup` with both legs
  on the same **unmanaged** switch. `bond-all_slaves_active 1` made the driver
  deliver frames received on the *inactive* backup leg; the switch floods a
  guest's own frames back onto that leg, so the host bridge learns the guest
  MAC on `bond0` instead of its veth and misdirects the guest's return traffic
  out to the switch — an intermittent "MAC-flapping" black-hole that recurs on
  reboots/HA-moves and hit dns-02 (and any guest that lands on a bonded host).
  Full diagnosis + recovery: `docs/34-bond-mac-flapping.md`.

This role codifies all four.

## Variables

- `nic_tuning_ip_forward` (default `false`) — write
  `/etc/sysctl.d/99-nic-tuning-ip-forward.conf` with `net.ipv4.ip_forward=1`
  and apply it via a scoped `ansible.posix.sysctl` reload of just
  `net.ipv4.ip_forward` from that drop-in (deliberately not `sysctl --system`,
  so an unrelated bad entry elsewhere in `/etc/sysctl.d/` can't fail the apply).
- `nic_tuning_vm_swappiness` (default `null`) — set to an integer `0`-`100`
  per host/group to write `/etc/sysctl.d/99-nic-tuning-swappiness.conf` with
  `vm.swappiness=<n>` and apply it via a scoped `ansible.posix.sysctl` reload
  of just `vm.swappiness` (same isolation rationale as `ip_forward`). Leave
  `null` to leave swappiness unmanaged. pve-nas-01 sets `1` (paired with the
  4 GiB ARC cap + daily swap-reset to keep swap flat).
- `nic_tuning_overrides` (default `[]`) — list of per-interface dicts:
  ```yaml
  nic_tuning_overrides:
    - interface: nic1
      options:
        - feature: gro
          value: "off"
  ```
  Writes `/etc/network/interfaces.d/99-nic-<iface>-tuning.cfg` — an
  `iface <iface> inet manual` stanza with a `post-up /sbin/ethtool -K ...`
  line per option — and applies the overrides immediately via the `ethtool`
  command (idempotent). The stanza header is load-bearing: ifupdown2 rejects
  bare `post-up` lines ("error processing line"), which would leave the
  drop-in inert at boot; duplicate `iface` stanzas are merged with the
  interface's declaration in `/etc/network/interfaces`.
- `nic_tuning_bond_asa_guard` (default `true`) — force
  `all_slaves_active=0` on every `active-backup` bond, across three layers:
  - **`/etc/modprobe.d/bonding.conf`** module option — the *real* boot-time
    control. The bonding module default is applied when the module loads (before
    ifupdown2 runs); a stale `all_slaves_active=1` here is why the guard used to
    revert on every reboot. Surgically flips `=1` → `=0`, preserving
    `fail_over_mac`; only touches an existing file.
  - **`/etc/network/interfaces`** stanza — surgical `replace` of
    `bond-all_slaves_active 1` → `0` (never inserts a line, never reloads).
    Belt-and-suspenders: ifupdown2 does **not** honor this stanza, so it is
    harmless but not load-bearing.
  - **live sysfs** `/sys/class/net/<bond>/bonding/all_slaves_active` — applies
    the fix now, without a reboot.

  Idempotent and a no-op on non-bonded hosts. Set `false` only if a bond
  legitimately needs `=1` (multi-switch multicast RX) — this fleet does not.

## Example inventory wiring

```yaml
# ansible/inventories/prod/host_vars/pve-nas-01.yml
nic_tuning_ip_forward: true
nic_tuning_overrides:
  - interface: nic1
    options:
      - feature: gro
        value: "off"
```

For other Proxmox hosts that need ip_forward but no NIC overrides:

```yaml
# ansible/inventories/prod/group_vars/proxmox.yml
nic_tuning_ip_forward: true
```

## Scope

- Does NOT flash NIC firmware. pve-nas-01's AQC113 firmware update from 1.5.38
  to 1.5.45 requires a Windows USB boot (Station-Drivers package) and is a
  manual user task — see `docs/16-next-steps.md`.
