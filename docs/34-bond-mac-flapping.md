# Bond MAC-Flap Black-Hole (active-backup + unmanaged switch)

A recurring, intermittent network black-hole on HA-managed guests (dns-01,
dns-02, smtp-relay, home-assistant) — historically misdiagnosed as a "dumb
switch / MAC-flapping" hardware problem. The root cause is a bonding option,
not the switch. This runbook documents the diagnosis, the immediate recovery,
and the permanent fix (codified in the `nic_tuning` role).

> **Two different opt-node network faults live in this file.** If the *whole
> host* went dark (dropped out of the Proxmox cluster, needed a power-cycle),
> this is **not** the bond bug — jump to
> [e1000e TX Hardware Unit Hang](#e1000e-tx-hardware-unit-hang-whole-host-goes-dark).
> The bond bug below black-holes a *guest* while the host and its co-resident
> guests stay reachable.

## Symptom

- A guest on a **bonded** Proxmox host (`.104` / `.105` / `.106`) loses all
  traffic that crosses the physical uplink — it cannot ping its gateway
  (`10.0.10.1`) or hosts on other nodes, and external clients cannot reach it.
- Traffic to **co-resident** guests on the same host still works, so it looks
  partial/flaky rather than "down".
- Recurs after reboots and HA relocations. Rebooting the unmanaged switch
  "fixes" it temporarily (it flushes the switch's MAC table), which wrongly
  points the finger at the switch.
- DNS-specific fallout: CoreDNS round-robins to both `.150` and `.160`, so when
  dns-02 is black-holed, ~half of in-cluster lookups time out and CI/pods flake
  on DNS.

## Root cause

The bonded hosts carried `all_slaves_active=1` in **two** places — the visible
`/etc/network/interfaces` stanza and, decisively, the bonding **module option**
in `/etc/modprobe.d/bonding.conf` (which is what the kernel actually applies at
boot, before ifupdown2 runs):

```
# /etc/network/interfaces
iface bond0 inet manual
    bond-slaves nic0 nic1
    bond-mode active-backup
    bond-all_slaves_active 1      # <-- ignored by ifupdown2 (see "Permanent fix")

# /etc/modprobe.d/bonding.conf
options bonding fail_over_mac=1 all_slaves_active=1   # <-- the real boot-time bug
```

Both bond legs plug into the same **unmanaged** switch. In `active-backup` only
one leg transmits, but the switch floods the guest's own frames (and broadcasts)
back to the host on the **other** (inactive/backup) leg. With
`all_slaves_active 1` the bonding driver **delivers** those inbound frames to
`vmbr0` instead of dropping them. `vmbr0` then learns the guest's MAC on `bond0`
(the uplink) rather than on the guest's `fwpr<vmid>p0` veth — so every unicast
**reply** to the guest (the gateway's ARP reply, DNS responses) is forwarded
back **out to the switch** instead of down to the container. The guest's own
egress still floods outward fine, which is why every *other* host has learned
its MAC while its return path is dead.

The kernel default, `all_slaves_active 0`, **drops** frames received on an
inactive slave — which is exactly what an active-backup bond on a shared switch
needs.

### Confirming the diagnosis

Start with the fleet sweep — it reports bond `all_slaves_active`, ARP/FDB state
for the HA guest IPs and VIPs, corosync, kube-vip and MetalLB in one pass:

```bash
task diagnose:network
```

Then, on the guest's host, watch the guest MAC flap in the bridge FDB while
pinging its gateway from the guest:

```bash
# <MAC> = the guest's hwaddr (pct config <vmid> | grep net0), <vmid> the CTID
for i in $(seq 6); do
  bridge fdb show br vmbr0 | grep -i <MAC>   # flaps between fwpr<vmid>p0 and bond0
  sleep 1
done
```

A tcpdump makes it unambiguous — the gateway's ARP reply is visible on `vmbr0`
but never reaches the guest-side veth (`veth<vmid>i0`):

```bash
tcpdump -i vmbr0     -e -n 'ether host <MAC> and arp'   # reply present here
tcpdump -i veth<vmid>i0 -e -n arp                       # reply MISSING here
```

## Immediate recovery (no reboot, no link blip)

`all_slaves_active` is runtime-tunable via sysfs — the fix applies live:

```bash
echo 0 | sudo tee /sys/class/net/bond0/bonding/all_slaves_active
```

The FDB flap stops immediately and the guest's return path is restored. Verify:

```bash
sudo pct exec <vmid> -- ping -c2 10.0.10.1   # gateway now answers
```

Apply to every bonded host (`.104`, `.105`, `.106`).

## Permanent fix (codified)

The `nic_tuning` role enforces `all_slaves_active=0` on every `active-backup`
bond (`nic_tuning_bond_asa_guard`, default `true`) across three layers:

- **Boot-time control** (`/etc/modprobe.d/bonding.conf`): surgically flips a
  stale `all_slaves_active=1` → `0` in the bonding module options, preserving
  `fail_over_mac`. This is what actually decides the value at boot — the module
  default is applied when bonding loads, *before* ifupdown2 runs. **This was the
  missing piece**: the fleet's `bonding.conf` still carried the legacy `=1`, so
  despite the interfaces rewrite below the kernel value reverted to `1` on every
  reboot (ifupdown2 does not honor the `bond-all_slaves_active` stanza). Applies
  on the next module load (reboot); no initramfs rebuild — bonding loads at
  network-up from the live `/etc/modprobe.d`.
- **Interfaces stanza** (belt-and-suspenders): surgically rewrites an explicit
  `bond-all_slaves_active 1` → `0` in `/etc/network/interfaces` (an
  `ansible.builtin.replace`; never inserts a line, never reloads networking, so
  the hand-maintained file is untouched and the uplink does not blip). Harmless
  where ifupdown2 ignores it; a safety net if a version ever honors it.
- **Apply live**: writes `0` to
  `/sys/class/net/<bond>/bonding/all_slaves_active` for any active-backup bond,
  so a `task` run fixes a host without a reboot.

Deploy with the role's usual path (`base.yml` / `site.yml`, tag `network`):

```bash
ansible-playbook ansible/playbooks/site.yml --tags network --limit 'pve-opt-*'
```

`nic_tuning` runs on the Proxmox hosts and is a no-op on non-bonded hosts
(`.102` / `.103` / `.107` have single-NIC uplinks) and inside containers.

## Notes

- `active-backup` is the correct bond mode here — it needs no switch-side
  configuration, unlike LACP/802.3ad, which the unmanaged switch cannot do.
  Only `all_slaves_active` was wrong.
- If the fleet ever moves to a managed switch with proper LACP, revisit this;
  `all_slaves_active` is specific to the active-backup-on-dumb-switch topology.
- Gratuitous ARP (`arp_notify`) was investigated and ruled out — the guests'
  ARP was working; the problem was the bridge FDB being poisoned, not a stale
  switch entry.

---

## e1000e TX Hardware Unit Hang (whole host goes dark)

A **separate** fault, first seen on the three OptiPlex hosts (`pve-opt-01` .104,
`pve-opt-02` .105, `pve-opt-03` .106). `pve-prec-01` (.107) carries the same
driver — an I219-LM `e1000e` at PCI `00:1f.6` rather than `00:19.0` — and is
covered by the same fix.

### Symptom

- The **entire host** stops answering: it drops out of the Proxmox cluster
  (`pvecm status` on a peer shows it offline), its guests are unreachable, and
  only a power-cycle recovers it. Contrast the bond bug above, where the host
  and its other guests keep working.
- No fail-over happens: the driver cannot reset the wedged TX unit and the
  **link stays up**, so the active-backup bond sees a healthy leg and never
  switches to `nic1`.

### Confirming the diagnosis

The signature is in the journal on the affected host, and (because
`alloy_host` ships journald to Loki) it survives the power-cycle:

```bash
# On the host after recovery
sudo journalctl -k -b -1 | grep -i "Hardware Unit Hang"

# Or fleet-wide in Grafana -> Explore -> Loki (survives a reboot).
# The journal stream label is job="journal" (alloy_host sets it; there is no
# "systemd-journal" job in this Loki) — a wrong label returns an EMPTY result,
# which reads as "not the e1000e hang" during exactly this incident:
#   {job="journal"} |= "Detected Hardware Unit Hang"
# Narrow to one host with e.g. {job="journal", host="pve-opt-01"}.
```

```
e1000e 0000:00:19.0 nic0: Detected Hardware Unit Hang:
  TDH   <x>
  TDT   <y>
  ...
```

Ruled out during diagnosis: swap/OOM pressure, the `all_slaves_active` bond bug
above, and the switch. The hang is reported by the driver for the **onboard
Intel e1000e NIC only** (`nic0`, PCI `00:19.0`); `nic1` (the second bond leg) is
a different controller and was never implicated.

### Fix (codified)

`tso`/`gso`/`gro` **off** on `nic0` — the standard e1000e cure for the TX-hang
class. Codified per host in
`ansible/inventories/prod/host_vars/pve-opt-0{1,2,3}.yml` and
`host_vars/pve-prec-01.yml`:

```yaml
nic_tuning_overrides:
  - interface: nic0
    options:
      - feature: tso
        value: "off"
      - feature: gso
        value: "off"
      - feature: gro
        value: "off"
```

`nic_tuning` applies the change live with `ethtool` (no link blip, no reboot)
and persists it through an ifup drop-in, so it survives reboots. Deploy the
same way as the bond fix:

```bash
ansible-playbook ansible/playbooks/site.yml --tags network \
  --limit 'pve-opt-*,pve-prec-01'
```

Verify on the host: `ethtool -k nic0 | grep -E 'tcp-segmentation|generic-(segmentation|receive)'`
— all three `off`.

### Notes

- The TSO/GSO/GRO fix covers the four `e1000e` hosts (.104/.105/.106/.107).
  `.103` has no `nic_tuning_overrides` at all (`nic_tuning` is override-driven,
  so an empty list is a no-op). `.102` is not override-free: it carries an
  unrelated `gro off` on `nic1` for the AQC113 10GbE NIC (a stability
  workaround; the pending firmware update is in
  [docs/16](16-next-steps.md#aqc113-firmware-update-pve-nas-01)), so an audit of
  NIC tuning must not skip it. On .107 the
  offloads were already off live but nothing persisted them, so the host_vars
  entry is what makes the setting survive a reboot.
- Turning off segmentation offload costs some CPU per gigabit; on these hosts
  that is irrelevant next to an unattended power-cycle.

## Related documentation

- [docs/01-overview.md](01-overview.md) - host/node topology and NIC placement
- [docs/11-firewall.md](11-firewall.md) - Proxmox firewall groups on the same hosts
- [docs/12-runbooks.md](12-runbooks.md) - operational procedures
- [docs/16-next-steps.md](16-next-steps.md) - open NIC/firmware follow-ups
- [docs/31-observability.md](31-observability.md) - the Loki/Grafana path used to diagnose this
