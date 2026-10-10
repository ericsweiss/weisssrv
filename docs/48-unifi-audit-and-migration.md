# UniFi Audit and Migration (2026-08)

> **Status: superseded.** This page is the record of how the UniFi tier was
> brought up and how the homelab was renumbered. Phase 1 (bench pre-provisioning
> and cutover) ran 2026-08-22; Phase 2 (the `192.168.0.0/24` →
> `10.0.10.0/24` renumber) ran 2026-08-25/26. Nothing here is a procedure to
> run today. Current network state — VLANs, zones and policies, wireless, port
> map, reservations, port forwards, site settings, day-2 operations — is
> [docs/46](46-unifi-network.md).

It is kept for two reasons: a later re-address or a gateway rebuild would follow
the same shape, and the 2026-08 configuration audit findings cited across the
repo as `ZBF-xx` / `PORT-xx` / `ADM-xx` / `GW-xx` are defined here and nowhere
else.

If any step is ever re-run, re-verify live state first —
the k3s-api-vip-transition playbook was removed 2026-09-02 and only exists in
git history, and the `/tmp/renumber-*.yml` files were deleted at
Phase 2 step 8.

---

## 2026-08 configuration audit

A configuration audit of the freshly cut-over UniFi tier ran 2026-08-30/31.
Its findings are referenced by ID from [docs/46](46-unifi-network.md) and from
`terraform/unifi/networks.tf`; this table is what those IDs mean.

| ID | Finding | Status |
|---|---|---|
| ADM-01 | The Terraform/CI credential is minted under the local `terraform` admin rather than the ui.com Owner, so it carries no cloud path | Closed — that is the live arrangement, and it is what bounds ADM-07 |
| ADM-07 | Homelab workloads keep `:443` to the console, so any pod that holds the key reaches the login form and the API | Accepted residual — recorded in [docs/46](46-unifi-network.md) § Zones and policies |
| GW-05 | Gateway services beyond `:443` were reachable from the trusted VLANs | Closed — BLOCK rows 24-25 |
| PORT-01 | The access ports run the controller's default **All** port profile, so an attached device can VLAN-hop by emitting tagged frames | Open — a console change; `unifi_device.port_override` is unsafe at provider 0.55.0 (#438/#430/#431). Tracked in docs/16 |
| PORT-06 | A wired Vizio display steered onto IoT sat on APIPA: a per-MAC override forces tagged delivery through a tag-unaware dumb-switch chain | Accepted — it stays on native Home until that drop gets a managed port |
| ZBF-01 | Hairpin NAT from homelab landed back in its own zone and hit the intra-zone Block All, so in-cluster probes of grey-cloud names timed out | Closed — ALLOW row 12 |
| ZBF-02 | The gateway answered on `8080`, `8443`, `8843`, `8880` and `6789` beyond the `22,80,443` the original block named | Closed — BLOCK rows 13-15 widened to all tcp |
| ZBF-03 | Home was silently exempt from the external-resolver block, so a device with a hard-coded resolver lost split-horizon DNS | Closed — Home joined BLOCK rows 16-19 |
| ZBF-04 | `homelab → iot` was zone-wide, so every k3s pod inherited reach into the unauthenticated IoT device APIs (pods SNAT to node addresses in VLAN 10) | Closed — scoped to Home Assistant (`.154`) alone |
| ZBF-06 | The PORT-01 gap seen from the zone-policy side: the All profile lets a device bypass the zone matrix at L2, where no policy can see it | Open — same remediation as PORT-01 |
| ZBF-07 | The trusted-VLAN half of the gateway-console fencing was missing | Closed — BLOCK rows 24-25 |

Two more items from the same pass are tracked outside this table: gateway
SYN-flood protection (`usg.syn_cookies`) is console-owned and off
([docs/46](46-unifi-network.md) § Codified vs manual, docs/16), and pve-nas-01's
`nic1` IPv6 link-local adjacency to the Home VLAN was closed in 2026-09 by
`nic_tuning_disable_ipv6: [nic1]`.

---

## Phase 1 — bring-up and cutover

### Expected breakage (all closed)

The repo landed before the hardware did, and three things were deliberately
wrong in that window. It closed on 2026-08-22; this records how each resolved.

- **`router.esweiss.com` returned 502** while the ASUS served its UI on
  plaintext `:80` and the `vm-ingress` backend expected the UCG's HTTPS UI on
  `:443` (via the `unifi-self-signed` ServersTransport). Resolved at cutover
  when the UCG took over the gateway address. It returned for the renumber
  window between Phase 2 steps 2 and 7: the live EndpointSlice still named
  `192.168.0.1` while the branch was unmerged and Flux suspended, so the 502
  started when the step-2 flip retired that address and healed when step 7's
  reconcile moved the EndpointSlice to `10.0.10.1`. No other route touches this
  backend.

- **`NetworkGearProbeFailed` fired for `10.0.1.2` and `10.0.1.3`.** **Resolved
  2026-08-30.** The switch and AP reservations were applied at the finishing
  v0.13.2 apply, both devices now answer at `10.0.1.2`/`10.0.1.3` (confirmed
  live), and the probes are green. The 2026-08-29 silence has expired. It was
  red until the apply because the entries were codified but not yet *applied*,
  so both devices held ordinary `10.0.1.100+` pool leases; a cabling change
  never affected it. The silence command, kept for reference if the gear is ever
  re-adopted:

  ```bash
  task observability:silence ALERT=NetworkGearProbeFailed DURATION=7d
  ```

  The gateway probe was green from cutover on `192.168.0.1`; Phase 2 moved the
  live target to `10.0.10.1`. During the renumber window it went red between
  Phase 2 steps 2 and 7 — the same interval and mechanism as the
  `router.esweiss.com` bullet above.

- **`unifi-drift-plan` was yellow after the first apply.** The job passes only
  on exit 2, so a yellow badge is drift and a broken plan — auth failure,
  unreachable gateway, state lock — fails red. Its *original* causes are gone
  — the `UniFi Controller` vault item exists, and the gateway is reachable at
  `https://192.168.0.1`. While the root was pinned to module v0.13.0, the
  `setting_preference` provider default made the plan show a standing diff on
  the six networks' DHCP fields (§ Cutover as executed) — cosmetic **and
  enumerable**. The pin is now v0.13.1, so what remains of that diff is the
  one-time in-place `setting_preference` update per network plus the pending
  reservations, all consumed by the first supervised apply.

  **That apply ran (2026-08-30) and retired this entry.** The next scheduled
  `unifi-drift-plan` went **green**, and a yellow from here is real drift or a
  broken credential — investigated, not ignored.

---

## Bench pre-provisioning

Do all of this with the new gear on a bench, on its own uplink, **before**
touching the production cabling. Nothing here is disruptive; the ASUS keeps
serving the house throughout.

1. **First boot.** Power the UCG-Fiber, connect a laptop to a LAN port, and
   walk the setup wizard at <https://unifi.ui.com> (or the gateway's own
   address). Sign in with the ui.com account for remote access, set
   country/timezone, and take the one firmware update the wizard offers.
2. **Turn device auto-upgrade off for the bench window** (Settings → System →
   Updates). The codified end state is `auto_upgrade = true` — device firmware
   updates nightly at 1 AM by choice (operator ruling;
   [docs/46](46-unifi-network.md) § Site settings) — but the window between
   first boot and first apply is exactly when an unattended reboot hurts, so
   leave it off until Terraform re-enables it at the first apply.
3. **Set the management network.** Default network → `10.0.1.1/24`, DHCP
   `10.0.1.100`-`10.0.1.199`. This is the network Terraform will *import*, so
   its name must stay `Default`.
4. **Create the Terraform admin and API key.** Settings → Admins → new admin,
   **local access only**, no 2FA (the provider cannot satisfy an MFA prompt).
   The provider needs full admin rights within the Network application; narrow
   the role no further than that or plans start failing. Then Control Plane →
   Integrations → API Key, minted under *that* admin — not the Owner. Copy
   both.
5. **Store the credentials** (the item titles are what `docs/15-credential-rotation.md` and the
   Taskfile env anchor expect — do not rename them):

   ```bash
   op item create --category login --vault Homelab --title "UniFi Controller" \
     username=terraform url=https://10.0.10.1
   # then fill `password` and `api-key` in the 1Password app — a chosen secret
   # must never be an argv element (below)

   op item create --category login --vault Homelab --title "WiFi Panopticon" \
     --generate-password='letters,digits,32'
   op item create --category login --vault Homelab --title "WiFi DunderMiffLAN" \
     --generate-password='letters,digits,32'
   ```

   The other two PSKs are **chosen**, not generated (TheRevengers keeps the
   existing house PSK so devices roam over untouched; the guest PSK has to be
   readable aloud). A chosen secret must never be an argv element — it lands in
   `~/.zsh_history` and is visible in `ps` for the duration of the call, and a
   `read -rs`-into-`op item edit "password=$psk"` pipeline only fixes the
   history half: the expanded value is still an argument for the duration of
   the edit. So create the item with a generated placeholder and **type the
   real PSK into the 1Password app** (open the item → edit → `password`),
   which never passes the value through a process argument at all:

   ```bash
   for item in "WiFi TheRevengers" "WiFi kugel-tikka-masala"; do
     op item create --category login --vault Homelab --title "$item" \
       --generate-password='letters,digits,32'
   done
   # then replace each password field in the 1Password app
   ```

   `url` is the **production** address (`https://10.0.10.1`). While the
   gateway is still on the bench, override it per invocation with
   `TF_VAR_unifi_api_url=https://<bench-address>` rather than editing the item.

6. **Adopt the switch and the AP** (Settings → Devices). Both come up on the
   Default network by DHCP. Once adopted, read their MACs, give them fixed
   addresses `10.0.1.2` (switch) and `10.0.1.3` (AP), and fill in the commented
   client entries in `terraform/unifi/` so the addresses are codified rather
   than UI state.
7. **Assign the port map** ([docs/46](46-unifi-network.md) § Physical port map)
   in the UI — native VLAN per
   access port, trunk profiles for the DAC uplink, the AP port and Connection A.
   This is the step the provider cannot do safely; take a screenshot of the
   finished port list and keep it with the .unf backup.
8. **Turn on mDNS reflection** (Settings → Networks → Multicast DNS). Casting
   from Home to IoT depends on it. On Network 10.x this is one **site-level**
   control, not the per-network checkbox older guides describe: choose `Auto` to
   reflect across every network, or `Custom` to scope it by service and by the
   networks each service is reflected between. Scope it to homelab, home and iot
   — guest and work have no discovery to do, and reflecting into guest would
   advertise the house's devices to visitors.
9. **Prove the dumb switches pass 802.1Q tags — days before the window, while
   the fallback is free.** The whole Connection A design (native VLAN 20,
   tagged VLAN 10 for pve-nas-01) assumes the unmanaged 10G TP-Link — and the
   1G TP-Link and MoCA pair it feeds — forward tagged frames untouched and
   accept the resulting 1522-byte "baby giant". Most cheap switches do; some
   strip tags, some drop anything over 1518. The first time step 7 of the
   cutover exercises it, NFS, GitLab, Plex, Nextcloud, Immich, an etcd member
   and an agent node are all riding on the answer.

   Test it on the **isolated bench**, never by patching the bench gear into the
   live LAN — the bench UCG serves `192.168.0.1/24` on VLAN 10 and the ASUS
   still holds `192.168.0.1`, so bridging the two segments is the duplicate-
   gateway failure this runbook works hard to avoid. Instead: unplug the 10G
   TP-Link from the house for ten minutes (or use the 1G one), and build the
   chain on the bench —

   `new switch spare port (native 20, tagged 10)` → `dumb switch` → `laptop`

   From the laptop: the untagged interface must get a `10.0.20.x` lease, and a
   VLAN-10 sub-interface must get a `192.168.0.x` one.

   ```bash
   sudo ip link add link <nic> name <nic>.10 type vlan id 10   # macOS: add a VLAN
   sudo ip link set <nic>.10 up                                # service in Network settings
   sudo dhclient <nic>.10                                      # tagged lease → tags survive the dumb switch
   ping -c3 192.168.0.1                                        # the bench UCG on VLAN 10
   ping -M do -s 1472 -c3 192.168.0.1                          # 1522-byte frame survives too
   ```

   All of it must pass. If any of it does not, the fix is a small managed switch
   at the far end of Connection A — decided before the window, not during it.

### First Terraform apply

The order below is the procedure, not a suggestion: applying before the imports
plans a CREATE for a network named `Default` that already exists, and the apply
then fails part-way on the unique-name constraint — on a resource carrying
`prevent_destroy`, in the middle of a maintenance window.

```bash
task terraform:unifi-init

# 1. Import what already exists on the controller. `unifi_network` is the only
#    resource in this provider that accepts a name= import id; the settings
#    singleton imports by SITE NAME, and clients by colon-separated MAC only
#    (no site: prefix, dashes rejected).
task terraform:unifi-import -- 'module.network.unifi_network.this["default"]' name=Default
task terraform:unifi-import -- 'module.network.unifi_setting.site' default

# 2. Plan, and ASSERT: no `create` for unifi_network.this["default"].
task terraform:unifi-plan     # read every line

# 3. Apply ONE custom zone first — see the zone probe below.
task terraform:unifi-apply -- -target='module.network.unifi_firewall_zone.this["homelab"]'

# 4. …probe, then the rest.
task terraform:unifi-apply    # supervised: type "apply", then "yes" at terraform's own prompt
```

**Clients are deliberately not in that list.** The module sets `allow_existing`,
so a create ADOPTS whatever the controller already knows — and on the bench it
knows none of them, because the apartment's devices are still behind the ASUS.
Import a client only once the controller has seen it (after cutover, and only
to have it tracked from the plan rather than adopted on the first apply):
`task terraform:unifi-import -- 'module.network.unifi_client.this["hdhr"]' 00:18:DD:0A:37:45`.

(`terraform/unifi/README.md` § Adopting the live site carries the full import
recipe — every pre-existing network, zone and WLAN, and where the ids come
from.)

**Why the zone probe sits between the two applies.** It is unverified whether
this controller moves a network out of `Internal` when a custom zone claims it,
and the provider neither does it nor detects it. So apply **one** custom zone,
then read the built-in back without writing anything. Two ways, no credentials
gymnastics in either:

```bash
# The controller is authoritative and this works mid-window with nothing but a
# browser: Settings → Security → Zone Matrix → open `Internal` and read its
# network list.

# Or the same fact from the API, with the key already in the vault. The host in
# the URL is the gateway's address AT THAT MOMENT — `192.168.0.1` on cutover
# night, `10.0.10.1` once § Phase 2 step 2 has flipped the subnet; it is also
# the `url` field of the `UniFi Controller` 1Password item:
UNIFI_API_KEY="op://Homelab/UniFi Controller/api-key" op run -- sh -c \
  'curl -sk -H "X-API-KEY: $UNIFI_API_KEY" \
     https://192.168.0.1/proxy/network/v2/api/site/default/firewall/zone' \
  | jq '[.. | objects | select(has("network_ids")) | {name, network_ids}]'
```

