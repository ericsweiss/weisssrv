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

This role codifies both.

## Variables

- `nic_tuning_ip_forward` (default `false`) — write
  `/etc/sysctl.d/99-nic-tuning-ip-forward.conf` with `net.ipv4.ip_forward=1`
  and run `sysctl --system`.
- `nic_tuning_overrides` (default `[]`) — list of per-interface dicts:
  ```yaml
  nic_tuning_overrides:
    - interface: nic1
      options:
        - feature: gro
          value: "off"
  ```
  Writes `/etc/network/interfaces.d/99-nic-<iface>-tuning.cfg` with a
  `post-up /sbin/ethtool -K ...` line per option, and applies the overrides
  immediately via the `ethtool` command (idempotent).

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
