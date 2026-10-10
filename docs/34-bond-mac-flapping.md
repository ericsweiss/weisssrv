# Host network faults: bond MAC-flap, e1000e TX hang, br_netfilter skb_ext leak

A recurring, intermittent network black-hole on HA-managed guests (dns-01,
dns-02, smtp-relay, home-assistant). The cause is the `all_slaves_active`
bonding option, not the switch. This runbook documents the diagnosis, the
immediate recovery, and the permanent fix (codified in the `nic_tuning` role).

> **Three host network faults live in this file.** If the *whole host* went
> dark (dropped out of the Proxmox cluster, needed a power-cycle), this is
> **not** the bond bug — jump to
> [e1000e TX Hardware Unit Hang](#e1000e-tx-hardware-unit-hang-whole-host-goes-dark).
> If memory is disappearing into unreclaimable slab, jump to
> [br_netfilter skb_ext slab leak](#br_netfilter-skb_ext-slab-leak). The bond
> bug below black-holes a *guest* while the host and its co-resident guests
> stay reachable.

## Symptom

- A guest on a **bonded** Proxmox host (`.104` / `.105` / `.106`) loses all
  traffic that crosses the physical uplink — it cannot ping its gateway
  (`10.0.10.1`) or hosts on other nodes, and external clients cannot reach it.
- Traffic to **co-resident** guests on the same host still works, so it looks
  partial/flaky rather than "down".
- Recurs after reboots and HA relocations. Power-cycling the switch "fixes" it
  temporarily by flushing its MAC table.
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

Both bond legs plug into the same switch. In `active-backup` only
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
  `fail_over_mac`. This is what decides the value at boot — the module option is
  applied when bonding loads, *before* ifupdown2 runs, and ifupdown2 does not
  honor the `bond-all_slaves_active` stanza, so the module option is the only
  layer that survives a reboot. Applies
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
task infra:deploy -- --tags network --limit 'pve-opt-*'
```

`nic_tuning` runs on the Proxmox hosts and is a no-op on non-bonded hosts
(`.102` / `.103` / `.107` have single-NIC uplinks) and inside containers.

## Notes

- `active-backup` remains the bond mode. The three bonded hosts land on
  USW-Pro-XG-8-PoE access ports (USW 1-6, [docs/46](46-unifi-network.md)), so
  LACP/802.3ad is available where it was not before. The bond invariant is
  unchanged either way, because `all_slaves_active` is a property of
  active-backup and not of the switch. Re-verification against the new link
  partner is tracked in [docs/16](16-next-steps.md).
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

A wedged TX unit keeps the link up, so the bond cannot fail away from it on its
own. The mitigation for that is `nic_tuning_bond_primary: nic1`, set once in
`ansible/inventories/prod/group_vars/bonded_hosts.yml` for the three bonded
OptiPlex hosts: `nic0` becomes the backup leg, so a hang cannot take the
transmitting leg. `reselect` stays at its `failure` default, so applying it
never blips the live uplink. pve-prec-01 has no bond and keeps the tso/gso/gro
cure alone. `nic_tuning_bond_primary` takes effect once the `weisssrv.infra` pin
carries it and `task infra:deploy` has run.

### Fix (codified)

`tso`/`gso`/`gro` **off** on `nic0` — the standard e1000e cure for the TX-hang
class. Codified once in
`ansible/inventories/prod/group_vars/e1000e_hosts.yml`, a child group of
`proxmox` holding pve-opt-01/02/03 and pve-prec-01:

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
task infra:deploy -- --tags network --limit 'pve-opt-*,pve-prec-01'
```

Verify on the host: `ethtool -k nic0 | grep -E 'tcp-segmentation|generic-(segmentation|receive)'`
— all three `off`.

### Notes

- The TSO/GSO/GRO fix covers the four `e1000e` hosts (.104/.105/.106/.107).
  `.103` has no `nic_tuning_overrides` at all (`nic_tuning` is override-driven,
  so an empty list is a no-op). `.102` is not override-free: it carries an
  unrelated `gro off` on `nic1` for the AQC113 10GbE NIC (a stability
  workaround; the 1.5.48 firmware attempt was closed and the card stays at
  1.5.38), so an audit of NIC tuning must not skip it. On .107 the
  offloads were already off live but nothing persisted them, so the group_vars
  entry is what makes the setting survive a reboot.
- Turning off segmentation offload costs some CPU per gigabit; on these hosts
  that is irrelevant next to an unattended power-cycle.

### Host-dark events without the hang signature

The TSO/GSO/GRO mitigation is verifiably applied on all four `e1000e` hosts, and
no `Detected Hardware Unit Hang` line has appeared since. A host can still go
dark without it: pve-opt-02 went down unexpectedly from 2026-09-09 10:44 to
2026-09-12 13:54, with a clean kernel log on every boot in that window, and came
back on a smart-outlet power cycle. The e1000e hang is ruled out for it by that
clean log, so a repeat means a different fault rather than a regression of this
one. Open follow-up: an MCE/EDAC sweep of the opt nodes, to decide between
memory/CPU and firmware.

Standing check, per opt node, after any unexplained outage:

```bash
sudo journalctl --list-boots        # a boot that ends mid-operation is a freeze
sudo journalctl -k -b -1 | grep -iE 'Hardware Unit Hang|mce|EDAC'
```

No hang line means look elsewhere: MCE/EDAC counters, the BIOS event log, and
the UCG switch-port log for that minute.

If `scripts/diagnose-network-issues.sh` reports `Host unreachable` for every
host and you believe they are up, ICMP is probably filtered. Go over SSH
instead:

```bash
ssh eric@<pve-host> "grep -A 10 'auto vmbr' /etc/network/interfaces"
```

## br_netfilter skb_ext slab leak

Unreclaimable slab grows until a reboot on every Proxmox host, fastest on the
NAS. The standing posture, the `HostSlabLeakSuspected` pager and the retirement
condition live in [docs/06 § Kernel 192-byte slab leak](06-zfs.md). This section
is the diagnosis that got there.

bpftrace tracing showed every attributable allocator balanced while the slab grew
about 500 objects/s, so the tenant was not visible under the merged `:0000192`
alias (which `/proc/slabinfo` displays under a `file_lock_cache` name that is not
the culprit). The first `slub_nomerge` boot named `skbuff_ext_cache`, allocated
by br_netfilter per bridged frame through `skb_ext_add` from
`br_nf_pre_routing`, `br_nf_forward` and `br_flood`.

Growth tracks NFS GETATTR volume because the NFS data plane transits the NAS
bridge, which is why the leak is fleet-wide but slow elsewhere: roughly
230-380 MB per host per day against 2.7-4 GiB/day on the NAS. The signature
matches the historical v5.4 bridge-nf skb_ext leak (commit 895b5c9f206).

The `nas_storage_nfs_disable_delegations` experiment was retired: measurement
showed the leak survives with zero delegations, and the switch caused 15-30 s
SQLite stalls and liveness-kill loops in the *arr apps through server-side
LOCK/LOCKU. Do not resurrect that toggle.

## Related documentation

- [docs/01-overview.md](01-overview.md) - host/node topology and NIC placement
- [docs/11-firewall.md](11-firewall.md) - Proxmox firewall groups on the same hosts
- [docs/12-runbooks.md](12-runbooks.md) - operational procedures
- [docs/16-next-steps.md](16-next-steps.md) - open NIC/firmware follow-ups
- [docs/31-observability.md](31-observability.md) - the Loki/Grafana path used to diagnose this