If the network disappeared from `Internal`, the controller does the move and
the remaining zones can be applied normally. If it did not, the network sits in
two zones and policy evaluation is ambiguous — stop and resolve it in the UI
before continuing. Never import and manage `Internal` in the same apply as a
custom-zone create; two `unifi_firewall_zone` resources fighting over one
network is a loop, not a diff.

The same membership is visible in Terraform state after the apply, at
`module.network.data.unifi_firewall_zone.builtin["internal"]` — a
module-internal data source, so it is **not** reachable from `terraform
console`, which evaluates in root scope, and reading it needs the state-backend
variables the Taskfile anchor injects. That is what `terraform:unifi-state`
carries, so the state answer is one line too:

```bash
task terraform:unifi-state -- show 'module.network.data.unifi_firewall_zone.builtin["internal"]'
```

Use it as the cross-check, not the primary: it reports what the last refresh
wrote into state, while the `curl` recipe above reads the controller itself.

Also verify at first plan that the built-in zone display names really are
`Internal` / `External` / `Gateway` on this controller, and that the client QoS
rate is named `Default` — both are taken from upstream examples, not from a
guaranteed schema.

---

## Cutover

Disruptive; needs console access to pve-nas-01 and a window where the house can
lose the network. Everything before this point was bench work.

The runbook below is kept as written, because it is the procedure a rebuild
would follow. **It is not what happened on 2026-08-22** — the window was run
without the bench phase, and the deltas and their lessons are in § Cutover as
executed, after step 11. Read that section first if you are about to run this.

**A day before:** drop the ASUS's DHCP lease time to ~5 minutes. Wired devices
behind the dumb switches never see link-down when Connection A moves, so they
do not restart DHCP on their own — a short lease is what makes them re-ask
promptly once the UCG owns the subnet (step 6 covers the ones that still need a
shove).

1. **Back up both sides.** Download the UniFi `.unf` backup (Settings → Control
   Plane → Backups on UniFi OS 5.x — the older Settings → System → Backups path
   is gone) and export the ASUS configuration. The `.unf` is the only fast path
   back to a configured controller, and it currently lives only on the console's
   own storage — an offsite copy and a daily auto-backup cadence are an open item
   (docs/16).
2. **Disarm Proxmox HA — before any cable moves.** Every step below interrupts
   corosync on the *only* ring, and more than half the cluster is off the wire
   at once in step 6, so the surviving partition is inquorate too. With HA armed
   that is not a blip: the LRM on a node holding an HA resource self-fences via
   the softdog after ~60 s of lost quorum, and pve-nas-01 fencing mid-cutover
   means a NAS reboot that lands on the encrypted-pool unlock path
   ([docs/32](32-zfs-encryption.md)) with the network half-migrated.

   The four HA resources are `ct:150` (dns-01), `ct:151` (smtp-relay),
   `ct:160` (dns-02) and `vm:154` (home-assistant). Node-maintenance mode is
   the wrong tool here — it *relocates* services, and there is nowhere to
   relocate to when the whole cluster is being re-cabled. Set them out of HA's
   hands instead, from any node:

   ```bash
   for sid in ct:150 ct:151 ct:160 vm:154; do sudo ha-manager set $sid --state ignored; done
   sudo ha-manager status          # all four must read "ignored"; guests keep running
   ```

   The guests keep running exactly as they are; HA simply stops having an
   opinion, and no LRM will fence. Re-arm in step 9 — **the window is not over
   until `ha-manager status` shows all four `started` again.**
3. **Quiesce the storage path.** `task flux:status` first, so you know what
   "healthy" looked like. Then take the k3s workloads that ride pve-nas-01 out
   of the way of step 7 (`k3s-srv-nas-01` is one of three etcd members;
   `k3s-agt-nas-01` carries the prometheus/loki/authentik/mealie zvols):

   ```bash
   kubectl get nodes                                   # 9/9 Ready before touching anything
   task k3s:backup                                     # etcd snapshot
   kubectl cordon k3s-srv-nas-01 k3s-agt-nas-01
   kubectl drain k3s-agt-nas-01 --ignore-daemonsets --delete-emptydir-data
   ```

   Do **not** drain the server node — draining an etcd member is not the point;
   cordoning keeps new work off it while it is off the wire. Everything else
   NFS-dependent either stops or tolerates a stall: a NAS uplink change
   mid-write is how stale handles happen.
4. **Move the WAN handoff** to the UCG's 10G RJ45 port. Verify from a wired
   client on Default: gateway reachable, internet reachable, WAN IP as
   expected.
5. **Disconnect the ASUS.** Unplug every one of its LAN ports and power it
   down *before* the UCG brings up a LAN. Both claim `192.168.0.1` and both run
   a DHCP server for the same range; left cabled into the segment Connection A
   now delivers to switch port 7, they fight an ARP war over the default
   gateway of every Proxmox host and guest, and race each other to answer DHCP.
   That failure mode looks like nothing at all from the console and like
   intermittent connectivity everywhere else. Confirm only one answers:

   ```bash
   arping -D -I <nic> -c3 192.168.0.1     # no duplicate; MAC is the UCG's
   ```

   The ASUS keeps its *configuration* — nothing on it is changed, and rollback
   is re-cabling and re-powering it (§ Rollback).
6. **Re-cable the LAN** to [docs/46](46-unifi-network.md) § Physical port map:
   UCG port 6 (SFP+ 1) → switch
   port 10 (SFP+ 2) on the DAC; the three opt nodes onto switch ports 1-6; Hue,
   laptop and prec onto UCG ports 1/2/3; AP onto switch port 8; **Connection A
   onto switch port 7**. Connection A stays native Homelab until pve-nas-01 has
   its tagged sub-interface (step 7) — flipping it to native Home first strands
   the NAS.

   Then **force the stranded wired devices to re-DHCP.** Nothing behind the
   dumb switches saw link-down, so each still holds an ASUS-issued
   `192.168.0.x` lease on what is now `10.0.20.0/24`: the HDHomeRun (no UI to
   renew — Plex is about to look for it at `10.0.20.200`), Vasim's desktop and
   the TVs/consoles on the MoCA leg, the laptop dock, and the bedroom Hyperion.
   Power-cycle the 10G TP-Link, the 1G TP-Link and both MoCA adapters, and
   check each device has an address from the Home pool in the controller's
   client list. Anything missed self-heals at lease/2 (5 minutes if the
   pre-window lease change was made, otherwise up to 12 hours).
7. **Flip pve-nas-01 onto the tagged VLAN.** Its uplink is the one that must
   carry VLAN 10 over a port whose native VLAN is 20, so `vmbr0` moves from the
   raw NIC to a sub-interface. `/etc/network/interfaces` on that host is
   hand-maintained (not Ansible-templated) — do this **from the Proxmox
   console**, never over SSH.

   > **(Executed pre-renumber — addresses as they were.)** This step runs on
   > cutover night, when VLAN 10 is still `192.168.0.0/24`; the addresses below
   > are the Phase-1 ones deliberately. § Phase 2 step 1 adds the second
   > address to this same stanza and its late address-drop step rewrites it to
   > `10.0.10.102/24` / `gateway 10.0.10.1`. Only `bridge-ports` changes here.

   ```
   # before
   auto vmbr0
   iface vmbr0 inet static
       address 192.168.0.102/24
       gateway 192.168.0.1
       bridge-ports nic1
       bridge-stp off
       bridge-fd 0

   # after
   auto nic1
   iface nic1 inet manual

   auto nic1.10
   iface nic1.10 inet manual
       vlan-raw-device nic1

   auto vmbr0
   iface vmbr0 inet static
       address 192.168.0.102/24
       gateway 192.168.0.1
       bridge-ports nic1.10
       bridge-stp off
       bridge-fd 0
   ```

   ```bash
   cp /etc/network/interfaces /root/interfaces.pre-vlan
   # edit, then:
   ifreload -a
   ip -br addr show vmbr0            # 192.168.0.102/24 still present
   ping -c3 192.168.0.1
   ```

   The host IP and every guest stay unchanged — only the bridge's uplink moves.
   **Rollback**: `cp /root/interfaces.pre-vlan /etc/network/interfaces &&
   ifreload -a`, and set switch port 7 back to native Homelab.

   `nic1` stays link-up but address-less on port 7, whose native VLAN is Home
   (20). Its IPv6 is disabled per-interface (`nic_tuning_disable_ipv6: [nic1]`
   in `host_vars/pve-nas-01.yml`, weisssrv.infra >= v0.17.0), so no `fe80::`
   adjacency to the Home VLAN survives and the `homelab → home` source scoping
   (ALLOW row 4) cannot be sidestepped at L2. The management bridge, Tailscale
   and the tagged VLAN subinterfaces keep their IPv6. (The controller's client
   list still shows a nameless Home device with the NAS's MAC and a stale
   `last_ip: 10.0.10.102` — expected, not a fault.)

   Afterwards re-check the AQC113 offload state, which `nic_tuning` pins on the
   *physical* device: `ethtool -k nic1 | grep generic-receive-offload` must
   still read `off` (docs/34).
8. **Bring the rest of the estate back**: confirm every Proxmox host and guest
   pings, and that NFS mounts are alive (a pod holding a stale handle needs
   deleting, not restarting — docs/12). Then settle k3s and hand it back:

   ```bash
   pvecm status                                   # quorate, all six nodes
   task k3s:status                                # nodes, etcd quorum, kube-vip, kubelets
   kubectl uncordon k3s-srv-nas-01 k3s-agt-nas-01
   task flux:status                               # matches the pre-window snapshot
   ```

9. **Re-arm Proxmox HA.** The mirror of step 2, and the window is not closed
   until it is done and verified:

   ```bash
   for sid in ct:150 ct:151 ct:160 vm:154; do sudo ha-manager set $sid --state started; done
   sudo ha-manager status          # all four "started", homes as configured (docs/25)
   ```

