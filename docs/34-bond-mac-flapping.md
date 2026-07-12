# Bond MAC-Flap Black-Hole (active-backup + unmanaged switch)

A recurring, intermittent network black-hole on HA-managed guests (dns-01,
dns-02, smtp-relay, home-assistant) — historically misdiagnosed as a "dumb
switch / MAC-flapping" hardware problem. The root cause is a bonding option,
not the switch. This runbook documents the diagnosis, the immediate recovery,
and the permanent fix (codified in the `nic_tuning` role).

## Symptom

- A guest on a **bonded** Proxmox host (`.104` / `.105` / `.106`) loses all
  traffic that crosses the physical uplink — it cannot ping its gateway
  (`192.168.0.1`) or hosts on other nodes, and external clients cannot reach it.
- Traffic to **co-resident** guests on the same host still works, so it looks
  partial/flaky rather than "down".
- Recurs after reboots and HA relocations. Rebooting the unmanaged switch
  "fixes" it temporarily (it flushes the switch's MAC table), which wrongly
  points the finger at the switch.
- DNS-specific fallout: CoreDNS round-robins to both `.150` and `.160`, so when
  dns-02 is black-holed, ~half of in-cluster lookups time out and CI/pods flake
  on DNS.

## Root cause

`/etc/network/interfaces` on the bonded hosts had:

```
iface bond0 inet manual
    bond-slaves nic0 nic1
    bond-mode active-backup
    bond-all_slaves_active 1      # <-- the bug
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

On the guest's host, watch the guest MAC flap in the bridge FDB while pinging
its gateway from the guest:

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
sudo pct exec <vmid> -- ping -c2 192.168.0.1   # gateway now answers
```

Apply to every bonded host (`.104`, `.105`, `.106`).

## Permanent fix (codified)

The `nic_tuning` role enforces `all_slaves_active=0` on every `active-backup`
bond (`nic_tuning_bond_asa_guard`, default `true`):

- **Persist**: surgically rewrites an explicit `bond-all_slaves_active 1` → `0`
  in `/etc/network/interfaces` (an `ansible.builtin.replace`; it never inserts a
  line and never reloads networking, so the hand-maintained file is otherwise
  untouched and the uplink does not blip). Takes effect on the next reboot.
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