10. **Move the wireless clients.** TheRevengers keeps its PSK, so home devices
    roam over untouched. A phone or laptop that should sit on Work has to join
    `DunderMiffLAN` — an SSID is the only thing that places an *unreserved* device.
    Everything reserved in [docs/46](46-unifi-network.md) § DHCP reservations
    moves on its own: the WLED
    controllers, the Kasa plugs (`K125M-*`), the Levoit appliances, the TVs and
    the Echoes all land on IoT from any SSID, which is what the cutover proved.
    Re-onboarding them onto `Panopticon` still happens, on no deadline — it is
    what removes the Home PSK from those devices, and steering alone does not
    ([docs/46](46-unifi-network.md) § DHCP reservations, "placement, not
    authorization"). The Hue bridge moves to the wired IoT port (UCG port 1)
    rather than an SSID.
11. **Re-point discovery-based integrations** per the SSDP table above, and fix
    the application-layer settings that assume one flat subnet — the network is
    correct at this point and these are the things that still look broken:

    - **Plex → Settings → Network → LAN Networks**: set
      `192.168.0.0/24,10.0.20.0/24,10.0.30.0/24` — the homelab entry is the
      **pre-renumber** subnet because this step runs on cutover night; § Phase 2
      step 7 replaces it with `10.0.10.0/24`.
      Plex calls a client "local" only if its address is in that list; every
      phone, laptop and TV now reaches it from a different subnet, so without
      this they are treated as *remote* — remote quality caps, transcodes where
      there used to be direct play, and sessions counted against the
      remote-streaming limits (docs/20).
    - **Nextcloud `trusted_domains` / `trusted_proxies` and Immich's proxy
      settings**: unchanged if clients keep arriving through Traefik, but worth
      confirming, since Home-VLAN devices now reach those guests from
      `10.0.20.0/24` (docs/35, docs/36).
    - **Home Assistant**: any integration whose discovery fails gets a manual
      host entry; its `internal_url` stays the same.

### Cutover as executed (2026-08-22)

What actually happened, and what each delta teaches. The estate ended the night
correct — 11 zones, 20 policies, four SSIDs, 15 reservations, five forwards, and
zero unhealthy pods — but almost none of it arrived the way the runbook above
describes.

**The bench phase was skipped; everything was re-cabled first.** The gear was
plugged into its final ports and the gateway brought up flat on
`192.168.0.1/24`, with Terraform run against the live controller afterwards. It
worked, and it is still the wrong order: every provider bug below was discovered
with the house's network already depending on the answer, and § Bench
pre-provisioning exists precisely so that discovery happens on a bench. Two
pre-flight faults surfaced immediately — pve-laptop-01 came up with its link
down (a hard restart fixed it) and the admin Mac took `192.168.0.203` out of the
UCG's default `.6-.254` pool, squatting a k3s agent's address. **A fresh
controller's DHCP pool covers nearly the whole subnet; narrow it before anything
else joins.**

**Zone-based firewalling needed a one-time UI enablement.** A fresh console
ships with ZBF off, and the provider cannot turn it on: `unifi_firewall_zone`
and `unifi_firewall_policy` simply have nothing to attach to until it is enabled
in the UI. Once on, the built-in zone display names were confirmed to be
`Internal`, `External`, `Gateway`, `Vpn`, `Hotspot`, `Dmz` — the assumption
§ First Terraform apply asks you to verify, now verified.

**The Default-flip and Homelab-create raced, and the LAN went away.** In one
apply Terraform re-addressed the built-in `Default` network *and* created
`Homelab` on `192.168.0.0/24`. The create ran first, hit `SubnetOverlapped`
against the not-yet-flipped `Default`, and failed — leaving no network holding
`192.168.0.0/24` at all. This is the hazard § First Terraform apply predicts,
and the mitigation it prescribes (apply the `Default` import and flip on their
own, before any other network exists) is the one thing that would have avoided
it. Recovery was to hand-create the Homelab VLAN in the UI and `terraform
import` it, then let the rest of the plan converge.

**Failed creates leave tainted resources.** Several resources landed
half-created with read-back errors and were marked tainted, which makes the
next apply destroy and recreate them — unacceptable for a live VLAN. The
standing fix is now one task per resource:

```bash
task terraform:unifi-untaint -- 'module.network.unifi_wlan.this["home"]'
```

(`untaint` clears the mark and touches nothing else; the verb is hardcoded in
the Taskfile the same way `terraform:unifi-state`'s is.) On cutover night that
task did not exist yet, so the repair was state surgery — pull, delete each
instance's `"status": "tainted"` line, bump `serial`, push — which works but
holds no lock while the file is being edited. Use the task; keep state surgery
for the shapes `untaint` cannot express.

**`setting_preference` defaulted to `auto`, and every network write reset the
DHCP fields.** The final apply left five networks with `dns_enabled` and
`domain_name` stripped: at module v0.13.0 `unifi_network` does not set
`setting_preference`, the provider defaults it to `auto`, and the controller
then treats the manual DHCP fields as derived and resets them on **every** write.
It looks like the apply silently ignored half its own configuration. Repaired by
`PUT`ing all six networks through the API with `setting_preference=manual`
alongside the DNS, domain and IGMP values, which held. The module sets it
explicitly from v0.13.1:

> **The freeze this caused is over: the `?ref=` pin in `main.tf` is v0.13.1.**
> An apply at the old pin re-stripped those fields on every write, so applies
> were frozen between the cutover and the pin bump, with `unifi-drift-plan`
> showing the known cosmetic diff as the one allowed exception to the "a
> yellow after the first apply is real drift" rule in § Expected breakage.
> The exception died with the first supervised apply at this pin; the executed
> sequence is recorded below.

**`allow_existing` does not cover a client the controller has never seen.**
Three `unifi_client` entries failed with `not found: type=`, because
`allow_existing` adopts a *known* client and these MACs had never associated.
The failure is also not clean: the objects existed server-side afterwards, so
the retry adopted them. Pre-seed a reservation by letting the device join once,
or expect one failed apply followed by a successful one.

**Proxmox HA was left armed, and it split a container from its disk.** The
runbook's step 2 disarm was skipped in the improvised window. Later that day HA
relocated `ct:150` (dns-01) to pve-opt-02 — moving only its *config*, since the
`subvol-150-disk-1` volume exists solely on pve-prec-01 — which took dns-01 down
and failed all four of its replication jobs with "dataset does not exist".
Recovery: `ha-manager set ct:150 --state disabled`, move `150.conf` back to
pve-prec-01, then `--state started` (a plain `pct start` is refused while HA
holds the resource, even disabled). **Step 2 is not optional**, and its failure
mode is not the fencing the step warns about — it is a quiet relocation hours
later, to a node that cannot start the guest.

**The WAN outage was not ours.** Port 5 showed no carrier for hours and was
chased as a 10GBase-T-versus-gigabit-handoff problem. It was Astound: they reset
their own out-of-apartment equipment and the carrier returned, with the public
IP unchanged (so no DDNS wait). **Check with the ISP before re-seating anything
on a fresh install** — a new gateway makes every ISP-side fault look like a
compatibility problem.

**The external GitLab names hairpinned, and it cost two outages.** First
Hermes went `ImagePullBackOff`: containerd is redirected to
`https://git.ericsweiss.com/jwt/auth` for its bearer token, that name resolved
publicly, and the UCG does not reliably loop a node back to its own WAN address
the way the ASUS did. The next day the same class hit CI — a runner pulling the
molecule image by `registry.git.ericsweiss.com` timed out, failing 43/43
molecule jobs in weisssrv-lib !35. Both were fixed with AdGuard rewrites, now
codified in `group_vars/dns.yml` along with the `pages.git` pair for parity;
[docs/08](08-dns.md) § Cross-domain rewrites carries the reasoning, the names
deliberately left public, and the monitoring coverage the rewrites cost.

**Anything that dials a public name from inside is a cutover risk**, not just
the obvious ingress paths — and the second instance is the real lesson: once
the class is identified, sweep for its siblings instead of waiting for each one
to fail on its own.

**Reservations turned out to be per-MAC VLAN steering.** The best surprise of
the night: wireless clients re-associated to TheRevengers and landed on Home
`10.0.20.x`, *and* the WLED controllers and Kasa plugs came up on IoT
`10.0.30.x` at their reserved addresses without ever joining `Panopticon`. A
`unifi_client` reservation names the network a device joins from any SSID. Step
10's re-onboarding is therefore hygiene for anything already reserved, and the
fix for an IoT-class device sitting on Home is to reserve it — which is what the
Levoit pair, the TVs and the Echoes now do ([docs/46](46-unifi-network.md) §
DHCP reservations). Whether it
also works for a WIRED device behind an unmanaged switch is the open question
those entries test.

### Post-cutover checklist

Every item in § Expected breakage was true *on purpose* before the window. Each
has to be actively retired, or the next person reads a stale allowance as a
sanctioned state. The generic holder sweep this list instantiates — app-stored
CIDR allowlists, `known_hosts`, device Configure-IP fields — is
[docs/12](12-runbooks.md) § After an addressing change. Status as of 2026-08-31:

- [x] **`router.esweiss.com` serves the UCG UI** over the `unifi-self-signed`
  transport — the 502 cleared when the UCG took the gateway address, with no
  repo change, as § Expected breakage predicted.
- [x] **The admin MacBook reservation is filled in** (`10.0.20.10`, the
  per-network private Wi-Fi address) — [docs/46](46-unifi-network.md) § DHCP
  reservations. It takes effect at
  the apply below; until then the MacBook holds a pool address outside
  `10.0.20.8/29` and has no admin reach except over Tailscale.
- [x] **The two mgmt reservations are filled in and applied** (`10.0.1.2`,
  `10.0.1.3`, MACs read at adoption). Converged at the 2026-08-30 apply — both
  devices now answer at `.2`/`.3` and `NetworkGearProbeFailed` is green for them.
  (Each device's `config_network` still records its pre-renumber `192.168.0.x`
  in the Configure-IP field; inert while it is DHCP, worth clearing so a future
  static flip cannot strand it on the retired subnet — docs/16.)
- [x] **The module pin is bumped to v0.13.1** (rode the post-cutover MR with
  the full atomic pin set), closing the `setting_preference` re-strip that froze
  applies at v0.13.0 (§ Cutover as executed).
- [x] **The v0.13.1 unfreeze apply ran on 2026-08-23** and landed its most
  important half: the six networks' `setting_preference` converged. It also
  surfaced three more controller behaviours — the default network rejects
  virtual-network overrides (failing the two mgmt reservations), and WLAN
  `ap_group_ids` / site `ips` writes flap (the failed `ips` write disabled the
  console-enabled IPS; restored by hand in Settings → CyberSecure, which owns
  day-2 IPS mode from v0.13.2 on). Module v0.13.2 absorbs all three; the
  failed client creates left server-side stubs the next apply adopts.
- [x] ~~**Run the finishing supervised apply at v0.13.2.**~~ DONE 2026-08-30:
  the plan converged to no-changes; all 28 reservations exist live, including the
  two mgmt ones (`10.0.1.2`/`.3`). No `-replace` was needed — the interrupted
  replace had already removed the old `eric-bedroom-hyperion` entry.
- [x] ~~**Expire the `NetworkGearProbeFailed` silence**~~ DONE: the mgmt
  reservations converged at that apply, both devices answer at `10.0.1.2`/`.3`,
  and the 2026-08-29 silence has passed — all three probes are green (validation
  row 18).
- [x] ~~**`unifi-drift-plan` is green on the next schedule**~~ DONE: the next
  scheduled plan went green (validation row 23). From here a yellow is drift or a
  broken credential.
- [x] ~~**Finish Connection A**~~ DONE 2026-08-30: pve-nas-01's tagged `nic1.10`
  sub-interface is up and switch port 7 is native Home (20) with VLAN 10 tagged
  (VLAN 30 added later for the bedroom Pi's self-tagged wired-IoT leg) — the
  finale state ([docs/46](46-unifi-network.md) § Physical port map).
- [x] ~~**Start the IPS burn-in clock**~~ DONE 2026-08-23: `ids` went live on
  all six VLANs. Flipped to inline `ips` 2026-09-12
  ([docs/46](46-unifi-network.md) § Day-2); the category-set review and the
  Suricata engine upgrade remain in docs/16.

Two rows did not close with the window and were carried forward into
docs/16-next-steps.md: clearing the stale older-generation `subvol-150-disk-0`
replicas left on pve-opt-01, pve-opt-03 and pve-laptop-01 by the HA relocation
(inert, but misleading during a recovery), and re-verifying the bond procedure
in [docs/34](34-bond-mac-flapping.md) against the managed switch, which is a new
link partner for all three bonded hosts.

---

## Phase 1 rollback (never used)

The ASUS's **configuration** is untouched by any of this — no setting on it was
changed, and it keeps its own export from step 1 — but it was unplugged and
powered down in step 5, so rollback is physical and includes putting it back:

1. Power the ASUS back on and re-cable its LAN ports.
2. Move the WAN handoff back to the ASUS.
3. Re-cable the hosts and the dumb switches to the old unmanaged switch.
4. Revert pve-nas-01's `/etc/network/interfaces` from `/root/interfaces.pre-vlan`
   and `ifreload -a`.
5. Power-cycle the dumb switches and the MoCA pair again, so everything
   re-DHCPs from the ASUS instead of holding a `10.0.20.x` lease.
6. Leave the UniFi gear powered off; its state (and the `.unf` backup) survives
   for the next attempt.
7. **Re-arm HA** (cutover step 9) — a rolled-back window still leaves the four
   resources `ignored` until someone sets them back.

Nothing in the repo needs reverting to make the old network work: the firewall
sets are supersets of the old ones, `cluster_home_cidr` matches no live device
on the flat LAN, and the only Kubernetes change that assumes UniFi is the
`router.esweiss.com` backend port — which fails to a 502 on that one hostname,
nothing else.

---

---

## Phase 2 — homelab renumber (2026-08-25/26)

> **Status: superseded.** Executed 2026-08-25/26; kept as the record of how the
> renumber was done, not as a procedure to run. If it is ever re-run, every step
> needs live re-verification first — addresses, quorum, etcd membership and Flux
> health all move — and § Rollback in this section is the thing to read before
> the first command.

Phase 2 moves the homelab from `192.168.0.0/24` to `10.0.10.0/24` on the same
VLAN 10, preserving every last octet: hosts `.102`-`.107`, guests `.150`-`.160`,
k3s `.202`-`.207`/`.222`/`.223`/`.227`, VIPs `.99`/`.100`/`.101`/`.161`. Nothing
about the physical layer, the VLAN tags or the zone policies changes — only the
addressing inside VLAN 10, and the handful of places that name a homelab address
from outside it.

### Why this cannot be a rolling change

A UniFi network carries exactly one subnet. The moment `local.networks.homelab.
subnet` flips, the gateway stops answering on `192.168.0.1` and starts answering
on `10.0.10.1` — every default route in the homelab dies at once, and there is
no overlap window to be had from the gateway side.

The overlap has to come from the *hosts*: VLAN 10 is one broadcast domain, so a
host holding `192.168.0.102/24` **and** `10.0.10.102/24` can talk to every other
dual-addressed host on either subnet without any router at all. Only off-subnet
traffic (internet, other VLANs, the tailnet) needs the gateway. So the shape of
the migration is:

1. dual-address everything while the old gateway is still live (reversible, no
   outage),
2. flip the gateway (off-subnet outage — the table below is what that really
   costs, and it is not small), then widen the host-side allowlists so **both**
   subnets are admitted for the rest of the window,
3. move the things that are addresses-in-config rather than addresses-on-wire
   (corosync, the k3s API VIP, the nodes, DNS) **while every host still holds
   both addresses**,
4. only then drop the old addresses, and narrow the allowlists back.

Step 3 is where the ordering matters most. Corosync's link 0 is bound to the old
address, so a host that drops it leaves the membership at once; with six nodes,
quorum is four, and flipping the third splits the cluster into two inquorate
halves with `/etc/pve` read-only. The dual-address state has to survive the
entire corosync migration, which is why the address drop is step 6b and not
step 3.

**What is actually down, and for how long.** "Short outage" describes VLAN 10's
own traffic, not the estate. Be honest about the rest before the window opens:

| What | Down from | Back at | Why |
|---|---|---|---|
| Off-subnet egress from hosts/guests (internet, other VLANs, tailnet) | step 2 apply | step 2.4/2.5 (minutes) | Default routes are repaired immediately after the apply |
| **VIP-backed inbound** — `*.ericsweiss.com` (http/https) and the wg-easy endpoint | step 2 apply | step 2.8 if the early VIP restore is done (minutes), otherwise step 7 (hours) | The gateway cannot forward to `192.168.0.100` once VLAN 10 *is* `10.0.10.0/24`, and re-pointing the forwards is not the cure — nothing answers on the new VIPs until MetalLB re-announces them |
| **Guest-backed inbound** — Plex remote and `ssh -p 2222 git@git.ericsweiss.com` | step 2 apply | step 2.5 (once plex `.152` and gitlab `.153` have their new default routes) | These two forward to dual-addressed guests, not VIPs: the same apply re-points their targets to `10.0.10.x`, the guests already hold those addresses, and only the guests' default routes (for the return path) are missing until 2.5 |
| DNS on **every client VLAN — Home included** | step 2 apply | step 2.6 (minutes, once clients re-DHCP; the admin station gets static DNS first — see 2.6) | All four VLANs' clients hold leases naming `192.168.0.150`/`.160`; after the flip the gateway has no route to that subnet at all |
| Home Assistant **on its own address** (`https://10.0.10.154:8123`, the app on the LAN) | step 2.7 | step 2.7 (one reboot) | HAOS is the one guest that cannot be dual-addressed — see that step |
| Home Assistant **through Traefik** — `home.esweiss.com` / `home.ericsweiss.com`, and the five HA-bypass IngressRoutes (tv/movies/nzbget/qbittorrent/music) | step 2.7 | step 7 (the Flux reconcile) | Flux is suspended, so the live cluster still publishes the EndpointSlice address `192.168.0.154` (`apps/vm-ingress/services-default.yaml`) and still matches `ClientIP(192.168.0.154/32)` (`apps/download-clients/ingress-routes-ha-bypass.yaml`). Those are per-guest literals, not `${cluster_*}` placeholders, so they move only when the branch itself reconciles. Expect 502 on the two hostnames and HA's *arr integrations falling through to the SSO route |
| The other vm-ingress guests through Traefik — plex, gitlab, nextcloud, immich, and the two AdGuard dashboards (`dns-01`/`dns-02.esweiss.com`) | step 5 (when each drops its old address) | step 7 | Same EndpointSlice mechanism. Reaching them by address inside VLAN 10 keeps working throughout |
| `router.esweiss.com` (the UCG console through Traefik) | step 2 apply | step 7 | Its EndpointSlice still names `192.168.0.1` while Flux is suspended. The console itself stays reachable directly at `https://10.0.10.1` from the admin station |
| **In-cluster apiserver egress** for the workloads whose NetworkPolicies name the server node IPs (the `netpol-egress-apiserver` ipBlocks: ESO, the runners, kured, and peers) | step 6, shrinking with each server move, gone after the last | step 7 | The live policies allow only `192.168.0.222/.223/.227` while Flux is suspended. Deliberately tolerated rather than live-patched: every affected consumer is a retrying controller (ESO re-syncs, runners idle, kured waits), nothing user-facing rides it, and the gap between step 6's last server and step 7's merge is minutes |
| **In-cluster DNS for the download stack** (the clients' `dnsConfig`, Gluetun, `tailnet-dns`, and the resolver-scoped egress policies — all naming `192.168.0.150`/`.160`) | step 5 (when the resolvers drop their old addresses) | step 7 | Same suspended-Flux literal pattern. The media stack pauses rather than breaks — downloads stall and resume; keep the resolvers' old-address drop late in step 5 to shrink the gap |

Two consequences to arrange **before** the window:

- **The wg-easy fallback is gone for the whole inbound gap**, and step 9 puts
  Tailscale in flux too. Confirm one out-of-band admin path that this plan does
  not touch — Tailscale on a host you are not migrating that hour, plus a
  physical console — and prove it works before the first command.
- **Tell the household.** Remote Plex, external `*.ericsweiss.com` and the VPN
  all stop answering at step 2 and stay down until the VIPs move. Nothing in
  this runbook makes that invisible.

### Repo/CI posture for the whole window

These are prerequisites, not optional hygiene. Work through them in order
before step 1.

**1. Disarm Proxmox HA — before any host, quorum or reboot work.** Phase 2 ran
as its own window; HA was armed when it opened, because cutover step 9 re-armed
all four resources as the closing condition of the Phase 1 window. Everything
from step 2.7 onwards either reboots an
HA-managed guest or puts corosync membership in motion, and an LRM that loses
quorum while holding a resource self-fences via the softdog after ~60 s. On
pve-nas-01 that means a NAS reboot landing on the encrypted-pool unlock path
([docs/32](32-zfs-encryption.md)) with the network half-migrated — the exact
outcome cutover step 2 exists to prevent. Same command block, from any node:

```bash
for sid in ct:150 ct:151 ct:160 vm:154; do sudo ha-manager set $sid --state ignored; done
sudo ha-manager status          # all four must read "ignored"; guests keep running
```

Step 9b re-arms them, and **the window is not closed until `ha-manager status`
shows all four `started` again** (validation row 21).

**2. Suspend Flux before step 2, not before step 7.** Two reasons: the
`cluster-config` change — `cluster_lan_cidr`, all four VIPs, the resolver pair —
must land when you say so rather than when the poll fires, *and* the early VIP
restore at step 2.8 patches live resources that their controllers would
otherwise drift-revert.

`task flux:suspend` takes a target (`-- <ns>/<kind>/<name>`); a bare invocation
prints usage and exits 1. The set below is derived from *what owns each object
step 2.8 patches*, which is not one Kustomization:

| Object patched at 2.8 | Owned by | Suspend |
|---|---|---|
| `IPAddressPool` public-/internal-/vpn-pool | kustomize-controller, `infrastructure-configs` (`infrastructure/configs/metallb-ip-pools.yaml`) | `task flux:suspend -- flux-system/kustomization/infrastructure-configs` |
| `Service traefik-internal` (`.101`) | kustomize-controller, `infrastructure-controllers` (`controllers/traefik/traefik-internal-service.yaml`) | `task flux:suspend -- flux-system/kustomization/infrastructure-controllers` |
| `Service traefik` (`.100`) — the annotation is **chart-rendered** | **helm-controller**, from `HelmRelease traefik/traefik`, which sets `driftDetection: mode: enabled` on a 30 m interval | `task flux:suspend -- traefik/helmrelease/traefik` |
| `Service wg-easy` (`.99`) | kustomize-controller, `apps` (`apps/wg-easy/service.yaml`) | `task flux:suspend -- flux-system/kustomization/apps` |

The third row is the one that bites: suspending a Kustomization stops
kustomize-controller re-applying the `HelmRelease` *object*, but helm-controller
keeps reconciling the release it already has — so without that suspend the
public VIP annotation is drift-corrected back to `192.168.0.100` within 30
minutes, silently re-breaking every inbound request in the middle of the window.

Suspend the parent first so nothing re-applies the child Kustomizations, then
the four owners:

```bash
task flux:suspend -- flux-system/kustomization/flux-system
task flux:suspend -- flux-system/kustomization/infrastructure-configs
task flux:suspend -- flux-system/kustomization/infrastructure-controllers
task flux:suspend -- flux-system/kustomization/apps
task flux:suspend -- traefik/helmrelease/traefik

flux get kustomizations -A      # the four above read Suspended
flux get helmreleases -n traefik
```

`infrastructure-sources`, `infrastructure-crds`,
`infrastructure-metrics-server` and `infrastructure-observability` stay running
on purpose: they own nothing step 2.8 touches, and Flux tracks `main`
(`gotk-sync.yaml`: `branch: main`) so no new revision can arrive while the MR is
unmerged. Step 7 resumes the five in reverse order.

**3. Do not merge before step 7.** Run the Ansible steps from the branch,
locally — the CI deploy jobs run on merge to `main` and would fan out to
whichever addresses the inventory names, on their own schedule. The merge is
step 7's first action, because Flux reconciles `main` and cannot see the branch.

**4. Apply the transition Tailscale policy BEFORE the window opens.** The
committed `policy.hujson` names only `10.0.10.0/24` (the
`check-tailscale-policy` gate holds `autoApprovers.routes` equal to the
inventory's `tailscale_advertise_routes`, so a committed superset would be a
standing stale-route). The transition form is therefore a **working-tree edit,
never committed** — the same live-only pattern as the `/tmp/renumber-*`
extra-vars files. In `terraform/tailscale/policy.hujson`, duplicate ACL rule
2's `dst` entry and the `autoApprovers.routes` entry with `192.168.0.0/24`
spelled in place of `10.0.10.0/24`, then `task terraform:tailscale-plan` →
review (the plan is exactly those two additions) → supervised apply, and
**leave the edit uncommitted**. With the superset live, the old-CIDR fallback
keeps working for the whole window, and any advertisement of the new route —
step 9's deliberate one **or the post-merge deploy pipeline's** (step 7 runs
the Proxmox play against the merged inventory, which re-advertises
`tailscale_advertise_routes` on its own schedule) — is auto-approved instead
of severing remote admin while it sits pending. `tailscale-drift-plan` shows
this as an expected two-entry diff until step 10 restores the committed file
and re-applies.

**5. Confirm the out-of-band admin path before the first command.** wg-easy is
down for the inbound gap (§ Why this cannot be a rolling change) and the
subnet-route handover spans steps 7-9, so the fallback has to be something this
plan is not touching that hour — plus a physical console.

**6. Keep a Home-VLAN admin station** (`10.0.20.8/29`, already in `admin_lan`).
It reaches the homelab through the gateway, so it survives the flip as soon as
the hosts carry their new addresses — which is exactly why step 1 comes first.
Every Ansible step below runs from here. **`kubectl` is the exception between
2.3 and step 4**: the kubeconfig names the API VIP `192.168.0.161`, which the
gateway no longer routes once VLAN 10 is `10.0.10.0/24` — run the kubectl
commands in 2.8/2.9 from a **k3s server** over SSH (`sudo k3s kubectl ...`, the
bundled locally-authenticated client; a Proxmox host has neither `kubectl` nor
a kubeconfig), or over the Tailscale subnet route if item 5's
out-of-band path is up. Step 4 moves the VIP and `task k3s:kubeconfig` restores
the admin station's access.

**7. Keep a console path** to pve-nas-01 and at least one other host.
IPMI/monitor + keyboard; the whole plan assumes you can recover a host whose
`interfaces` file you have just broken.

**8. Take the backups**: UniFi `.unf`, `task k3s:backup` (etcd snapshot), and a
`/etc/network/interfaces` + `/etc/pve/corosync.conf` copy per host (the
rollbacks below restore `/root/interfaces.pre-renumber` and
`/root/corosync.conf.pre-renumber`).

### Step 1 — dual-address every host and guest (no outage, reversible)

For each Proxmox host, in `/etc/network/interfaces`, add the new address as a
**second `address` line inside the existing stanza** — not a second stanza:

```
auto vmbr0
iface vmbr0 inet static
    address 192.168.0.102/24
    address 10.0.10.102/24
    gateway 192.168.0.1
    bridge-ports nic1.10
    bridge-stp off
    bridge-fd 0
```

`address` is a **list** attribute in ifupdown2 (what Proxmox VE ships in place
of classic ifupdown), so repeating it is the documented way to give an interface
several addresses; the Debian `interfaces(5)` page for ifupdown2 shows exactly
this shape in its own samples — `iface br0` and `iface lo` each carry two
`address` lines. A duplicate `iface vmbr0 inet static` block is *not* that form:
ifupdown2 builds one object per interface name, so the second block is merged or
dropped rather than applied, and with classic ifupdown it would need its own
`auto`/alias to come up at all. Either way you get one address and a check that
says so.

Do **one host first**, from the console, and treat the reload as the real proof —
this is the only claim in Phase 2 that cannot be verified from the repo:

```bash
cp /etc/network/interfaces /root/interfaces.pre-renumber
# edit, then:
ifreload -a -n                # dry run: prints what it would do, changes nothing
ifreload -a
ip -br addr show vmbr0        # BOTH addresses on the one interface, e.g.
                              # vmbr0 UP 192.168.0.102/24 10.0.10.102/24
ping -c1 192.168.0.1          # the old gateway still answers
```

If `ip -br addr show vmbr0` prints only one address, stop: nothing later in this
plan works without the overlap. Restore `/root/interfaces.pre-renumber`,
`ifreload -a`, and resolve the syntax before touching a second host.

Then dual-address **every statically addressed guest**, by class. All of these
are live-only changes; the permanent form lands in step 5. Run them from the
guest's Proxmox host (`pct`/`qm` need no working guest network) or over SSH to
the guest's *old* address:

| Class | Guests | Command |
|---|---|---|
| LXC | dns-01 `.150`, dns-02 `.160`, smtp-relay `.151`, plex `.152`, immich-ml `.158` | `pct exec <vmid> -- ip addr add 10.0.10.<n>/24 dev eth0` |
| cloud-init VM | gitlab `.153`, nextcloud `.156`, immich `.157`, k3s servers `.222`/`.223`/`.227`, k3s agents `.202`-`.207` | `ssh eric@192.168.0.<n> 'sudo ip addr add 10.0.10.<n>/24 dev ens18'` (confirm the interface name with `ip -br link`) |
| Windows | windows `.155` | at an elevated prompt: `netsh interface ipv4 add address name="Ethernet" address=10.0.10.155 mask=255.255.255.0` |
| HAOS | home `.154` | **cannot be dual-addressed — skip it here; step 2.7 flips it** |

`ha network update` *replaces* an interface's IPv4 configuration rather than
adding to it, and HAOS exposes no add-a-secondary form, so Home Assistant is the
one guest that takes a coordinated flip instead of an overlap (docs/24).

Verify before going further: from a **Proxmox host**, ping every other host
**and every guest** on **both** addresses. A machine that answers on only one
is a machine you are about to lose. The probe must force the **old** source
address: the deployed firewall still admits only `192.168.0.0/24` sources until
step 2.9, and a dual-addressed sender would otherwise pick its `10.0.10.x`
source for new-subnet destinations — making every correctly configured guest
read as `MISSING`. (Both subnets share the VLAN-10 wire during the overlap, so
an old-source ping to a new-subnet destination is answered directly.)

The list below is every statically addressed machine in VLAN 10 except `.154`
(HAOS, which step 2.7 flips instead). Cross-check it against
`scripts/hosts.env` (`task hosts:sync` regenerates that file from the
inventory) rather than trusting this copy — an omission here is invisible until
the gateway has already moved.

```bash
old_source=$(
  ip -4 -o addr show vmbr0 |
    awk '$4 ~ /^192[.]168[.]0[.]/ { split($4, a, "/"); print a[1]; exit }'
)
test -n "$old_source" || { echo "no old vmbr0 source address — run from a Proxmox host"; false; }

for n in 102 103 104 105 106 107 150 151 152 153 155 156 157 158 160 \
         202 203 204 205 206 207 222 223 227; do
  for net in 192.168.0 10.0.10; do
    ping -I "$old_source" -c1 -W1 "$net.$n" >/dev/null 2>&1 ||
      echo "MISSING $net.$n"
  done
done
```

`.160` (dns-02) is easy to lose from this list and expensive to lose in
practice: it is one of the two resolvers step 2.5 gates on, so a missed
`pct exec 160 -- ip addr add` surfaces only after the gateway has flipped, with
half the estate's resolution gone.

### Step 2 — flip the gateway (the loudest step in the plan)

The apply itself takes seconds. Steps 2.4 to 2.9 are what turn the estate back
on afterwards, and none of them are optional — **do not stop at 2.3**. Read
§ Why this cannot be a rolling change for what is down between here and 2.8.

**2.1 — leave the controller's `url` where it is.** The `UniFi Controller`
1Password item still names `https://192.168.0.1`, and it has to stay there until
2.3b. That field *is* `TF_VAR_unifi_api_url` — the Taskfile's `&tf_unifi_env`
anchor resolves it through `op run` — so it is the address Terraform connects
to, and the address the gateway answers on for this plan and this apply is still
the old one. Moving it early aims the flip itself at `10.0.10.1`, which nothing
answers on until the flip has already happened.

```bash
op item get "UniFi Controller" --vault Homelab --fields url   # https://192.168.0.1 — confirm, do not edit
```

**2.2 — plan, against the live controller address.** From the Home-VLAN admin
station:

```bash
TF_VAR_unifi_api_url=https://192.168.0.1 task terraform:unifi-plan
```

The override reaches Terraform even though the task's `env:` anchor names a
1Password reference: go-task gives the parent process environment precedence
over a task-level `env:` entry (verified on task v3.53.1), and `op run` passes a
value straight through when it is not an `op://` reference. Everything else in
the anchor — the API key, the four passphrases, the state-backend credentials —
still resolves from 1Password as usual.

The homelab network must show an **in-place update** of `subnet` and the DHCP
scope, plus the port-forward target updates. A `-/+ replace` on
`unifi_network.this["homelab"]` means `prevent_destroy` is about to abort the
apply — stop and re-read; a replace loses the network's ID and every reference
to it.

**2.3 — apply.** Supervised, with the same override:

```bash
TF_VAR_unifi_api_url=https://192.168.0.1 task terraform:unifi-apply
```

From the moment it lands, the gateway answers on `10.0.10.1` and nothing else in
the estate has a default route.

**Expect this apply to look unhappy, and do not re-run it.** It moves the
address of the controller Terraform is talking to, mid-run: the API session dies
with the old address, so the post-apply refresh can fail, time out, or report a
partial result even though the gateway has already flipped. Terraform's own
exit here is not the arbiter — 2.3b is. Re-running `apply` against the old
address at this point reaches nothing at all.

**2.3b — move the controller's `url`, then converge.** Now that the gateway
answers on the new address, point 1Password at it and let the first plan against
it be the proof that 2.3 landed:

```bash
op item edit "UniFi Controller" --vault Homelab url=https://10.0.10.1

task terraform:unifi-plan          # no override now — expect "No changes."
```

An empty diff is the convergence gate for the whole of step 2: it proves both
that the apply took and that the stored `url` reaches a controller that answers.
A non-empty diff is the real signal — read it before doing anything else. If it
is exactly a **remainder of 2.3's own change set** (the apply died mid-run, so
some of its resources — typically the port forwards — never landed), finish the
job against the new address and re-gate:

```bash
task terraform:unifi-apply          # supervised; plan section must be the 2.3 remainder only
task terraform:unifi-plan           # re-gate — repeat until "No changes."
```

Leaving that remainder unapplied is not an option: it is the WAN forwards still
pointing at the old subnet. Anything in the diff **outside** 2.3's expected
change set is a stop-and-investigate, not an apply. A plan
that cannot connect at all means the gateway did not flip, and 2.4 onwards would
be repairing routes towards an address that is not there.

**Rollback for step 2.** Apply `terraform/unifi` from a `main` checkout — it
still carries the pre-renumber `homelab` subnet, DHCP scope and forward targets
— with `TF_VAR_unifi_api_url` set to whichever address the gateway currently
answers on, then put the 1Password `url` back to match — and then **reverse
every route repair already made**: any host or guest that 2.4/2.5 pointed at
`10.0.10.1` still routes there after the gateway returns to `192.168.0.1`,
with no off-subnet access and no replies to the admin station until each one
is put back (`ip route replace default via 192.168.0.1`, same loops, old
gateway). Step 1 is untouched
either way: the hosts keep both addresses, and the addresses themselves need
no undoing — only the routes.

**2.4 — host default routes.** On each Proxmox host:

```bash
ip route replace default via 10.0.10.1
ping -c1 1.1.1.1        # IP egress only — DNS is NOT testable yet: both
                        # resolvers still route upstream through the retired
                        # gateway until 2.5, so a name lookup fails here even
                        # when this route is correct. 2.5's gate covers names.
```

This route is **volatile until step 6b**, which is where
`/etc/network/interfaces` finally drops the old addresses and gains the new
`gateway` line — a host that reboots before then comes back with the retired
`192.168.0.1` gateway and no off-subnet reach, and this command is the repair
(from the console, or from a neighbour over the still-shared L2).

**2.5 — guest default routes, resolvers first.** Every guest still carries
`gateway 192.168.0.1`, which no longer exists. Repair them here, not in step 5:
until this is done dns-01/dns-02 cannot reach their DoT upstreams, so *every
non-rewritten name in the estate stops resolving* — and smtp-relay (alert mail),
acme.sh, GitLab, Nextcloud, Immich and every k3s node's egress go with it.

Order is not negotiable — **the two resolvers first**, then gate on them:

```bash
# dns-01 (.150) and dns-02 (.160) — both HA-managed: use the same
# resolve-the-node-then-ssh pattern as the 151/152/158 loop below (or run each
# pct exec by hand on the node `sudo ha-manager status | grep -E 'ct:1[56]0'`
# reports), and treat any failure as a hard stop — nothing below works without
# the resolvers.
pct exec 150 -- ip route replace default via 10.0.10.1   # on dns-01's CURRENT node
pct exec 160 -- ip route replace default via 10.0.10.1   # on dns-02's CURRENT node

# GATE: real recursion through each resolver before anything else moves
dig @10.0.10.150 example.com +short
dig @10.0.10.160 example.com +short
```

Both must return an address. If either does not, fix it before continuing —
everything below assumes working resolution.

Then the rest, same command per class as step 1.

Run it from the **admin station against the hosts' new addresses**. After the
2.3 flip the gateway routes Home only to `10.0.10.0/24`, so the old subnet is
unroutable from any client VLAN, and the hosts' sshd `from=` allowlists already
admit the station's `10.0.20.8/29` source (that entry shipped with Phase 1, not
with 2.9's transition sets, which widen host-to-host membership only). The
`pvesh` query rides the same path.

Pin the hosts' existing SSH identities to their new addresses first —
`accept-new` would TOFU whatever answers, and this is the moment a mistaken or
duplicate address must not be trusted. The keys are already in `known_hosts`
under the old addresses, and `awk` rebuilds each entry with the new address in
the host field; a `sed` over the field would fail on hashed `known_hosts`, where
`ssh-keygen -F` prints the hash token rather than the address.

`pct exec` only runs on the node currently hosting the container, and
smtp-relay is HA-managed, so each guest's node is resolved first and a
wrong-node attempt fails loud rather than scrolling past with smtp still on the
retired gateway.

```bash
# remaining LXC guests: smtp-relay 151, plex 152, immich-ml 158
# pin each host's existing SSH identity to its new address before connecting
for o in 102 103 104 105 106 107; do
  ssh-keygen -F "192.168.0.$o" | awk -v ip="10.0.10.$o" '!/^#/ {print ip, $2, $3}' >> ~/.ssh/known_hosts
done

# a function, not a bash-4 associative array: the admin station is macOS
# (bash 3.2 / zsh), where `declare -A` stops the loop cold
nip() { case "$1" in
  pve-nas-01) echo 10.0.10.102;; pve-laptop-01) echo 10.0.10.103;;
  pve-opt-01) echo 10.0.10.104;; pve-opt-02) echo 10.0.10.105;;
  pve-opt-03) echo 10.0.10.106;; pve-prec-01) echo 10.0.10.107;;
esac; }
for v in 151 152 158; do
  node=$(ssh eric@10.0.10.102 "sudo pvesh get /cluster/resources --type vm --output-format json" \
    | python3 -c "import json,sys; print([r['node'] for r in json.load(sys.stdin) if r.get('vmid')==$v][0])")
  ssh "eric@$(nip "$node")" \
    "sudo pct exec $v -- ip route replace default via 10.0.10.1" \
    || { echo "STOP: ct $v failed on $node — fix before continuing"; lxc_ok=0; break; }
done
[ "${lxc_ok:-1}" = 1 ]
# the flag carries the failure out of the loop (`break` itself returns 0), so
# $? stays nonzero after a STOP — safe to paste interactively

# cloud-init VMs and k3s nodes, over SSH to their new addresses
failed=""
for n in 153 156 157 202 203 204 205 206 207 222 223 227; do
  ssh "eric@10.0.10.$n" 'sudo ip route replace default via 10.0.10.1' || failed="$failed .$n"
done
[ -z "$failed" ] || { echo "STOP — no route on:$failed. Fix each before ANY further step."; false; }
```

A `STOP` line here — or from the LXC loop above — halts the whole procedure,
not just its loop: every later step assumes each guest routes through
`10.0.10.1`, and a guest left on the retired gateway silently loses every
off-subnet reply while the migration keeps mutating around it.

```bash

# windows .155, at an elevated prompt: one netsh command sets address, mask and
# the adapter's configured gateway together, which is what survives a reboot.
# A persistent `route -p add` leaves the configured gateway on the old subnet.
#   netsh interface ipv4 set address name="Ethernet" static 10.0.10.155 255.255.255.0 10.0.10.1
#   netsh interface ipv4 set dns name="Ethernet" static 10.0.10.150 primary
#   netsh interface ipv4 add dns name="Ethernet" 10.0.10.160 index=2
#   netsh interface ipv4 show config name="Ethernet"   # address, gateway and both DNS servers on 10.0.10.x
```

That single `set address` **replaces** the adapter's IPv4 configuration, so it
also removes the `192.168.0.155` secondary added in step 1 — Windows is the one
guest whose old address goes here rather than in step 5. Everything it needs to
reach is either on-link at `10.0.10.x` or off-subnet through the new gateway, so
there is nothing left for the old address to serve.

**2.6 — make the client VLANs re-DHCP, Home included.** Every client VLAN's
clients hold leases naming `192.168.0.150`/`.160` — **Home too**, which is easy
to forget because Home feels adjacent to the migration rather than subject to
it. Their own VLANs did not change, so nothing re-DHCPs on its own — and the
gateway now has no route to that subnet at all, so all four VLANs have *no*
DNS until each lease renews (24 h by default). The DNS allow policies moving to
the new resolver addresses in the same apply is a detail; the missing route is
the outage.

**The admin station first**: it sits on TheRevengers, so it carries the same
dead resolvers as any Home client. Give it static DNS `10.0.10.150`/`.160` for
the window (or renew its lease immediately after the toggle below) before
relying on name resolution for any later step.

Force the renewal instead of waiting:

```
Controller → Settings → WiFi → TheRevengers / Panopticon / kugel-tikka-masala / DunderMiffLAN:
  toggle "Enable" off, save, on, save.        # every client re-associates and re-DHCPs
  # TheRevengers drops the admin station for a few seconds — expected; it
  # re-associates and returns inside 10.0.20.8/29 via its reservation.
Controller → the wired IoT access ports (2.5G-3, and any other IoT/Work port):
  toggle the port off/on, or power-cycle PoE. # wired IoT gear, e.g. the Hue bridge
```

Then **walk one client per VLAN — all four** — and confirm both halves:

```bash
# on a client joined to each of home / iot / guest / work
ipconfig /all | findstr "DNS"    # or: resolvectl status / cat /etc/resolv.conf
dig @10.0.10.150 git.esweiss.com +short
```

The lease must name `10.0.10.150`/`.160` and the query must answer. If
re-associating the whole house is impractical, the alternative is to shorten
`dhcp.leasetime` on all four client networks — Home included, it holds the
same retired-resolver leases (module input, default `24h0m0s`) — in a
supervised apply a day *before* the window and put it back afterwards — two
extra applies, but no walking.

**2.7 — Home Assistant (coordinated flip).** First, **run the whole of 2.9
now** — both transition pushes, firewall AND NFS exports (the numbering is
narrative, the dependency order is 2.9 → 2.7 → 2.8; 2.9's own position below
then reads as an idempotent checkpoint). This step needs both halves live,
because the flipped HAOS sources its media mount from `10.0.10.154`, an
address the pre-transition export ACL and host firewall deny (docs/24: HAOS is
the one documented plaintext-NFS client, so the export ACL names it
specifically), and 2.8 needs the firewall half for the wg VIP.
HAOS cannot hold two addresses, so
it moves now, in one step, from its console (Proxmox → VM 154 → Console):

```
ha > network info                       # note the interface name (e.g. enp0s18)
ha > network update <interface> --ipv4-method static \
       --ipv4-address 10.0.10.154/24 \
       --ipv4-gateway 10.0.10.1 \
       --ipv4-nameserver 10.0.10.150 --ipv4-nameserver 10.0.10.160
ha > host reboot
```

Then from a host: `ping -c1 10.0.10.154` and
`curl -s -o /dev/null -w '%{http_code}\n' --resolve home.esweiss.com:8123:10.0.10.154 https://home.esweiss.com:8123`
— HTTPS, because `home_assistant_ssl_enabled: true` has HA serving TLS on 8123
(a healthy instance FAILS a plaintext probe), and `--resolve` lets the
wildcard cert validate while steering the connection at the new address.
HA is a Proxmox HA resource — confirm it is still `ignored`/paused per the
cutover HA-pause procedure, or that a reboot will not trigger a recovery, before
issuing `host reboot`.

**2.8 — restore inbound early (strongly recommended).** The three VIP-backed
forwards (http, https, wg-easy) are dead from 2.3 until MetalLB announces the
VIPs on the new subnet — left to step 7 that is hours. (Plex and gitlab-ssh
target dual-addressed guests directly and came back at 2.5 with those guests'
default routes, as the outage table states.) The nodes are already dual-addressed, so the VIPs can move
now — and 2.9's pushes must already be live (2.7 ran them; if you skipped the
HAOS flip, run 2.9's firewall half now). The deployed rules still destination-scope
WireGuard to `192.168.0.99` (and source-scope everything to the old subnet), so
without the transitional push the new wg-easy VIP stays blocked even after
MetalLB announces it — the patch would look done and restore nothing. The
NFS-export half of 2.9 can stay where it is.

**Where to run the kubectl commands below:** not the admin station (its
kubeconfig names the API VIP `192.168.0.161`, unroutable from VLAN 20 until
step 4) and not a Proxmox host (no `kubectl`, no kubeconfig). SSH to a healthy
k3s server over its new secondary address and use the bundled, locally
authenticated client:

```bash
ssh eric@10.0.10.222
sudo -i
# prefix every kubectl command below with `k3s`, e.g. `k3s kubectl -n metallb-system ...`
```

These patches hold only because **all five** suspends from § Repo/CI
posture item 2 are in place — in particular `traefik/helmrelease/traefik`,
without which helm-controller drift-corrects the public VIP annotation back
within 30 minutes. Re-check before patching:

```bash
# same k3s-server path as everything in 2.8 — the flux CLI lives on the admin
# station, whose kubeconfig cannot reach the API mid-window. All five rows must
# print suspended=true (kubectl's default columns do not show it).
k3s kubectl get kustomization -n flux-system flux-system infrastructure-configs infrastructure-controllers apps \
  -o custom-columns=NAME:.metadata.name,SUSPENDED:.spec.suspend
k3s kubectl get helmrelease -n traefik traefik \
  -o custom-columns=NAME:.metadata.name,SUSPENDED:.spec.suspend
```

With those suspended the patches stand until step 7 reconciles the same values
from `cluster-config` and makes them a no-op.

The where-to-run block above applies to everything below — a k3s server over
SSH, every command prefixed `k3s`:

```bash
# pools first, then the Services that claim them; wg-easy first so the VPN
# fallback comes back before anything else.
k3s kubectl -n metallb-system patch ipaddresspool vpn-pool      --type merge -p '{"spec":{"addresses":["10.0.10.99/32"]}}'
k3s kubectl -n metallb-system patch ipaddresspool public-pool   --type merge -p '{"spec":{"addresses":["10.0.10.100/32"]}}'
k3s kubectl -n metallb-system patch ipaddresspool internal-pool --type merge -p '{"spec":{"addresses":["10.0.10.101/32"]}}'

k3s kubectl -n wg-easy annotate svc wg-easy          metallb.io/loadBalancerIPs=10.0.10.99  --overwrite
k3s kubectl -n traefik annotate svc traefik          metallb.io/loadBalancerIPs=10.0.10.100 --overwrite
k3s kubectl -n traefik annotate svc traefik-internal metallb.io/loadBalancerIPs=10.0.10.101 --overwrite

k3s kubectl get svc -A -o wide | grep LoadBalancer   # EXTERNAL-IP on all three is 10.0.10.x
curl -I -m5 https://<public-address>             # from off-net: the forwards work again
```

Only the three VIPs move here. The **rest** of `cluster-config` waits for step 7
on purpose: `cluster_lan_cidr` is one key feeding ~15 NetworkPolicy `ipBlock`
sets and the `vm-ingress` EndpointSlices, and until steps 5-6b have made
`10.0.10.x` the *only* address on every node, guest and host, some traffic still
sources from `192.168.0.x` and a `10.0.10.0/24`-only allowlist would drop it.
That is the same reasoning behind 2.9's transitional supersets, one layer down.

If you skip 2.8, say so out loud: the household loses external access for the
rest of the window and the VPN fallback with it.

Everything inside VLAN 10 kept working across the apply itself — what moved was
every route *out* of it, which is why 2.4 through 2.9 exist.

**2.9 — widen the host-side allowlists to BOTH subnets.** If you followed 2.7,
both pushes already ran and this is an idempotent checkpoint — re-running is a
no-op. This is the step that
makes everything after it possible, and it has no equivalent in Phase 1.

Two access-control layers on the hosts are keyed to source address, and both are
currently deployed with `192.168.0.x` membership (from `main`) while this branch
carries `10.0.10.x`-only membership:

- **The Proxmox firewall.** `cluster.fw` renders `pve_hosts`, `k3s_nodes`,
  `nfs_clients`, `core-cluster`, `admin_lan`, `lan_clients` and `dns_clients`
  from the inventory, and `host.fw` is `policy_in: DROP` ([docs/11](11-firewall.md)).
  From 2.4 onwards a host's off-subnet traffic already sources from
  `10.0.10.x`, and from step 3 corosync's second ring does too. `sg-pve-cluster`
  admits 5405/5406 only `-source +dc/pve_hosts`; `sg-nfs-server` admits 111/2049
  only from `+dc/nfs_clients`; `sg-host-admin` admits host-to-host SSH/8006 only
  from `admin_lan`; `sg-metrics` admits exporter scrapes only from `k3s_nodes`.
  The corosync **ports** are pre-opened — that is what makes step 3's add-a-link
  path low-risk — but the **source sets** are not, and a port with no matching
  source is a drop.
- **The NFS export ACLs.** `/etc/exports` is rendered wholesale from
  `nas_storage_exports` and is evaluated per client source address. From step 5
  the k3s nodes and app guests source from `10.0.10.x`, which the deployed
  export list does not name: new mounts get `EACCES` and established ones break.
  That covers every k3s NFS PV, all six hosts' `/export/tank-proxmox`, and
  HAOS's plaintext `/export/media` mount — HAOS flipped back at 2.7.

Both are pushed here as a **transitional superset** naming both subnets, and
narrowed to the branch's `10.0.10.x`-only values at step 8. Neither push changes
anything else: the extra-vars files below are the only delta, and they are
deleted at step 8.

*Firewall.* `firewall_ipset_special_entries` is merged into whichever set name
it lists — including the host-derived ones — so one override widens every set at
once. Extra-vars **replace** a dict rather than merging into it, so the file
restates the branch's entries and adds `192.168.0.0/24` alongside each:

```bash
cat > /tmp/renumber-fw-transition.yml <<'YAML'
# Phase 2 transition ONLY. Deleted at step 8.
# The four MEMBER sets get exact old-address counterparts, never the old /24 —
# a /24 would hand every old-addressed guest member-class access for the whole
# window. The client SCOPES further down keep /24 entries, as they did before.
firewall_ipset_special_entries:
  pve_hosts:
    - {ip: 192.168.0.102, comment: TRANSITION old host address}
    - {ip: 192.168.0.103, comment: TRANSITION old host address}
    - {ip: 192.168.0.104, comment: TRANSITION old host address}
    - {ip: 192.168.0.105, comment: TRANSITION old host address}
    - {ip: 192.168.0.106, comment: TRANSITION old host address}
    - {ip: 192.168.0.107, comment: TRANSITION old host address}
  core-cluster:
    - {ip: 192.168.0.102, comment: TRANSITION old member address}
    - {ip: 192.168.0.103, comment: TRANSITION old member address}
    - {ip: 192.168.0.104, comment: TRANSITION old member address}
    - {ip: 192.168.0.105, comment: TRANSITION old member address}
    - {ip: 192.168.0.106, comment: TRANSITION old member address}
    - {ip: 192.168.0.107, comment: TRANSITION old member address}
    - {ip: 192.168.0.150, comment: TRANSITION old member address}
    - {ip: 192.168.0.151, comment: TRANSITION old member address}
    - {ip: 192.168.0.152, comment: TRANSITION old member address}
    - {ip: 192.168.0.153, comment: TRANSITION old member address}
    - {ip: 192.168.0.154, comment: TRANSITION old member address}
    - {ip: 192.168.0.155, comment: TRANSITION old member address}
    - {ip: 192.168.0.156, comment: TRANSITION old member address}
    - {ip: 192.168.0.157, comment: TRANSITION old member address}
    - {ip: 192.168.0.158, comment: TRANSITION old member address}
    - {ip: 192.168.0.160, comment: TRANSITION old member address}
    - {ip: 192.168.0.202, comment: TRANSITION old member address}
    - {ip: 192.168.0.203, comment: TRANSITION old member address}
    - {ip: 192.168.0.204, comment: TRANSITION old member address}
    - {ip: 192.168.0.205, comment: TRANSITION old member address}
    - {ip: 192.168.0.206, comment: TRANSITION old member address}
    - {ip: 192.168.0.207, comment: TRANSITION old member address}
    - {ip: 192.168.0.222, comment: TRANSITION old member address}
    - {ip: 192.168.0.223, comment: TRANSITION old member address}
    - {ip: 192.168.0.227, comment: TRANSITION old member address}
  nfs_clients:
    - {ip: 192.168.0.102, comment: TRANSITION old member address}
    - {ip: 192.168.0.103, comment: TRANSITION old member address}
    - {ip: 192.168.0.104, comment: TRANSITION old member address}
    - {ip: 192.168.0.105, comment: TRANSITION old member address}
    - {ip: 192.168.0.106, comment: TRANSITION old member address}
    - {ip: 192.168.0.107, comment: TRANSITION old member address}
    - {ip: 192.168.0.153, comment: TRANSITION old member address}
    - {ip: 192.168.0.154, comment: TRANSITION old member address}
    - {ip: 192.168.0.156, comment: TRANSITION old member address}
    - {ip: 192.168.0.157, comment: TRANSITION old member address}
    - {ip: 192.168.0.202, comment: TRANSITION old member address}
    - {ip: 192.168.0.203, comment: TRANSITION old member address}
    - {ip: 192.168.0.204, comment: TRANSITION old member address}
    - {ip: 192.168.0.205, comment: TRANSITION old member address}
    - {ip: 192.168.0.206, comment: TRANSITION old member address}
    - {ip: 192.168.0.207, comment: TRANSITION old member address}
    - {ip: 192.168.0.222, comment: TRANSITION old member address}
    - {ip: 192.168.0.223, comment: TRANSITION old member address}
    - {ip: 192.168.0.227, comment: TRANSITION old member address}
  k3s_nodes:
    - {ip: 10.0.10.161, comment: k3s API VIP}
    - {ip: 192.168.0.161, comment: TRANSITION old k3s API VIP}
    - {ip: 192.168.0.202, comment: TRANSITION old node address}
    - {ip: 192.168.0.203, comment: TRANSITION old node address}
    - {ip: 192.168.0.204, comment: TRANSITION old node address}
    - {ip: 192.168.0.205, comment: TRANSITION old node address}
    - {ip: 192.168.0.206, comment: TRANSITION old node address}
    - {ip: 192.168.0.207, comment: TRANSITION old node address}
    - {ip: 192.168.0.222, comment: TRANSITION old node address}
    - {ip: 192.168.0.223, comment: TRANSITION old node address}
    - {ip: 192.168.0.227, comment: TRANSITION old node address}
  lan_clients:
    - {ip: 10.0.10.0/24, comment: homelab LAN}
    - {ip: 10.0.20.0/24, comment: Home VLAN 20}
    - {ip: 192.168.0.0/24, comment: TRANSITION old homelab subnet}
  dns_clients:
    - {ip: 10.0.10.0/24, comment: homelab LAN}
    - {ip: 10.0.20.0/24, comment: Home VLAN 20}
    - {ip: 10.0.30.0/24, comment: IoT VLAN 30}
    - {ip: 10.0.40.0/24, comment: Guest VLAN 40}
    - {ip: 10.0.50.0/24, comment: Work VLAN 50}
    - {ip: 192.168.0.0/24, comment: TRANSITION old homelab subnet}
proxmox_firewall_admin_lan_cidrs:
  - 10.0.10.0/24
  - 10.0.20.8/29
  - 192.168.0.0/24        # TRANSITION
proxmox_firewall_wan_wireguard_vips:
  - 10.0.10.99
  - 192.168.0.99          # TRANSITION — until 2.8's pool patch has settled
YAML

task infra:deploy -- --tags proxmox_firewall -e @/tmp/renumber-fw-transition.yml
```

`proxmox_firewall_smb_client_cidrs` is *derived* from
`firewall_ipset_special_entries.lan_clients`, so it widens with the override and
needs no entry of its own. The reachability probe/gate are `tags: always`, so a
tag-scoped run keeps the same contract as a full converge.

*NFS exports.* An inline `-e` override of `nas_storage_exports` is impractical —
43 client lines across 12 export paths, each with its own options string — and
running the role twice does not accumulate, because `/etc/exports` is templated
wholesale. Generate the superset from the branch's own inventory instead, so the
two lists cannot drift:

```bash
cd ansible
ansible-inventory -i inventories/prod/hosts.yml --host pve-nas-01 \
| python3 -c '
import json, sys, yaml
exports = json.load(sys.stdin)["nas_storage_exports"]
for e in exports:                     # each client line, then its 192.168.0.x twin
    e["clients"] = [c for pair in ((c, dict(c, spec=c["spec"].replace("10.0.10.", "192.168.0.")))
                                   for c in e["clients"]) for c in pair]
yaml.safe_dump({"nas_storage_exports": exports}, sys.stdout, sort_keys=False)
' > /tmp/renumber-nfs-transition.yml

grep -c 'spec:' /tmp/renumber-nfs-transition.yml     # 86 — exactly twice the 43 in host_vars
task storage:deploy -- -e @/tmp/renumber-nfs-transition.yml
```

The role's `Reload NFS exports` handler runs `exportfs -ra` for you. Verify on
the NAS that both spellings are live before moving on:

```bash
ssh eric@10.0.10.102 'sudo exportfs -v | grep -c 192.168.0.'   # non-zero
ssh eric@10.0.10.102 'sudo exportfs -v | grep -c 10.0.10.'     # non-zero
# on any Proxmox host — the compiled ruleset is what pve-firewall actually loads
ssh eric@10.0.10.104 'sudo pve-firewall compile >/dev/null && echo cluster.fw parses'
ssh eric@10.0.10.104 'grep -c 192.168.0. /etc/pve/firewall/cluster.fw'   # non-zero
```

Then confirm the firewall did not lock you out of anything already working:
`pvecm status` still 6/6, `k3s kubectl get nodes` still 9/9 Ready (from a k3s
server over SSH — the 2.8 where-to-run note applies until step 4), and an
existing NFS-backed pod still reads its volume.

### Step 3 — corosync ring migration (add a link, then drop the old one)

> **Every host keeps both addresses through this whole step.** The old address
> is what corosync's link 0 is bound to — whether `ring0_addr` spells the
> literal IP or the node name that `/etc/hosts` resolves to it — so removing it
> from a host removes that host from the membership immediately. With six nodes
> quorum is four: flip three and *both* halves are inquorate, `/etc/pve` goes
> read-only cluster-wide, and (§ Repo/CI posture item 1 is why HA is disarmed)
> an armed LRM would fence. The address drop is **step 6b**, after every node
> is on the new ring, and it is gated there.

Do **not** rewrite `ring0_addr` in place. Add the new addresses as a second
link, verify both rings, then remove the first. Corosync's second port
(`5406`) is already open in the Proxmox firewall rules — but a port is only half
of it: the `pve_hosts` **source set** has to admit `10.0.10.0/24` too, which is
what step 2.9 pushed. If 2.9 was skipped, ring 1 comes up `disconnected` and no
amount of re-editing fixes it.

1. Edit `/etc/pve/corosync.conf` (the cluster-wide copy, **not**
   `/etc/corosync/corosync.conf`): bump `config_version`, add
   `ring1_addr: 10.0.10.<n>` to every node, and add the matching
   `interface { linknumber: 1 }` to `totem`. Spell `ring1_addr` as the
   **literal** address, never the node name — `/etc/hosts` still maps every node
   name to its `192.168.0.x` address until step 6b, so a name here would give
   you two links on the same subnet and no migration at all.
2. `corosync-cfgtool -s` on every node: two rings, both `connected`. A ring 1
   that is `connected` on some nodes and not others means 2.9 did not land
   everywhere — fix that before item 3 removes the ring the cluster is
   currently running on.
3. Only then, in a second edit (bump `config_version` again), remove
   `ring0_addr` / linknumber 0 and renumber the surviving link so the cluster
   runs on `10.0.10.x` alone.
4. `pvecm status` after each edit — 6/6, and no node showing a stale ring.

Gate before leaving this step — this is what step 6b keys off:

```bash
pvecm status                     # Quorate: Yes, 6 of 6
corosync-cfgtool -s              # on EVERY node: one link, id 0, all peers connected
grep -E 'ring[0-9]_addr' /etc/pve/corosync.conf   # only 10.0.10.x remains
```

If quorum is lost mid-edit: first pick ONE authoritative partition and make
sure every node outside it is genuinely inert — stop `corosync` there (or power
the node off) and verify nothing else still holds a writable `/etc/pve` —
because `pvecm expected 1` while a second partition is active creates two
writable cluster states and corrupts the config store. Only then, on one node
of the surviving partition: `pvecm expected 1`, restore
`/root/corosync.conf.pre-renumber`, restart `corosync` + `pve-cluster`, and
re-join the isolated nodes one at a time.

### Step 4 — move the k3s API VIP (once, before any node is redeployed)

**This is the single API-VIP step, and it comes before step 5, not after step
6.** Every node config on this branch — server *and* agent — templates
`server: https://{{ k3s_api_vip }}:6443` from one variable, which is
`10.0.10.161` here. So the first node redeployed from the branch already tries
to reach the new VIP: if it does not exist yet, that node hangs. There is no
per-node override to bridge with (see step 6).

All three servers are still up and, since step 1, dual-addressed — `10.0.10.161`
is announceable on the same L2 they already sit on. So move it now, while
nothing is drained.

**Use the dedicated transition play, not `k3s.yml`.**
`ansible-playbook playbooks/k3s.yml --limit k3s_servers` is not a VIP move:
`--limit` selects *hosts*, not plays, so it also runs `base`, `qol`,
`postfix_null_client`, `alloy_host`, `nfs_tls`, `node_exporter_host` and
`proxmox_firewall` against those servers, plus the whole k3s role. The firewall
play is the one that hurts — `proxmox_firewall` deploys the cluster-wide
`cluster.fw` from any play, guest-hosted ones included (that task is `run_once` +
delegated precisely so it can), so it would re-render it from the branch
inventory and delete step 2.9's transitional `192.168.0.0/24` ipset entries
while every host still sources from that subnet. `base` would move
`/etc/resolv.conf` and the ssh `from=` / fail2ban allowlists that step 5 owns,
and the k3s role would rewrite `config.yaml` wholesale, dropping the old VIP and
the node's old `ansible_host` from `tls-san` while both are still in use.

The k3s-api-vip-transition playbook existed for this window only (it
was **removed 2026-09-02** once the cluster was wholly on `10.0.10.0/24`; restore
from git history if the record below ever needs to be re-run). It
changed the three places the VIP is spelled — the apiserver SAN list, the server
join URL, and the kube-vip DaemonSet's `address` — restarts the servers one at a
time behind a `/readyz` gate, and asserts after each restart that the node's
`InternalIP` has not moved. Its header comment carries the full list of what it
deliberately does not touch. A final play then rewrites each **agent's** join
URL, edit-only: running agents ride the VIP move on their supervisor tunnels
(the agent load-balancer holds real server addresses, not the VIP), but a
*restarted* agent dials its `server:` URL first, and until step 6 re-templates
it that URL would name the retired VIP — the edit closes that crash window
without bouncing a single agent, because it is inert until the agent's own next
restart, by which point the new VIP is live.

```bash
# etcd gate, per server over SSH. The client port 2379 is gRPC-only here, so
# the gate uses k3s's loopback metrics listener on :2381; three healthy members
# expected (etcd-client is installed at step 6 where member surgery needs it).
for s in 222 223 227; do
  ssh eric@10.0.10.$s 'echo "$(hostname -s): $(curl -s -m 5 http://127.0.0.1:2381/health)"'
done   # each: {"health":"true","reason":""}

# from the branch, back on the admin station
ansible-playbook -i ansible/inventories/prod/hosts.yml \
  ansible/playbooks/k3s-api-vip-transition.yml

# kube-vip re-elects and ARPs the new VIP; the old one stops being announced.
# Anonymous /readyz is DENIED on this cluster: a 401 JSON body from the bare
# curl below is the healthy signal (the apiserver answered on the VIP) — a
# timeout or connection failure is the unhealthy one. The authenticated
# kubectl probe after it is the real "ok".
curl -sk https://10.0.10.161:6443/readyz          # 401 body = the VIP answers
task k3s:kubeconfig                                # kubeconfig follows the VIP
kubectl get --raw /readyz                          # "ok"
kubectl get nodes                                  # all 9 still Ready
# etcd again, per server as above:
for s in 222 223 227; do
  ssh eric@10.0.10.$s 'echo "$(hostname -s): $(curl -s -m 5 http://127.0.0.1:2381/health)"'
done
```

The play runs `k3s_servers` at `serial: 1`, so expect a few seconds of API
unavailability per server rather than all at once; do not proceed until
`kubectl get nodes` is clean.

**Invariant gate — no server may have left the old subnet yet.** The k3s role
templates neither `node-ip` nor `advertise-address`, so k3s re-detects a node's
address at every start, and these servers hold *both* subnets from step 1 until
step 6b. A server that comes back registered on `10.0.10.x` has moved its etcd
peer identity ahead of step 6's one-at-a-time member replacement. The play
asserts this per server as it goes and stops the rollout at the first one that
moves; confirm the whole control plane before continuing:

```bash
moved=$(kubectl get nodes -l node-role.kubernetes.io/control-plane=true \
  -o jsonpath='{range .items[*]}{.metadata.name}={.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' \
  | grep -v '=192\.168\.0\.') || true

if [ -z "$moved" ]; then
  echo "OK — every server is still on 192.168.0.x"
else
  echo "STOP — a server InternalIP moved ahead of step 6:"
  echo "$moved"
fi
```

`grep -v` exits 1 when *every* line matched, which is the healthy case — hence
the `|| true` on the assignment, and the decision made on `$moved` being empty
rather than on grep's status.

Then confirm the new VIP is a certificate SAN and not merely a reachable
address; `task k3s:kubeconfig` above validates against it, so a missing SAN
surfaces as a `kubectl` TLS error rather than a timeout:

```bash
openssl s_client -connect 10.0.10.161:6443 </dev/null 2>/dev/null \
  | openssl x509 -noout -text | grep -A1 'Subject Alternative Name'
```

`10.0.10.161` must be listed. If it is not, the restart did not pick up the new
`tls-san` entry: on one server at a time, `rm -f
/var/lib/rancher/k3s/server/tls/dynamic-cert.json`, `kubectl -n kube-system
delete secret k3s-serving`, then `systemctl restart k3s`.

**Rollback**: re-run the same play with `-e k3s_api_vip=192.168.0.161`. It *adds*
the new VIP to `tls-san` rather than replacing the old one, so both addresses
stay valid certificate names and the move needs no certificate work in either
direction. The server VMs still hold both addresses until step 5, so this stays
reversible up to that point. `main` is not a rollback path here — the playbook
exists only on this branch, and `k3s.yml` from `main` would re-render
`cluster.fw` from the pre-renumber inventory: the same hazard, in the other
direction.

### Step 5 — guests, then k3s agents (one at a time)

Routes and DNS are already correct inside every guest — step 2.5 did that, and
Home Assistant took its whole flip at 2.7. What is left here is making the new
address **permanent** (so a reboot or a rebuild keeps it) and dropping the old
one. Per class:

| Class | Permanent form |
|---|---|
| LXC | on the host: `pct config <vmid> \| grep ^net0` → re-set that *whole* line — every existing option verbatim, only `ip=`/`gw=` changed. `--net0` REPLACES the complete property, so an option you omit is an option you turned off: dropping `firewall=1` silently detaches the container from its Proxmox security groups, and dropping `hwaddr` invalidates the UniFi reservation and the DHCP-independent identity the firewall aliases assume. Build the new value by editing the printed line, not by retyping it. Then `pct reboot <vmid>` |
| cloud-init VM | on the host: `qm set <vmid> --ipconfig0 ip=10.0.10.<n>/24,gw=10.0.10.1`, **and** fix the guest's own on-disk config in the same visit (`/etc/network/interfaces.d/50-cloud-init.cfg` or the netplan file) so it survives whether or not cloud-init re-runs the network module at boot; reboot and confirm with `ip -br addr` |
| Windows | nothing — done at 2.5, where one `netsh interface ipv4 set address … static` replaced the whole configuration (address, mask **and** the adapter's default gateway) |
| HAOS | nothing — done at 2.7 |

Also fix `/etc/resolv.conf` (or the LXC/cloud-init nameserver field) in the same
edit: an entry naming `192.168.0.150` keeps working only while the resolvers
still hold their old secondary, and breaks the moment they drop it below. The
**Proxmox hosts'** copy moves with the same inventory, so push it here rather
than waiting for the hosts' own step: `task infra:deploy -- --tags base` writes
`/etc/resolv.conf` from `dns_servers` (`10.0.10.150`/`.160`) on every managed
host.

**dns-01 and dns-02 come first** — everything downstream resolves through them.
The mid-window deploy must NOT push certificates: the push now originates from
`10.0.10.150`, and every receiver's `authorized_keys` still carries a `from=`
pin naming the old `.150` until that receiver's own play re-renders it below —
a push before then is rejected at every receiver. And "hold the distribution"
cannot mean just skipping a later command, because `task dns:deploy` itself
runs the `acme_certs` role, whose configured
`acme_certs_distribution_targets` trigger the push inside the deploy. So the
mid-window invocation empties the targets explicitly — **and carries the
transition firewall file**: `dns.yml` ends by running `proxmox_firewall`
against the dns guests, and that role's cluster.fw render is `run_once` +
delegated, so a `dns:deploy` without the transition vars re-renders the
cluster firewall from the branch inventory and silently deletes step 2.9's
old-subnet ipset entries while every k3s node still sources from them (the
step-4 hazard note called this out for `k3s.yml`; it is true of EVERY play
that reaches `proxmox_firewall`, this one included):

```bash
task dns:deploy -- -e '{"acme_certs_distribution_targets": []}' \
  -e @/tmp/renumber-fw-transition.yml
```

Then the order is: move the resolvers (the command above), run each receiver's
owning play (which updates its pin from the branch inventory), and close the
step with `task dns:deploy -- -e @/tmp/renumber-fw-transition.yml` — targets
restored from `host_vars`, and that run IS the distribution ("plain" here
means only that the acme-targets override is gone; the transition firewall
file stays on every `dns:deploy` until step 8 narrows it) — with one manual
exception first:

**HAOS's key is operator-managed** (docs/24 — the role does not touch its
`authorized_keys`), so before the distribution edit it by hand to a
transitional pin permitting **both** source addresses,
`from="192.168.0.150,10.0.10.150"`, and narrow it back to the new address
alone once the post-window distribution has succeeded.

Then run the playbook that owns each guest, from the branch — every one of
them with `-e @/tmp/renumber-fw-transition.yml` appended, for the same reason
as `dns:deploy` above: the guest plays all end by running `proxmox_firewall`.

Then the AdGuard rewrites: `group_vars/dns.yml` carries ~50 A rewrites plus the
PTR rules in `adguard_home_user_rules`, which have moved from
`<n>.0.168.192.in-addr.arpa` to `<n>.10.0.10.in-addr.arpa`. Push them with
`task dns:deploy -- -e @/tmp/renumber-fw-transition.yml` once both resolvers
are on their new addresses — before this point clients resolve to addresses that
are still secondary-only, which works but hides mistakes.

k3s agents, **one node at a time**:

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
# re-IP the VM (console or recreate per docs/19), then from the branch:
ansible-playbook -i ansible/inventories/prod/hosts.yml \
  ansible/playbooks/k3s.yml --limit <node> -e @/tmp/renumber-fw-transition.yml
kubectl get node <node> -o wide     # INTERNAL-IP is the new address
kubectl uncordon <node>
```

The `-e @/tmp/renumber-fw-transition.yml` is not optional, and **plain
`task k3s:deploy` is off-limits for the whole window**: `--limit` limits hosts,
not plays, so every `k3s.yml` invocation also runs the cluster-wide
`proxmox_firewall` play (the § 2.9 trap), and without the transition file that
play would replace the dual-subnet ipsets with the committed new-subnet-only
membership while later nodes still hold old addresses — cutting NFS and
API reach out from under the un-migrated half of the cluster.

Wait for the node to go `Ready` and for its pods to settle before the next one.
The flannel wireguard-native peers re-key from the node's `InternalIP`, so a
half-migrated node shows up as pod-to-pod packet loss, not as a `NotReady`.

### Step 6 — k3s servers (one at a time)

**The VIP has already moved** — step 4 did it, before any node was redeployed
from the branch, and every node migrated in step 5 is already joining through
`10.0.10.161`. There is no VIP action in this step and no second `task
k3s:deploy` pass for it: a server rejoining here dials the live new VIP like
everything else. If `curl -sk https://10.0.10.161:6443/readyz` does not answer
before you start, go back to step 4 — do not migrate a server into a VIP that
is not there.

**Health gate — etcd membership, not the Kubernetes API.** `k3s etcd-snapshot
ls` lists snapshot *files* and succeeds happily on a one-member cluster; there
is no etcd pod to `exec` into either, because k3s runs etcd inside the k3s
process. Ask etcd directly, on any surviving server:

```bash
# etcdctl is not shipped with k3s: `apt-get install -y etcd-client` on one
# server, once, or run it from an etcd image. Certs are k3s's own.
export ETCDCTL_API=3
export ETCDCTL_ENDPOINTS=https://127.0.0.1:2379
export ETCDCTL_CACERT=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt
export ETCDCTL_CERT=/var/lib/rancher/k3s/server/tls/etcd/server-client.crt
export ETCDCTL_KEY=/var/lib/rancher/k3s/server/tls/etcd/server-client.key

sudo -E etcdctl endpoint health --cluster --write-out=table   # 3 rows, all true
sudo -E etcdctl member list --write-out=table                 # 3 members, no "unstarted"
```

Three healthy members with the addresses you expect — anything less and you stop.
`kubectl get nodes -o wide` alongside it is a useful cross-check but is **not**
the gate: the API server answers on a single-member etcd exactly as it does on
three.

An embedded-etcd member cannot change its peer URL in place. Quorum is 2 of 3,
so move **one server at a time**, running the gate above before and after each:

1. `kubectl drain <server> --ignore-daemonsets --delete-emptydir-data`
2. Stop k3s on it, remove it from the cluster (`kubectl delete node <server>`),
   then — before wiping anything — **prove the etcd membership is gone**: node
   deletion triggers k3s's etcd member removal asynchronously, and rejoining
   while the old peer entry lingers leaves a stale fourth member or blocks the
   replacement. In the step-6 etcd shell:

   ```bash
   etcdctl member list --write-out=table   # must show 2 members, the stopped server absent
   # if its entry lingers: etcdctl member remove <ID>, then re-check
   ```

   Only then wipe `/var/lib/rancher/k3s/server/db/etcd` so it rejoins as a
   fresh member rather than an unreachable one.
3. Re-IP the VM (step 5's cloud-init form), then re-run the k3s play for that
   host from the branch:

   ```bash
   ansible-playbook -i ansible/inventories/prod/hosts.yml \
     ansible/playbooks/k3s.yml --limit <server> -e @/tmp/renumber-fw-transition.yml
   ```

   The transition extra-vars file is as mandatory here as on the agent loop
   below — this invocation also re-renders the cluster-wide firewall, and
   without the dual-subnet override it would strip the old-subnet entries while
   the other two etcd servers still hold old addresses, severing inter-server
   and NFS traffic mid-quorum-move.

   It rejoins through `10.0.10.161`, which step 4 made live. The role has **no
   per-node join-URL input** — `server:` is templated from `k3s_api_vip` in both
   the server and agent configs, and that same variable feeds the kube-vip
   manifest and the apiserver TLS SANs. That is precisely why the VIP cannot be
   left until the end: there is no "join via a concrete server address" lever to
   bridge the gap with.
4. Re-run the etcd gate: 3 healthy members, the moved one among them. Then
   uncordon.

After the third server, confirm the whole cluster once:

```bash
task k3s:kubeconfig    # re-fetch — the kubeconfig points at the VIP
kubectl get nodes -o wide                                     # 9 Ready, all 10.0.10.x
sudo -E etcdctl endpoint health --cluster --write-out=table   # 3 healthy
```

### Step 6b — drop the `192.168.0.x` addresses from the hosts

This is the last thing that changes on the wire, and it is deliberately last:
every host has carried both addresses since step 1 precisely so that corosync
(step 3) and k3s (steps 4-6) could migrate underneath a stable membership.
Guests dropped theirs in step 5 (Windows at 2.5, HAOS at 2.7); the six Proxmox
hosts drop theirs here.

**Gate before the first host** — all four must hold, or go back to step 3:

```bash
pvecm status                                      # Quorate: Yes, 6 of 6
grep -E 'ring[0-9]_addr' /etc/pve/corosync.conf   # only 10.0.10.x
corosync-cfgtool -s                               # on every node: all peers connected
kubectl get nodes -o wide                         # 9 Ready, all INTERNAL-IP 10.0.10.x
```

Then one host at a time, from the console, watching quorum from a *different*
host. Step 1 put both addresses in **one** `iface vmbr0` stanza, so this is a
two-line edit inside that stanza — do not reintroduce the second-stanza form
step 1 warned against:

```
auto vmbr0
iface vmbr0 inet static
    address 192.168.0.<n>/24      <- DELETE this line
    address 10.0.10.<n>/24
    gateway 192.168.0.1           <- change to: gateway 10.0.10.1
    bridge-ports nic1             <- unchanged (nic1.10 on pve-nas-01)
    bridge-stp off
    bridge-fd 0
```

```bash
cp /etc/network/interfaces /root/interfaces.pre-drop
# edit as above, then:
ifreload -a -n                # dry run first, same as step 1
ifreload -a
ip -br addr show vmbr0        # ONE address: 10.0.10.<n>/24
ping -c3 10.0.10.1
```

From another host, before touching the next one: `pvecm status` still 6/6 and
`corosync-cfgtool -s` shows the flipped node connected. A node that drops out
here means its ring 1 was never really up — restore
`/root/interfaces.pre-drop`, `ifreload -a`, and re-check step 3.

Update `/etc/hosts` on the same host in the same edit — Proxmox resolves its own
node name through it and a stale entry breaks `pvecm`/`pveproxy` in confusing
ways.

Re-check the NIC-offload pins afterwards (`ethtool -k nic1`, `cat
/proc/net/bonding/bond0`) — docs/34.

**pve-nas-01 is the special one.** Dropping `192.168.0.102` invalidates every
NFS mount still established against that address — Proxmox marks the
`tank-proxmox` storage inactive on the other five hosts, and any pod holding
a stale handle needs **deleting**, not restarting (docs/12; a Flux-managed
workload drift-reverts a `rollout restart`). Do it last, and expect to work
through the remount list at step 8:

```bash
pvesm status                       # on each host: tank-proxmox active again
kubectl get pods -A | grep -vE 'Running|Completed'
```

### Step 7 — merge, then the coordinated Flux moment

**Merge the MR first.** Flux reconciles `main`
(`kubernetes/clusters/weisssrv/flux-system/gotk-sync.yaml`: `branch: main`), so
pushing the branch changes nothing in-cluster — and resuming before the merge
would re-apply *main's* manifests, which still carry `192.168.0.x`, undoing the
step-2.8 VIP patches and taking inbound down again. The precondition for merging
was "the fleet is on the new addresses", and step 6b is where that becomes true.
The post-merge deploy pipeline is a no-op re-run against a fleet you have already
converged by hand — with one deliberate exception: its Proxmox play re-advertises
`tailscale_advertise_routes` as `10.0.10.0/24`, which is exactly the
advertisement posture item 4's pre-applied policy auto-approves (and why that
item runs before the window, not at step 9).

1. Merge, then confirm Flux can see it:

   ```bash
   flux get sources git flux-system      # revision is the merge commit
   ```

2. Resume the **children first, parent last** — the order below is
   load-bearing twice over. The parent `flux-system` Kustomization owns the
   children's CR definitions, and resuming it first re-applies them from git
   with `suspend` unset — clearing the CLI-applied suspension on all three
   children at once and letting them race ahead of this sequence. So the
   children come back one at a time (each waited to Ready, `dependsOn` gating
   the rest), the parent only after them, and the traefik HelmRelease last of
   all — waking it any earlier lets helm-controller's drift detection
   re-assert the old chart-rendered VIP annotation (`192.168.0.100`) and undo
   2.8; it must come back only after `infrastructure-controllers` has applied
   the `10.0.10.x`-substituted HelmRelease:

   ```bash
   task flux:resume -- flux-system/kustomization/infrastructure-configs
   flux reconcile kustomization infrastructure-configs --with-source
   task flux:resume -- flux-system/kustomization/infrastructure-controllers
   flux reconcile kustomization infrastructure-controllers
   task flux:resume -- flux-system/kustomization/apps
   flux reconcile kustomization apps
   # children Ready, sequence held — only now hand their definitions back:
   task flux:resume -- flux-system/kustomization/flux-system
   task flux:reconcile
   # verify the rendered HelmRelease now carries the new VIP before waking it:
   kubectl -n traefik get helmrelease traefik -o yaml | grep 10.0.10.100
   task flux:resume -- traefik/helmrelease/traefik
   ```

3. `cluster-config` carries the new `cluster_lan_cidr`, the three MetalLB VIPs
   and the API VIP. If step 2.8 already patched the pools this is a no-op that
   simply puts Flux back in charge of values it now agrees with; if it did not,
   this is the moment inbound comes back. Either way, watch `kubectl -n
   metallb-system get ipaddresspool,l2advertisement` and then the Traefik
   services' `EXTERNAL-IP` — including the **public** one, which only now returns
   to helm-controller's ownership.
4. The ~15 NetworkPolicy `ipBlock` sets, the `vm-ingress` EndpointSlices and the
   observability targets all move in the same reconcile — this is where Home
   Assistant's ingress and the five HA-bypass IngressRoutes come back (§ what is
   actually down). Expect a burst of `EndpointDown`/`NetworkGearProbeFailed` while
   Prometheus re-resolves; it should clear inside two scrape intervals.
5. `task flux:status`, then `task flux:verify`.
6. Fix the application settings that name the subnet rather than resolve it —
   the network is correct at this point and these are what still looks broken:
   **Plex → Settings → Network → LAN Networks**, replacing the cutover-night
   `192.168.0.0/24` entry with `10.0.10.0/24` so the list reads
   `10.0.10.0/24,10.0.20.0/24,10.0.30.0/24` (docs/20; without it every client is
   treated as remote — quality caps and transcodes where there used to be direct
   play). Re-check the § Cutover step 11 list for anything else configured by
   address.

### Step 8 — narrow the transition membership, then remount

Step 2.9 widened the Proxmox firewall sets and the NFS export ACLs to admit
**both** subnets. Nothing sources from `192.168.0.x` any more, so converge them
back onto the committed branch values — which is simply the same two pushes with
the extra-vars files omitted:

```bash
task infra:deploy -- --tags proxmox_firewall     # no -e: the branch's 10.0.10.x-only sets
task storage:deploy                              # no -e: host_vars/pve-nas-01.yml as committed
rm -f /tmp/renumber-fw-transition.yml /tmp/renumber-nfs-transition.yml
```

Verify the narrowing actually happened — a leftover `-e` is how a transition
superset becomes permanent:

```bash
ssh eric@10.0.10.102 'sudo exportfs -v | grep -c 192.168.0.'          # 0
ssh eric@10.0.10.104 'grep -c 192.168.0. /etc/pve/firewall/cluster.fw'  # 0
```

The k3s NFS PVs mount **by hostname** (`pve-nas-01.esweiss.com`) with
`xprtsec: tls`, which is why this comes after the step-5 DNS rewrites: the
export matrix in `host_vars/pve-nas-01.yml` (the `.200/29` + `.220/29` +
`.227/32` blocks and every per-guest `/32`) only matches clients that already
source from `10.0.10.x`. The role's handler runs `exportfs -ra`.

Then clear whatever step 6b's NAS address drop left stale. A pod holding a stale
handle needs **deleting**, not restarting (docs/12) — and remember a
Flux-managed workload drift-reverts a `rollout restart`:

```bash
kubectl get pods -A | grep -vE 'Running|Completed'
pvesm status          # on each host: tank-proxmox active
```

### Step 9 — Tailscale route convergence

The policy half already happened: posture item 4 applied the transition
superset before the window opened, so both CIDRs are in ACL rule 2 and
`autoApprovers.routes`, and nothing in this step can leave a route pending or
sever the fallback. What remains is converging the advertisement and proving
it:

1. Re-run the Proxmox play so `tailscale_advertise_routes` advertises
   `10.0.10.0/24` — unless the post-merge deploy pipeline (step 7) already did,
   which is fine and safe for exactly the reason item 4 exists; check
   `tailscale status` on a host first.
2. From a tailnet client, reach a homelab service by its internal name over the
   new route. Only then remove the old `192.168.0.0/24` route from the admin
   console if it lingers. The old-CIDR **policy** entries stay until step 10's
   cleanup apply.

### Step 9b — re-arm Proxmox HA

The mirror of § Repo/CI posture item 1, and the window is not closed until it is
done and verified. Do it here, after the last reboot and the last quorum
change — but do **not** leave it to "sometime after validation": a window that
ends with HA still `ignored` looks healthy and has no failover.

```bash
for sid in ct:150 ct:151 ct:160 vm:154; do sudo ha-manager set $sid --state started; done
sudo ha-manager status          # all four "started", homes as configured (docs/25)
```

### Step 10 — retire the transition policy, then final validation

First close out posture item 4's superset: once step 9's checks pass, restore
the committed policy —
`git checkout -- terraform/tailscale/policy.hujson` discards the uncommitted
transition edit — then `task terraform:tailscale-plan` → review (the plan is
exactly the two `192.168.0.0/24` removals) → supervised apply. Nothing to
merge afterwards: the transition entries never entered git, and this apply puts
the tailnet back on the committed file, which also returns
`tailscale-drift-plan` to green.

The MR merged at step 7; the rest of this step is validation only. Re-run the
[docs/46](46-unifi-network.md) § Validation
matrix above in full — every row still applies. The ones this phase can break
are the rows that name a homelab address or a VIP: 3 (resolver reach), 8
(external DNS fenced — its `dig` control), 10 (Plex direct play, via LAN
Networks), 12/13/14 (internal ingress, appliance UIs, admin surfaces — the VIP
and the `admin_lan` membership), 15/16 (port forwards and wg-easy — the MetalLB
VIPs), 17 (Tailscale routes), 18 (gear probes — the gateway target moved), 21
(**HA re-armed** — step 9b just did it, and this is the row that proves it), 23
(drift plan) and 24 (cluster health). Row 2 is a cutover-night check and does not
re-run here. Add:

| # | Check | How | Expected |
|---|---|---|---|
| P2-1 | No stragglers, **both spellings** | the two greps below | Only the deliberate `/16` egress `except` entries and labelled Phase-1/historical prose |
| P2-2 | Reverse DNS | `dig -x 10.0.10.153 @10.0.10.150 +short` | `gitlab.esweiss.com.` |
| P2-3 | Gate agreement | `python3 scripts/check-cluster-literals.py` | Pass — `cluster-config` and the inventory agree |
| P2-4 | Address book | `task hosts:sync` | No diff in `scripts/hosts.env` |
| P2-5 | Corosync | `corosync-cfgtool -s` on every host | One link, new addresses, all `connected` |
| P2-6 | etcd | `etcdctl endpoint health --cluster` + `member list` (step 6's block) | 3 healthy members, all `10.0.10.x` |
| P2-7 | Client-VLAN DNS | On one client each of iot/guest/work: leased DNS servers, then `dig @10.0.10.150 git.esweiss.com` | Lease names `10.0.10.150`/`.160`; the query answers (step 2.6) |
| P2-8 | Inbound restored | From off-net: `curl -I https://<public>`, Plex remote, `ssh -p 2222 git@git.ericsweiss.com`, a wg-easy handshake | All four succeed — the WAN forwards and the MetalLB VIPs agree |
| P2-9 | Home Assistant | `ping 10.0.10.154`, then `https://home.esweiss.com` | Reachable at the new address (step 2.7) **and** through Traefik (step 7's reconcile moved the EndpointSlice) |
| P2-10 | Transition membership narrowed | On the NAS: `sudo exportfs -v \| grep -c 192.168.0.`; on any host: `grep -c 192.168.0. /etc/pve/firewall/cluster.fw` | `0` and `0` — step 8's pushes dropped the 2.9 supersets |

P2-1 is **two** greps, because the old subnet has two spellings in this repo and
the plain one finds only the easy half:

```bash
# 1. both spellings at once. `(\\)*` absorbs the escaped form; the second grep
#    drops the deliberate RFC1918 /16 `except` entries in the netpol components.
grep -rIn -E '192(\\)*\.168(\\)*\.0(\\)*\.' \
  ansible/ kubernetes/ terraform/ scripts/ Taskfile.yml docs/ \
  | grep -vE '192(\\)*\.168(\\)*\.0(\\)*\.0/16'

# 2. the escaped spelling on its own — Prometheus rule expressions, promtool
#    fixtures and Grafana dashboard JSON carry addresses as `192\\.168\\.0\\.`
#    (two literal backslashes per dot). This must return NOTHING.
grep -rIn '192\\\\\.168\\\\\.0\\\\\.' kubernetes/ scripts/
```

Grep 1 legitimately keeps hits in `docs/` and `terraform/unifi/README.md`. (The
transition helper `k3s-api-vip-transition.yml`, whose
`192.168.0.x` literals were deliberate — the rollback VIP value, the two-value
input restriction, and the old-subnet fleet gate — was **removed 2026-09-02**,
the follow-up the window owed once the cluster was wholly on `10.0.10.0/24`; its
hits retired with it.) The cutover runbook and the
Phase 2 narrative describe the pre-renumber world on purpose. Every one of those must read as a labelled historical or cutover-night
statement — if a hit is a live instruction, it is a straggler.

The MR is already merged — step 7 needed it, because Flux reconciles `main`.
What closes the window is this matrix plus row 21: `ha-manager status` showing
all four resources `started`.

### Rollback

The further in, the more this is a roll-*forward* migration — but each step has
its own reversal, and steps 1-2 are cheap:

- **Before step 3**: revert the gateway (step 2's own rollback line: apply
  `terraform/unifi` from a `main` checkout with `TF_VAR_unifi_api_url` aimed at
  whichever address answers, then put the `url` field back), undo the 2.8 MetalLB
  patches the same way (`kubectl patch`/`annotate` back to `192.168.0.x`, or
  just resume the five suspends and reconcile from `main`), reverse every
  2.4/2.5 route repair (`ip route replace default via 192.168.0.1`, same
  loops), and put back the TWO guests that took one-way flips: HAOS with the
  same `ha network update` at 2.7, and **Windows**, whose 2.5 `netsh set
  address` REPLACED the adapter config and removed `192.168.0.155` — the
  inverse at an elevated prompt:

  ```
  netsh interface ipv4 set address name="Ethernet" static 192.168.0.155 255.255.255.0 192.168.0.1
  netsh interface ipv4 set dns name="Ethernet" static 192.168.0.150 primary
  netsh interface ipv4 add dns name="Ethernet" 192.168.0.160 index=2
  ```

  Then **force the client VLANs to re-DHCP again** — the reverted Terraform
  restores `192.168.0.150`/`.160` as the DHCP DNS servers, but every Home /
  IoT / Guest / Work lease issued during the window still names
  `10.0.10.150`/`.160`, which the restored gateway no longer routes; re-run
  2.6's SSID and port toggles (all four SSIDs) or those VLANs sit without DNS
  until their leases renew. Every other host and guest still holds both
  addresses; nothing else changed. The 2.9
  transition pushes are supersets — they need no reversal. If you want the
  pre-window membership back, deploy the same two plays **from a `main`
  checkout**: re-running them from this branch without `-e` installs the
  `10.0.10.x`-only sets, which would drop corosync (still on `192.168.0.x`
  ring addresses) and de-authorize every NFS mount still established against
  `192.168.0.102` — the exact breakage 2.9 exists to prevent.
- **Step 3** (corosync): restore the saved `/root/corosync.conf.pre-renumber`,
  and if quorum is gone follow the step-3 recovery discipline exactly —
  **isolate every node outside one authoritative partition first** (stop
  `corosync` there or power off, verify nothing else holds a writable
  `/etc/pve`), and only then `pvecm expected 1` on one surviving node, restart
  `corosync` and `pve-cluster`, and re-join the isolated nodes one at a time.
  Forcing quorum with a second live partition writes two cluster states.
  Every host still holds both addresses at this point, which is why this
  reversal is cheap.
- **Step 4** (API VIP): **historical record only — there is no rollback for
  this step any more.** `k3s-api-vip-transition.yml` was removed 2026-09-02 and
  the estate is single-addressed on `10.0.10.0/24`; restoring that play and
  re-running it with the old VIP today would withdraw the live API VIP
  (`10.0.10.161`) and take the cluster API down. It was valid only inside the
  dual-addressed window, which is closed. Do not reach for `k3s.yml` from `main`
  as a substitute either: that re-renders `cluster.fw` from the pre-renumber
  inventory mid-window (§ step 4).
- **Steps 5-6**: a drained node that will not rejoin is a rebuild, not a
  rollback — rebuild it with the same guarded invocation the migration itself
  uses (`ansible-playbook -i ansible/inventories/prod/hosts.yml
  ansible/playbooks/k3s.yml --limit <node> -e @/tmp/renumber-fw-transition.yml`
  — plain `task k3s:deploy` stays forbidden for the whole window, § step 5), or
  restore the etcd snapshot from `task k3s:backup` (docs/19).
- **Step 6b** (host address drop): `cp /root/interfaces.pre-drop
  /etc/network/interfaces && ifreload -a` on the affected host, from the
  console. This is the last cheap reversal on the wire.
- **Step 7**: re-suspend the same five resources (§ Repo/CI posture item 2) to
  stop the bleeding; the previous `cluster-config` is one `git revert` on `main`
  away, and re-applying the 2.8 patches restores inbound in the meantime.
- **Step 9b**: nothing to reverse — but a rolled-back window still leaves the
  four HA resources `ignored` until someone sets them back.

---
