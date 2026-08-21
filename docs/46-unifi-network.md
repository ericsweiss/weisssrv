# UniFi Network

The gateway/switch/AP tier — UniFi Cloud Gateway Fiber (UCG-Fiber),
USW-Pro-XG-8-PoE and U7 Pro XGS — replaces the Asus GT-AX11000 Pro and the
unmanaged switch it fed, and carries the VLAN segmentation that
[docs/16-next-steps.md](16-next-steps.md) had listed as "planned" since the
first cluster build.

Everything the `ubiquiti-community/unifi` provider supports is codified in
[`terraform/unifi/`](../terraform/unifi/) (a thin caller of the weisssrv-lib
`unifi-network` module at a pinned `?ref=`, exactly like the other three
Terraform roots); everything the provider cannot express is a **numbered UI
step in this page's runbook**. The split is not aesthetic — § Codified vs
manual is the contract, and a UI change to something in the codified column is
drift that the next plan will try to revert.

**Migration state.** Phase 1 introduced the VLANs while the homelab stayed on
`192.168.0.0/24`. Phase 2 renumbers it to `10.0.10.0/24`, preserving every last
octet; it ships as its own MR after Phase 1 has been validated in production.
**This document is the Phase 2 branch's copy** — every address below is already
the post-renumber one, and § Phase 2 owns the sequence that gets the fleet
there. On `main` (Phase 1) the same tables read `192.168.0.x`.

---

## Ground rules

Same posture as `terraform/tailscale` and `terraform/authentik`:

- **Apply is a supervised manual step.** `task terraform:unifi-apply` refuses
  `-auto-approve` and CI never applies. A wrong VLAN or zone-policy field can
  cut the LAN off from its gateway — including the machine you are typing on.
- **Plan is always safe.** `task terraform:unifi-plan` is read-only against the
  controller API and the GitLab-hosted state (`terraform/state/unifi`); the
  `unifi-drift-plan` CI job re-plans post-merge and on the schedule
  (advisory, `allow_failure`).
- **The controller is the live authority during an outage.** If the network is
  broken, fix it in the UI and codify afterwards — then plan, confirm the diff
  is exactly your hot-fix, and MR it. Never apply Terraform to "fix" an outage
  you have not read the plan for.
- **`prevent_destroy` is module-side** on `unifi_network` and
  `unifi_firewall_zone`. Removing one is `terraform state rm` → delete the map
  entry → delete in the UI. Renaming a map key is a destroy+create in disguise;
  add a `moved {}` block.

---

## Design

### Physical port map

**As cabled on 2026-08-22** — port numbers are the controller's, and this table
is the live truth rather than the pre-cutover plan (the AP and Connection A
ended up swapped relative to the draft, and the UCG's SFP+ 1 is port 6).

UCG-Fiber (every port role is software-assigned):

| Port | Role |
|---|---|
| 1 (2.5G) | Hue bridge — access, native VLAN 30 |
| 2 (2.5G) | pve-laptop-01 — access, native VLAN 10 |
| 3 (2.5G) | pve-prec-01 — access, native VLAN 10 |
| 4 (2.5G, PoE+) | spare |
| 5 (10G RJ45) | **WAN** — symmetric gigabit ethernet handoff |
| 6 (SFP+ 1) | **Trunk to the switch** (DAC): native VLAN 1, tagged 10/20/30/40/50 |
| 7 (SFP+ 2) | spare |

USW-Pro-XG-8-PoE (155 W PoE budget; the AP draws ≤29 W). **PoE is disabled on
every port except 8** — nothing else on this switch is powered over ethernet,
and an enabled port that feeds a non-PoE device is a fault waiting for a
mis-patch:

| Port | Role |
|---|---|
| 1-2 | pve-opt-01 `nic0`/`nic1` — active-backup bond, **no LACP**, both access native VLAN 10 |
| 3-4 | pve-opt-02 — same |
| 5-6 | pve-opt-03 — same |
| 7 | **Connection A** — native VLAN 10 (Homelab) **today**; the finale is native VLAN 20 (Home), tagged VLAN 10 (below) |
| 8 | U7 Pro XGS (PoE++) — trunk: native VLAN 1, tagged 20/30/40/50 |
| 9 (SFP+ 1) | spare |
| 10 (SFP+ 2) | Uplink from the UCG — trunk all, native VLAN 1 |

"Connection A" is the run to the dumb 10G TP-Link switch, which fans out to
pve-nas-01, a 1G dumb switch (laptop dock, HDHomeRun), the bedroom Hyperion Pi,
and the MoCA leg feeding the living-room devices.

**Its port is native Homelab today, which is a temporary state.** The design
has it native Home (20) with VLAN 10 tagged, so that pve-nas-01 rides a tagged
sub-interface while everything else on the run stays untagged on Home. Cutover
night left it native Homelab instead, because flipping it before pve-nas-01 has
its sub-interface strands the NAS — the recovery from the subnet-overlap race
(§ Cutover as executed) made that ordering unavoidable. The consequence while it
stands: **every device on that run is on VLAN 10 with full homelab reach**,
including the wired TVs, the dock and Vasim's desktop. Closing it is a single
pending step — configure the NAS sub-interface (§ Cutover, step 7), then flip
the port — and until then the Connection A trust decisions below describe the
finale, not the present.

#### Accepted trust decisions on the Connection A run

These describe the design once port 7 is native Home; while it is still native
Homelab the exposure is strictly wider, as noted above.

The far end of port 7 is unmanaged, so **anything plugged in there that does
not tag its own frames lands on Home (VLAN 20)** — and Home reaches the homelab
in full (policy 1). That is the settled design, not an oversight, but it has
consequences worth naming so nobody re-derives them at 2 a.m.:

- **Wired TVs, streamers and game consoles land on Home by default**, and keep
  full homelab reach there — an accepted residual. This was written as
  unavoidable, on the reasoning that an SSID is the only thing that decides a
  VLAN; the cutover disproved the general form (a reservation steers by MAC,
  § DHCP reservations), so two wired devices are now reserved onto IoT to test
  the specific form. What is genuinely unavoidable is only the fallback: where
  MAC-based assignment does not reach a device behind an unmanaged switch, the
  fix is still a managed switch at the far end of Connection A (tracked in
  [docs/16](16-next-steps.md), not a Phase 1 blocker).
- **The work laptop's containment depends on how it is attached.** On the
  `3601-Work` SSID it is on VLAN 50 and gets DNS and nothing else; in the dock
  (1G dumb switch off Connection A) it is untagged Home and inherits
  `home → homelab any`. Work is therefore "the work laptop when mobile", which
  is the case the VLAN was created for. Validation row 6 tests the wireless
  path deliberately.
- **The laptop dock and the HDHomeRun are Home devices** and are reserved as
  such (§ DHCP reservations). The bedroom Hyperion Pi and the wired Vizio are
  reserved onto **IoT** despite being on this run — whether that takes depends
  on MAC-based VLAN assignment reaching a device behind an unmanaged switch
  (§ DHCP reservations).
- **Any Home device can reach the gateway console's login page** at
  `https://10.0.20.1` — the `*-to-gateway-mgmt` BLOCKs deliberately cover only
  guest/IoT/work. Fencing Home *except* the `/29` admin block would need an
  ALLOW-before-BLOCK pair on one zone-pair, i.e. rule ordering, which this
  design refuses to depend on (the provider cannot manage it) — and blocking
  all of Home would cut the admin station's break-glass path to the console
  when Traefik (the `/29`-restricted `router.esweiss.com` route) is down. The
  console's own authentication is the gate; the residual is a login page, not
  access.
- **The `/29` admin block is a DHCP-reservation boundary, not an
  authenticated one.** A Home device that statically claims `10.0.20.10`
  inherits the block's L3 trust (`admin_lan`, `lan-tailscale-strict`). Every
  admin surface behind it still authenticates (SSH keys, Proxmox/AdGuard/
  Connect logins), so this narrows exposure rather than granting access — but
  a real identity boundary needs a dedicated admin SSID/VLAN, tracked in
  [docs/16](16-next-steps.md) § UniFi network follow-ups.

Bonding note: ports 1-6 stay **plain access ports**. The opt nodes run
`active-backup`, not LACP, and `bond-all_slaves_active 0` is codified in
`nic_tuning` ([docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md)) — a
managed switch changes the link partner, so re-verify that doc's procedure
after cutover.

### Networks

Subnets are written in the provider's **gateway form** (the host part of
`subnet` is the gateway address).

| Key | Name | VLAN | Subnet | DHCP pool | Notes |
|---|---|---|---|---|---|
| `default` | Default | 1 (built-in) | `10.0.1.1/24` | `.100`-`.199` | Management: gateway, switch, AP. Imported, not created (`name=Default`). DHCP DNS is `1.1.1.1`/`9.9.9.9`, not the resolvers |
| `homelab` | Homelab | 10 | `10.0.10.1/24` | `.2`-`.98` | Hosts, guests, k3s, VIPs. Was `192.168.0.1/24` until Phase 2 |
| `home` | Home | 20 | `10.0.20.1/24` | `.50`-`.199` | Personal client devices. Pool stops at `.199` — the reservations sit above it (§ DHCP reservations) |
| `iot` | IoT | 30 | `10.0.30.1/24` | `.50`-`.99` | IGMP snooping on (as does Home — the two ends of the casting path). A 50-address dynamic range: every IoT device that matters is reserved at `.120`+ |
| `guest` | Guest | 40 | `10.0.40.1/24` | `.50`-`.249` | `purpose = corporate` — see below |
| `work` | Work | 50 | `10.0.50.1/24` | `.50`-`.249` | |

Every **client** VLAN hands out `10.0.10.150` / `10.0.10.160` as DHCP DNS
and `esweiss.com` as the domain, so split-horizon resolution
([docs/08-dns.md](08-dns.md)) works identically on all of them.

The **management VLAN is the deliberate exception**: `default` hands out
`1.1.1.1` / `9.9.9.9` instead. The switch and the AP reach the network through
themselves, and the resolvers are LXC guests two layers below them, so pointing
VLAN 1 at `.150`/`.160` is the same bootstrap loop this page rejects for the
gateway's own WAN DNS (§ Site settings). Nothing on that VLAN needs
split-horizon answers — it resolves `ui.com` for adoption, firmware and
telemetry and nothing else. That is also why there is no `Internal → homelab`
DNS policy in the matrix below: with public resolvers on VLAN 1 nothing from
the management zone ever asks AdGuard.

**IPv6 is off, and that is a posture rather than an omission.** No input in
`terraform/unifi/` touches any `ipv6_*` attribute, so every managed network
keeps the provider default `ipv6_interface_type = "none"` and hands out no GUA.
Turning it on later is not a one-line change: every allowlist below the gateway
(the Proxmox firewall sets, the Traefik middlewares, the NetworkPolicy
`ipBlock`s) is IPv4-only, and a v6-capable client under an IPv4-only allowlist
is an open door no plan shows. Validation row 22 checks that clients really
come up v4-only.

The homelab pool deliberately stops at `.98`: `.99` is the wg-easy MetalLB VIP
([docs/38-wireguard-vpn.md](38-wireguard-vpn.md)) and `.100`/`.101`/`.161` are
the other VIPs. That exclusion used to live only in router config; it is now a
codified pool bound.

**Guest uses `purpose = corporate` and a custom zone, not the guest/Hotspot
pair**, for two reasons in this order:

1. **The controller rewrites it anyway.** `purpose = "guest"` only sticks while
   the network sits in the controller's own `Hotspot` zone; anywhere else the
   controller rewrites it to `corporate` and the apply fails with an
   inconsistent-result error. This is the hard blocker, and the module's own
   validation enforces it (weisssrv-lib `unifi-network`, `networks` variable).
2. **The Hotspot pairing cannot be codified at this pin.** A built-in zone
   cannot be imported by name at 0.55.0 (the fix, upstream PR #401, merged
   after the tag and is unreleased), so the guest/Hotspot pair would move guest
   containment out of Terraform and into the UI.

Containment is therefore done by the two things that actually do the work: the
custom `guest` zone, whose only allow out is DNS, and `l2_isolation` on the
guest WLAN, which stops guest devices talking to each other. Both the module
README and this page use **#401** for the built-in-zone import gap — the
related-but-different open issue #396 asks for network→zone assignment to be
split off the zone resource, which is not what blocks anything here.

DHCP guarding (gateway-only DHCP server) is a **UI setting**, not a codified
one: the provider silently drops `dhcp_guarding.servers` on write for
corporate- and guest-purpose networks (#419), so declaring it would produce a
setting that never converges. Set it in the UI and record it here.

### Zones and policies

Every network gets **its own zone**, so the zone-based firewall's inter-zone
default-deny does the segmentation work: `homelab`, `home`, `iot`, `work`,
`guest` are custom zones created by Terraform, and the built-ins (`Internal`,
`External`, `Gateway`) are referenced through `data.unifi_firewall_zone` by
name.

**The baseline is UniFi's own, and it is wider than "deny":** no zone reaches
another internal zone, but **every zone reaches External and every zone reaches
Gateway**. No `ALLOW` list can narrow those two — they are defaults, not rules —
so the policy set has two kinds of entry: `ALLOW`s that open an inter-zone path
against the deny, and `BLOCK`s that fence off part of the two default-allow
paths.

**Eleven `ALLOW` entries** — the complete list of what crosses a VLAN boundary
(`create_allow_respond = true`, so stateful returns are created automatically,
except where noted):

| # | From → To | Scope | Why |
|---|---|---|---|
| 1 | home → homelab | any | Status-quo trust; per-port enforcement stays at the Proxmox firewall + NetworkPolicies |
| 2 | home → iot | any | Casting and device control (AirPlay/Chromecast data path) |
| 3 | homelab → iot | any | Home Assistant (.154) is the IoT controller |
| 4 | homelab → home | any | Plex → HDHomeRun, HA → TVs, admin reach |
| 5 | iot → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers |
| 6 | iot → homelab | tcp `:32400` → `.152` | Local Plex streaming from TVs |
| 7 | iot → homelab | tcp `:8123` → `.154` | HA's device-*initiated* paths: a Cast speaker fetching the TTS URL HA handed it, and integration webhooks posting back to `internal_url`. Policy 3 covers only what HA initiates |
| 8 | work → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers only |
| 9 | guest → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers only; everything else is internet-only |
| 10 | homelab → Internal | icmp → `10.0.1.2`/`.3` | The blackbox switch/AP probes: they run in a pod, so their echo requests arrive from VLAN 10 |
| 11 | Internal → homelab | icmp from `10.0.1.2`/`.3` | The echo *replies*. `create_allow_respond` is rejected for icmp, so the return direction is its own policy |

**Nine `BLOCK` entries** — narrowing the two default-allow paths:

| # | From → To | Scope | Why |
|---|---|---|---|
| 12-14 | {guest,iot,work} → Gateway | tcp `22,80,443`, logged | On a Cloud Gateway the console is a gateway service on *every* VLAN's own gateway address, so without these a guest with the WLAN PSK gets a login form at `https://10.0.40.1`. DHCP is broadcast before the client has an address and is unaffected; ICMP to the gateway stays up for troubleshooting |
| 15-17 | {guest,iot,work} → External | tcp/udp `53,853`, logged | DHCP option 6 is a suggestion: Chromecast hardware queries `8.8.8.8` regardless and most TVs ship a vendor resolver, so `:53`/`:853` outbound is fenced. Homelab is exempt — Unbound itself has to reach the internet |
| 18-20 | {guest,iot,work} → Gateway | tcp/udp `53,853`, logged | The other way off the resolvers: a UniFi OS gateway answers DNS on *every* VLAN's own `.1` and forwards to the WAN DNS servers (`1.1.1.1`/`9.9.9.9`, § Site settings), i.e. straight past AdGuard. Rows 15-17 and these together are what make "the weisssrv resolvers or nothing" true. DHCP (udp `67`/`68`) is untouched |

DoH on `:443` is **not** covered: it is indistinguishable from ordinary HTTPS at
this layer, and closing it needs the IDS/IPS or a blocklist (§ Day-2).

Blocked by the default deny, deliberately: iot → home, iot → work, work →
anything but DNS, guest → anything but DNS, home → work, and every
`Internal → homelab` path except the ICMP reply above.

Accepted, with eyes open:

- **Guests resolve through the split-horizon resolvers** (row 9). That is a
  user requirement — visitors get the household ad-blocking — and it means a
  guest device can *enumerate* internal names and addresses out of AdGuard's
  ~50 rewrites. It cannot reach any of them: everything but `:53` fails closed
  at the gateway. Disclosure, not access, and accepted as such.
- **Casting splits in two.** The flows that traverse home → iot work (cloud
  casts, Plex, TTS); the ones needing a connection *back* from the receiver do
  not — screen mirroring and casting local phone media — and AirPlay 2
  multi-room needs PTP timing multicast (udp 319/320) that does not cross a
  routed boundary at all. Validation rows 9a/9b test both halves.
- **No cross-VLAN SSDP/GDM/DLNA.** UniFi reflects mDNS but has no SSDP
  reflector, and multicast does not route, so anything discovered that way is
  configured by address instead (§ Codified vs manual).

The resolver, Plex and Home Assistant addresses and the two management-device
addresses are `locals` in the root (`dns_ips`, `plex_ip`, `ha_ip`,
`mgmt_device_ips`) — the single edit point Phase 2 used, and the one any
later homelab re-address uses.

**Policy ordering is not codifiable.** `unifi_firewall_policy.index` is
read-only and new policies append to the end of their zone-pair (upstream
#407). The set above is order-independent by construction: the `ALLOW`s are
allowances against a default deny rather than entries in a first-match list,
and each `BLOCK` targets a zone-pair (→ Gateway, → External) that no `ALLOW`
here touches (the two Gateway groups are disjoint by port: `22,80,443` tcp
against `53,853` tcp/udp). If that ever stops being true — an `ALLOW` and a
`BLOCK` on the same zone-pair — the ordering becomes a UI step and this page
must record it.

### Wireless

All four SSIDs are `wpapsk` on the U7 Pro XGS, `wlan_bands = ["2g","5g"]`.

| SSID | Network | WPA3 | Extras |
|---|---|---|---|
| TheRevengers | home | support + transition, PMF optional | Same PSK as today — devices roam over without re-onboarding |
| 3601-IoT | iot | off (plain WPA2, PMF disabled) | `allow_2ghz_high_perf = true` (the module inverts it to the provider's `no2ghz_oui = false`) — ESP32/Kasa-friendly 2.4 GHz |
| kugel-tikka-masala | guest | support + transition, PMF optional | `l2_isolation = true` |
| 3601-Work | work | support + transition, PMF optional | |

**6 GHz is deferred**: `unifi_wlan` creation fails when `6g` is in the band set
(upstream #406). Enabling it is a UI toggle plus a provider bump — tracked in
docs/16. Do NOT set `enhanced_iot`.

PSKs come from four 1Password items via `TF_VAR_wlan_passphrase_*` (§ Bench
pre-provisioning). `user_group_id` resolves from
`data.unifi_client_qos_rate` name `"Default"` — verify that name on the
controller at first plan (upstream examples omit it, so there is no
authoritative default).

### DHCP reservations

Codified as `unifi_client` entries with `allow_existing = true`; last octets
are preserved from the flat LAN where one existed.

| Network | Client | Address | MAC |
|---|---|---|---|
| home | macbook | `10.0.20.10` | `A2:30:58:E7:62:F2` — a macOS private address, see the note below |
| home | hdhr | `10.0.20.200` | `00:18:DD:0A:37:45` |
| iot | hue | `10.0.30.3` | `00:17:88:7E:C7:A2` |
| iot | K125M-0 … K125M-7 | `10.0.30.120`-`.127` | `6C:4C:BC:B0:0D:FE`, `:B0:0D:DD`, `:AF:F9:03`, `:AF:F0:AD`, `:AF:ED:23`, `:AF:E9:08`, `:AF:F0:DB`, `:B0:01:C8` |
| iot | living-room-hyperion | `10.0.30.210` | `B8:27:EB:A8:93:27` |
| iot | eric-bedroom-hyperion | `10.0.30.211` | `B8:27:EB:17:7D:DC` — **wired**, see the note below |
| iot | wled-kitchen-island | `10.0.30.213` | `9C:9C:1F:45:76:FE` |
| iot | wled-kitchen-cabinets | `10.0.30.214` | `9C:9C:1F:45:6B:5E` |
| iot | wled-bar | `10.0.30.215` | `9C:9C:1F:45:CF:F9` |
| iot | levoit-purifier | `10.0.30.216` | `A8:48:FA:34:3E:88` |
| iot | levoit-humidifier | `10.0.30.217` | `1C:9D:C2:73:00:B8` |
| iot | vizio-cast-display | `10.0.30.218` | `3C:9B:D6:7A:36:A3` — **wired** (MoCA leg), see the note below |
| iot | amazon-01f20c070 | `10.0.30.219` | `FC:49:2D:C3:D5:24` |
| iot | amazon-5b51cd6d9 | `10.0.30.220` | `38:F7:3D:11:A1:11` |
| iot | amazon-a70f51c2d | `10.0.30.221` | `DC:91:BF:D5:7E:E4` |
| iot | amazon-a9c5657f8 | `10.0.30.222` | `FC:49:2D:EA:F0:AA` |
| iot | amazon-f57e91 | `10.0.30.223` | `40:A2:DB:F5:7E:91` — presumed Echo |
| iot | amazon-c7d8bc | `10.0.30.224` | `34:D2:70:C7:D8:BC` — presumed Echo |
| iot | vizio-wifi | `10.0.30.225` | `A0:6A:44:50:EE:95` |
| default (mgmt) | usw-pro-xg-8 | `10.0.1.2` | `74:F9:2C:A6:A2:57` |
| default (mgmt) | u7-pro-xgs | `10.0.1.3` | `90:41:B2:C8:86:65` |

**A reservation is also VLAN steering, and that makes this table the standing
mechanism for putting a device on the right network.** A wireless client lands
on its reservation's network whichever SSID it associates with, so moving one is
"add an entry naming the target network" — it takes effect on the device's next
association, with no SSID re-join and nothing to configure on the device itself.
That is how the WLED controllers and the Kasa plugs arrived on IoT at cutover
without ever joining `3601-IoT`, and it is the immediate fix for every IoT-class
device that came up on Home: the Levoit pair, the TVs and the Echoes.

**Steering is placement, not authorization** — the reservation matches a
client-reported MAC, and a device still holding the `TheRevengers` PSK falls
back to Home the moment its MAC stops matching (randomized, spoofed after a
compromise, or replaced hardware). Re-onboarding onto `3601-IoT` (cutover
step 10) is therefore the step that actually removes the Home credential from
IoT-class devices, and it stays a required follow-up
([docs/16](16-next-steps.md)) even though nothing breaks while it waits. The
reservation keeps steering the device identically after the re-join, so the
migration is invisible at the network layer.

**Wired devices behind the unmanaged switches are the exception**, and two
entries above are marked for it: `eric-bedroom-hyperion` on the Connection A run
and `vizio-cast-display` on the MoCA leg. Steering them needs the USW to assign
a VLAN by MAC to a device it does not see on its own port. Where that works they
move like the wireless ones; where it does not, the entry is inert and the
device stays on whatever the port's native VLAN is — Homelab today, Home after
the Connection A finale. Placing a wired device on IoT in that case needs a
managed switch at the far end, which is already tracked in
[docs/16](16-next-steps.md). Check the controller's client list after the
unfreeze apply rather than assuming either outcome.

**Both Hyperion Pis are reserved on IoT, but only one of them is wireless.**
`living-room-hyperion` is wireless and steers cleanly. `eric-bedroom-hyperion`
is wired to the Connection A run and moved to IoT on 2026-08-23; whether the
reservation actually places it there depends on the MAC-based assignment
described above. Both landing on the same VLAN is the better outcome either way
— they address each other by their reserved IPs (§ SSDP does not cross VLANs),
and Home Assistant reaches both through `homelab-to-iot`.

**The admin MacBook's reservation depends on a MAC macOS randomises.** "Private
Wi-Fi Address" is on by default and per-SSID; the address reserved above is the
**per-network "Fixed" private address** the controller reports for TheRevengers,
not the hardware MAC. That is stable for as long as the network is remembered —
but forgetting and rejoining it, or toggling the setting, regenerates the
address, at which point the MacBook falls out of `10.0.20.8/29` and loses SSH,
`:8006`, `:6443`, the appliance UIs and RDP **at once**, because `admin_lan`,
the sshd `from=`, fail2ban's `ignoreip` and the `lan-tailscale-strict`
middleware all key off that /29. If it happens: read the new address from the
controller's client list, `-replace` the entry (§ Changing a client reservation
in the root README), and use **Tailscale** to get in meanwhile — `admin_ts` is
unaffected by any of this.

All three reservations that shipped as commented exemplars — the switch, the AP
and the MacBook — are filled in as of 2026-08-22. The device MACs were read at
adoption; the MacBook's once it associated with the new Home SSID, which is what
makes the `10.0.20.8/29` admin block in the Proxmox firewall mean anything.

Every reservation sits **outside** its network's DHCP pool, and the pool bounds
in `local.networks` are what enforce it — a reservation inside the pool can
collide with a lease the server has already handed out, while being in the same
*subnet* is the only thing the server itself requires. The reservations keep
the last octet each device had on the flat LAN, so the pools are bounded around
them rather than the other way round:

| Network | Pool | Below it | Above it |
|---|---|---|---|
| home | `.50`-`.199` | macbook `.10` | hdhr `.200` |
| iot | `.50`-`.99` | hue `.3` | K125M-0…7 `.120`-`.127`, then one device block `.210`-`.225` (`.212` unused): both Hyperion Pis, WLED `.213`-`.215`, Levoit `.216`-`.217`, TVs and Echoes `.218`-`.225` |
| default (mgmt) | `.100`-`.199` | switch `.2`, AP `.3` | — |

Adding a reservation means picking an address outside the pool for its network,
or moving the pool bound in `local.networks` first — both halves are one plan.

> **Trap:** `unifi_client` in-place **updates fail** on this provider version
> (upstream #428, `inconsistent result after apply: .last_ip`). Changing a
> name or a fixed IP needs `terraform apply -replace='module.network.unifi_client.this["<key>"]'`.

Homelab needs no reservations — hosts and guests are statically addressed by
Ansible. DHCP guarding (the gateway as the only permitted DHCP server) stays on
there as a UI setting, per the note under § Networks.

### Port forwards

| Name | WAN | → | Notes |
|---|---|---|---|
| http | tcp `80` | `10.0.10.100:80` | Public MetalLB VIP |
| https | tcp `443` | `10.0.10.100:443` | Public MetalLB VIP |
| plex | tcp `32400` | `10.0.10.152:32400` | docs/20 |
| gitlab-ssh | tcp `2222` | `10.0.10.153:2222` | Forwarded 2222→2222; the guest's own iptables PREROUTING rule redirects 2222→22, where sshd actually listens (docs/27 § Git SSH) |
| wg | **udp** `51820` | `10.0.10.99:51820` | wg-easy VIP — UDP, not TCP (docs/38) |

Every target is a `local` reference (`dns_ips`, `plex_ip`) or a VIP, so Phase 2
moved all five by editing the two locals and the subnet — the last octets are
unchanged from the `192.168.0.0/24` era.

### Site settings

| Setting | Value | Why |
|---|---|---|
| `mgmt.auto_upgrade` | `false` | Firmware is an operator-chosen window, not a surprise reboot of the whole network |
| `network_optimization.enabled` | `false` | Auto-optimize rewrites exactly the settings this repo codifies |
| `usg.upnp_enabled` / `upnp_nat_pmp_enabled` | `false` | Port forwards are declared, never negotiated |
| `igmp_snooping_networks` | `["home", "iot"]` | The two ends of the casting path. Homelab is deliberately out: snooping without a reliably elected querier prunes groups after the membership timeout, and VLAN 10 has nothing multicast-critical to gain (corosync is unicast knet) |
| `ips.ips_mode` | `"ids"` | Detection first; flip to `ips` after burn-in — § Day-2 |

**UPnP off is a user-visible trade-off for the game consoles.** The ASUS had it
on by default; with it off and no console-specific forwards, consoles on Home
report Strict / Type 3 NAT, which degrades party chat and matchmaking. That is
the right default for a codified network — a forward nobody declared is a
forward nobody reviews — and the remedy when someone complains is a per-console
`unifi_port_forward` entry in `local.port_forwards`, not re-enabling UPnP.

WAN DNS on the gateway is set to `1.1.1.1` + `9.9.9.9` plaintext in the UI, a
**deliberate divergence** from the DoT-to-internal-resolver arrangement the
ASUS ran: pointing the gateway at `.150`/`.160` creates a bootstrap loop
(resolvers are LXCs behind the gateway). LAN clients are unaffected — they get
the weisssrv resolvers by DHCP.

---

## Codified vs manual

| Area | Owner | Notes |
|---|---|---|
| Networks / VLANs / DHCP pools | **Terraform** | `default` is imported, the rest created |
| Firewall zones + policies | **Terraform** | Ordering is UI-only (#407) |
| WLANs (SSID, PSK, band, WPA3, isolation) | **Terraform** | 6 GHz deferred (#406) |
| Client fixed IPs | **Terraform** | Updates need `-replace` (#428) |
| Port forwards | **Terraform** | |
| Site settings (auto-upgrade, optimization, UPnP, IGMP, IPS mode) | **Terraform** | |
| Device adoption (switch, AP) | UI | The provider cannot create devices — adoption only |
| Per-port native/tagged VLAN assignment (the port map above) | UI | `unifi_device.port_override` is unsafe at 0.55.0: #438 wipes live overrides when the set is empty, #430 strips fields, #431 fails on unset Optional+Computed attributes. **Do not manage the switch with Terraform** |
| mDNS reflection | UI | Provider is read-only for this on UniFi OS gateways. **Network 10.x moved it from a per-network toggle to a SITE-level setting**: Settings → Networks → Multicast DNS, with three modes — Auto (reflect across all networks), Off, and Custom (pick the services and the networks each is reflected between). It is set to reflect between homelab, home and iot |
| Firewall-policy ordering | UI | |
| 6 GHz radio / band enablement | UI | Until the provider fixes #406 |
| WAN DNS servers | UI | See § Site settings |
| ui.com remote access + local Terraform admin + API key | UI | § Bench pre-provisioning |
| DHCP guarding (gateway-only DHCP server) | UI | `dhcp_guarding.servers` is dropped on write (#419) |
| IPS enablement (`ids` → `ips`) | Terraform, after a UI burn-in read | The mode is codified; the *decision* follows a week of IDS alerts |

**SSDP does not cross VLANs.** UniFi reflects mDNS but has no SSDP reflector,
so anything discovered over SSDP must be configured by address:

| Consumer | Target | Action |
|---|---|---|
| Plex (.152) | HDHomeRun `10.0.20.200` | Add the tuner by IP, not by discovery |
| Home Assistant (.154) | IoT devices on `10.0.30.0/24` | Any integration whose discovery fails gets a manual host entry |
| Hyperion / WLED | each other | Static addresses (reservations above) |
| Plex clients on IoT | Plex `.152:32400` | The client finds the server through plex.tv, not GDM — `:32400` is world-open (docs/20) |

That has a matching consequence one layer down, in the **Proxmox** firewall
([docs/11](11-firewall.md)): a guest-side rule only ever sees traffic a UniFi
zone policy already admitted, so a rule for a flow the gateway drops is dead
code that reads like a granted permission. Concretely, in
`ansible/inventories/prod/group_vars/all.yml`:

- `sg-plex` has **no** `10.0.30.0/24` rules at all. GDM (`32410-32414`) and
  SSDP (`1900`) are multicast and never leave the VLAN; unicast DLNA
  (`32469`) would be dropped by the zone firewall first, since the only
  iot → homelab allowances are `:53`, Plex `:32400` and HA `:8123`.
- `sg-haos` keeps `-source 10.0.30.0/24` on **udp 5353** — UniFi's mDNS
  repeater re-transmits the query on this interface with the *original* source
  address, so that rule really does match — and gains the same source on
  **tcp 8123**, pairing with `ALLOW` row 7. Its `1900` counterpart is gone: no
  UniFi feature reflects SSDP, so it could never have matched.

---

## weisssrv-side changes that ride with this

Phase 1 touches the repo in four places; each is documented where it lives.

- **Proxmox firewall** ([docs/11-firewall.md](11-firewall.md) § Client scopes):
  `admin_lan` shrinks to true admin surfaces and gains the `10.0.20.8/29`
  admin-device block; new `lan_clients` and `dns_clients` ipsets carry the
  service and resolver scopes. The two rule groups the collection renders
  itself follow via role variables (`weisssrv.infra` v0.13.0+):
  `proxmox_firewall_dns_client_sources` puts `sg-dns`'s `:53` on
  `dns_clients`, and `proxmox_firewall_k3s_ingress_int_sources` puts
  `sg-k3s-ingress-int` on `lan_clients`, so every VLAN can resolve and Home
  can reach the internal Traefik VIP. `ssh_authorized_keys` `from=`,
  `base_fail2ban_ignoreip` and `gitlab_ssh_allowed_users` mirror the admin
  split at the sshd layer.
- **Traefik internal allowlists** (both keys from
  `kubernetes/infrastructure/sources/cluster-config.yaml`): `lan-tailscale-only`
  gains `${cluster_home_cidr}` (`10.0.20.0/24`) so Home-VLAN devices reach the
  internal routes, which are all behind forward-auth anyway.
  `lan-tailscale-strict` gains the narrower `${cluster_home_admin_cidr}`
  (`10.0.20.8/29`) instead — it fronts the routes whose *only* gates are this
  list plus the backend's own login (1Password Connect, the router and AdGuard
  appliance UIs), and Traefik proxies from a k3s node that is itself inside
  `admin_lan`, so admitting the whole VLAN there would hand those UIs to every
  phone, TV and guest laptop that knows the house PSK. The /29 is exactly what
  `admin_lan`, the sshd `from=` restrictions and fail2ban's `ignoreip` carry, so
  the two layers say the same thing. No other client VLAN is allowlisted — the
  gateway's inter-zone deny is the first gate, the middleware the second.
- **`router.esweiss.com`** now proxies the gateway's HTTPS-only UI on `:443`
  through the `unifi-self-signed` ServersTransport (the UCG serves a
  self-signed certificate the acme.sh wildcard cannot cover), replacing the
  plaintext `:80` the ASUS served.
- **Observability** ([docs/31-observability.md](31-observability.md)): ICMP
  blackbox probes for `10.0.10.1`, `10.0.1.2` and `10.0.1.3` feed the
  `NetworkGearProbeFailed` alert (warning, 5m), with
  `NetworkGearProbeMissing` (warning, 15m) behind it as the drift guard for the
  pattern-matched target set. Those instances are excluded from the
  `EndpointDown` catch-all so one cause fires once.

### Expected breakage

The repo landed before the hardware did, and three things were wrong in that
window on purpose. **The window closed on 2026-08-22**; what follows records how
each one resolved, because two are gone and the third has been replaced by a
different expected-yellow with a different end date.

- **`router.esweiss.com` returned 502** while the ASUS served its UI on
  plaintext `:80` and the `vm-ingress` backend expected the UCG's HTTPS UI on
  `:443` (via the `unifi-self-signed` ServersTransport). **Resolved** at cutover
  when the UCG took over the gateway address — and **this branch brings it
  back**: the backend is renumbered to `10.0.10.1`, which nothing serves until
  § Phase 2 step 2 moves the gateway onto the new subnet. It clears itself at
  that flip; no other route touches this backend.

- **`NetworkGearProbeFailed` fires for `10.0.1.2` and `10.0.1.3`.** Still true,
  for a narrowed reason: the switch and the AP are adopted and their
  reservations are codified (§ DHCP reservations), but the entries have not been
  *applied*, so both devices still hold ordinary `10.0.1.100+` pool leases and
  nothing answers at `.2`/`.3`. It clears at the first apply after the pin bump
  below — not at a cabling change. A silence is active to 2026-08-29; renew it
  only if the pin bump slips, and let it expire rather than renewing by habit:

  ```bash
  task observability:silence ALERT=NetworkGearProbeFailed DURATION=7d
  ```

  The gateway probe was green from cutover on `192.168.0.1` — but this branch
  renumbers the target to `10.0.10.1`, so between this MR's merge and § Phase 2
  step 2 the gateway probe is red as well, for the same nothing-answers-yet
  reason.

- **`unifi-drift-plan` is yellow on every pipeline.** The job runs
  `terraform plan -detailed-exitcode` and carries `allow_failure: true`, so
  anything non-zero renders as one yellow badge. Its *original* causes are gone
  — the `UniFi Controller` vault item exists, and the gateway is reachable at
  `https://192.168.0.1`. While the root was pinned to module v0.13.0, the
  `setting_preference` provider default made the plan show a standing diff on
  the six networks' DHCP fields (§ Cutover as executed) — cosmetic **and
  enumerable**. The pin is now v0.13.1, so what remains of that diff is the
  one-time in-place `setting_preference` update per network plus the pending
  reservations, all consumed by the first supervised apply.

  **That apply retires this entry.** After it runs,
  the next scheduled `unifi-drift-plan` must go green,
  and a yellow after that is real drift or a broken credential — investigated,
  not ignored. (Narrowing the allowance to `exit_codes: 2`, so a broken plan is
  red and only drift is yellow, is a lib-wide follow-up for all three drift
  jobs, tracked in [docs/16](16-next-steps.md).)

---

## Bench pre-provisioning

Do all of this with the new gear on a bench, on its own uplink, **before**
touching the production cabling. Nothing here is disruptive; the ASUS keeps
serving the house throughout.

1. **First boot.** Power the UCG-Fiber, connect a laptop to a LAN port, and
   walk the setup wizard at <https://unifi.ui.com> (or the gateway's own
   address). Sign in with the ui.com account for remote access, set
   country/timezone, and take the one firmware update the wizard offers.
2. **Turn auto-upgrade off** immediately afterwards (Settings → System →
   Updates). Terraform also declares this, but the window between first boot
   and first apply is exactly when an unattended reboot hurts.
3. **Set the management network.** Default network → `10.0.1.1/24`, DHCP
   `10.0.1.100`-`10.0.1.199`. This is the network Terraform will *import*, so
   its name must stay `Default`.
4. **Create the Terraform admin and API key.** Settings → Admins → new admin,
   **local access only**, no 2FA (the provider cannot satisfy an MFA prompt);
   then Control Plane → Integrations → API Key. Copy both.
5. **Store the credentials** (the item titles are what `docs/15` and the
   Taskfile env anchor expect — do not rename them):

   ```bash
   op item create --category login --vault Homelab --title "UniFi Controller" \
     username=terraform url=https://10.0.10.1 password=<local-admin-password> \
     api-key=<integrations-api-key>

   op item create --category login --vault Homelab --title "WiFi 3601-IoT" \
     --generate-password='letters,digits,32'
   op item create --category login --vault Homelab --title "WiFi 3601-Work" \
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

   The same rule applies to the `password=` and `api-key=` values in the
   `UniFi Controller` item above: fill both fields in the app after creating
   the item skeleton.

   `url` is the **production** address (`https://10.0.10.1`). While the
   gateway is still on the bench, override it per invocation with
   `TF_VAR_unifi_api_url=https://<bench-address>` rather than editing the item.

6. **Adopt the switch and the AP** (Settings → Devices). Both come up on the
   Default network by DHCP. Once adopted, read their MACs, give them fixed
   addresses `10.0.1.2` (switch) and `10.0.1.3` (AP), and fill in the commented
   client entries in `terraform/unifi/` so the addresses are codified rather
   than UI state.
7. **Assign the port map** (§ Physical port map) in the UI — native VLAN per
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

1. **Back up both sides.** Download the UniFi `.unf` backup (Settings → System
   → Backups) and export the ASUS configuration. The `.unf` is the only fast
   path back to a configured controller.
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
6. **Re-cable the LAN** to § Physical port map: UCG port 6 (SFP+ 1) → switch
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
    `3601-Work` — an SSID is the only thing that places an *unreserved* device.
    Everything reserved in § DHCP reservations moves on its own: the WLED
    controllers, the Kasa plugs (`K125M-*`), the Levoit appliances, the TVs and
    the Echoes all land on IoT from any SSID, which is what the cutover proved.
    Re-onboarding them onto `3601-IoT` still happens, on no deadline — it is
    what removes the Home PSK from those devices, and steering alone does not
    (§ DHCP reservations, "placement, not authorization"). The Hue bridge moves
    to the wired IoT port (UCG port 1) rather than an SSID.
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
> The exception dies with the first supervised apply at this pin
> (`terraform/unifi/README.md` has the exact expected plan and the
> `-replace` it needs).

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
`10.0.30.x` at their reserved addresses without ever joining `3601-IoT`. A
`unifi_client` reservation names the network a device joins from any SSID. Step
10's re-onboarding is therefore hygiene for anything already reserved, and the
fix for an IoT-class device sitting on Home is to reserve it — which is what the
Levoit pair, the TVs and the Echoes now do (§ DHCP reservations). Whether it
also works for a WIRED device behind an unmanaged switch is the open question
those entries test.

### Post-cutover checklist

Every item in § Expected breakage was true *on purpose* before the window. Each
has to be actively retired, or the next person reads a stale allowance as a
sanctioned state. Status as of 2026-08-22:

- [x] **`router.esweiss.com` serves the UCG UI** over the `unifi-self-signed`
  transport — the 502 cleared when the UCG took the gateway address, with no
  repo change, as § Expected breakage predicted.
- [x] **The admin MacBook reservation is filled in** (`10.0.20.10`, the
  per-network private Wi-Fi address) — § DHCP reservations. It takes effect at
  the apply below; until then the MacBook holds a pool address outside
  `10.0.20.8/29` and has no admin reach except over Tailscale.
- [x] **The two mgmt reservations are filled in** (`10.0.1.2`, `10.0.1.3`, MACs
  read at adoption). Also pending the apply — both devices hold ordinary pool
  leases today, which is why `NetworkGearProbeFailed` is still red for them.
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
- [ ] **Run the finishing supervised apply at v0.13.2.** This is the gate every
  remaining item sits behind. The expected plan is exactly **sixteen
  reservation creates and nothing else** — no `-replace` needed any more (the
  interrupted replace already removed the old `eric-bedroom-hyperion` entry);
  full reasoning in `terraform/unifi/README.md`. The apply converges the plan
  to no-changes.
- [ ] **Expire the `NetworkGearProbeFailed` silence** (set to 2026-08-29) rather
  than renewing it, and confirm all three probes are green (validation row 18).
- [ ] **`unifi-drift-plan` is green on the next schedule** (validation row 23).
  From here on a yellow is drift or a broken credential.
- [ ] **Finish Connection A** — configure pve-nas-01's tagged sub-interface
  (§ Cutover, step 7), then flip switch port 7 from native Homelab to native
  Home with VLAN 10 tagged. Until both halves are done, everything on that run
  is on VLAN 10 with full homelab reach (§ Physical port map).
- [ ] **Clear the stale `ct:150` replicas.** The HA relocation left older-
  generation `subvol-150-disk-0` volumes on pve-opt-01, pve-opt-03 and
  pve-laptop-01. Replication is healthy again (4/4, FailCount 0) and these are
  inert, but they are misleading during a future recovery.
- [ ] **Re-verify the bond procedure in [docs/34](34-bond-mac-flapping.md)** —
  the managed switch is a new link partner for all three bonded hosts.
- [ ] **Start the IPS burn-in clock** — a week of `ids` detections before
  flipping to `ips` (§ Day-2).

---

## Validation

Run the whole matrix before declaring the cutover done. "Expected" is what a
correct segmentation produces — several rows are *failures by design*.

Two rows are **gated on the pending work in § Post-cutover checklist** and
cannot pass yet: row 2 needs the Connection A finale, row 18 needs the v0.13.1
pin bump and the apply that lands the mgmt reservations.

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Per-VLAN DHCP | Join each SSID / plug into each access port | Address from the right pool, DNS `.150`/`.160`, domain `esweiss.com` (the **mgmt** VLAN is the exception: `1.1.1.1`/`9.9.9.9`) |
| 2 | Stranded wired leases | Controller client list after step 6 | Every wired device on the Connection A run has a `10.0.20.x` address — no VLAN-10 address (`192.168.0.x` on cutover night, `10.0.10.x` after § Phase 2) outside VLAN 10. **Gated:** while port 7 is native Homelab this run is legitimately on VLAN 10, so the row only becomes meaningful after the Connection A finale |
| 3 | Resolver reach from every VLAN | `dig @10.0.10.150 git.esweiss.com` from home/iot/guest/work | Answer on all four |
| 4 | Guest containment | From guest: `curl -m5 https://git.esweiss.com`, ping another guest client | Both **fail** (DNS resolves, everything else denied; L2 isolation blocks the peer) |
| 5 | IoT containment | From an IoT device: reach anything on Home or `:443` on homelab | **Fails**; only `:53`, Plex `:32400` and HA `:8123` succeed |
| 6 | Work containment | Same from Work | Only `:53` succeeds |
| 7 | Gateway console fenced | From guest/iot/work: `curl -m5 -k https://<that VLAN's .1>` and `ssh <that VLAN's .1>` | Both **fail** (BLOCK rows 12-14). `ping <that VLAN's .1>` still works — icmp is deliberately left up |
| 8 | External DNS fenced | From guest/iot/work: `dig @8.8.8.8 example.com`, `dig +tls @8.8.8.8 example.com` | Both **fail/time out** (BLOCK rows 15-17); `dig @10.0.10.150` still answers |
| 8b | Gateway resolver fenced | From guest/iot/work: `dig @<that VLAN's .1> example.com` | **Fails/times out** (BLOCK rows 18-20). Confirm on the bench *before* cutover that the gateway answers this at all with the BLOCKs removed — the rows exist because a UniFi OS gateway normally does, and a bench `dig` is how that is established rather than assumed |
| 9a | Casting — the half that works | Cast a YouTube or Plex stream from a Home phone to an IoT TV/speaker | Device is discovered (site-level mDNS reflection) and plays |
| 9b | Casting — the half that does not | Screen-mirror / cast a local photo from the same phone; AirPlay to two speakers at once | **Fails, by design** — the receiver would have to open a connection back to Home, and AirPlay 2 needs PTP multicast that does not route |
| 10 | Plex local stream | Play from a TV — the Vizio pair and the Amazon units are reserved onto IoT, though the wired one only lands there if MAC-based assignment takes (§ DHCP reservations); check the client list for which VLAN it is actually on, then test | Direct play from `10.0.10.152:32400`, no transcode-over-WAN, from **either** VLAN — `iot-to-homelab-plex` and `home → homelab` both allow it. If it transcodes, check Plex's LAN Networks setting (cutover step 11) before suspecting the network |
| 11 | HDHomeRun | Live TV in Plex | Tuner reachable at `10.0.20.200` (configured by IP) |
| 12 | Internal ingress from Home | Browse `https://grafana.esweiss.com` from a Home laptop | 200 — the `cluster_home_cidr` allowlist entry |
| 13 | Appliance UIs from Home | Browse `https://router.esweiss.com` from a **non**-admin Home device, then from the admin MacBook | Non-admin **fails** (`lan-tailscale-strict` is the `10.0.20.8/29` block); admin succeeds |
| 14 | Admin surfaces from Home | SSH to a Proxmox host from a **non**-admin Home device | **Refused** — only `10.0.20.8/29` is in `admin_lan` |
| 15 | Port forwards | From off-net: `curl -I https://<public>`, Plex remote, `ssh -p 2222 git@git.ericsweiss.com` | All succeed |
| 16 | wg-easy | Connect a WireGuard client from cellular | Handshake completes, internet egress works (docs/38) |
| 17 | Tailscale | `tailscale status` on a host; reach a guest over the tailnet | Subnet route still advertised and approved (docs/05) |
| 18 | Network gear probes | Grafana / Prometheus | `NetworkGearProbeFailed` clear for all three targets (needs ALLOW rows 10/11 *and* the two mgmt reservations **applied**, not merely codified). `192.168.0.1` is green today; `.2`/`.3` clear at the first apply after the pin bump |
| 19 | Bond health | `cat /proc/net/bonding/bond0` on each opt node | `all_slaves_active 0`, one active leg (docs/34) |
| 20 | e1000e / AQC113 watch | Loki, over the next 48 h: `{job="journal"} \|= "Hardware Unit Hang"` | No hits (docs/34) |
| 21 | HA re-armed | `ha-manager status` | All four resources `started`, none `ignored` (cutover step 9) |
| 22 | No IPv6 on clients | `ip -6 addr` on a client from each VLAN | Link-local `fe80::` only — no GUA, no ULA (§ Networks) |
| 23 | Drift plan | Next scheduled pipeline after the first apply | `unifi-drift-plan` **green** — see § Expected breakage |
| 24 | Cluster health | `task flux:status`, `task infra:verify`, `task k3s:status` | Clean |

## Rollback

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

## Day-2 operations

- **Drift.** `unifi-drift-plan` (advisory) re-plans on merge and on schedule. A
  yellow job means the controller and the code disagree: either the change was
  an intended UI hot-fix (codify it, MR it, next plan is clean — do **not**
  apply first, apply would revert it) or nobody meant to change anything, which
  is an incident.
- **IPS after burn-in.** Ships as `ips_mode = "ids"`. After a week of clean
  detections, flip to `"ips"` in the root and supervised-apply. Note upstream
  #381: `ips.suppression_alerts` is not persisted, so suppressions are a UI
  concern with a permanent diff if codified.
- **Firmware.** Auto-upgrade is off by design. Upgrade in a chosen window,
  gateway last; `NetworkGearProbeFailed` will fire during the reboot, which is
  why it is a warning rather than a page.
- **Adding a device to a VLAN** is DHCP — no repo change. Adding a *reservation*
  is a `unifi_client` entry (remember `-replace` for edits, #428).
- **Anything the switch does per-port** stays a UI change, recorded here.

---

## Phase 2 — homelab renumber

> **This ordering is a DRAFT written before the window, for review during the
> Phase 2 session.** Nothing below is a script to run unattended. Every step
> gets live re-verification against the actual cluster state at the moment it
> runs — addresses, quorum, etcd membership and Flux health all move between
> the day this was written and the day it is executed. Re-read § Rollback in
> this section before the first command.

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
| **Everything inbound from the internet** — all five WAN forwards: `*.ericsweiss.com`, Plex remote, `ssh -p 2222 git@git.ericsweiss.com`, the wg-easy endpoint | step 2 apply | step 2.8 if the early VIP restore is done (minutes), otherwise step 7 (hours) | The gateway cannot forward to `192.168.0.100` once VLAN 10 *is* `10.0.10.0/24`. The forward targets in the same apply are not the cause and re-pointing them is not the cure — nothing answers on the new VIPs until MetalLB re-announces them |
| DNS on the IoT / Guest / Work VLANs | step 2 apply | step 2.6 (minutes, once clients re-DHCP) | Those clients hold leases naming `192.168.0.150`/`.160`; after the flip the gateway has no route to that subnet at all |
| Home Assistant **on its own address** (`http://10.0.10.154:8123`, the app on the LAN) | step 2.7 | step 2.7 (one reboot) | HAOS is the one guest that cannot be dual-addressed — see that step |
| Home Assistant **through Traefik** — `home.esweiss.com` / `home.ericsweiss.com`, and the five HA-bypass IngressRoutes (tv/movies/nzbget/qbittorrent/music) | step 2.7 | step 7 (the Flux reconcile) | Flux is suspended, so the live cluster still publishes the EndpointSlice address `192.168.0.154` (`apps/vm-ingress/services-default.yaml`) and still matches `ClientIP(192.168.0.154/32)` (`apps/download-clients/ingress-routes-ha-bypass.yaml`). Those are per-guest literals, not `${cluster_*}` placeholders, so they move only when the branch itself reconciles. Expect 502 on the two hostnames and HA's *arr integrations falling through to the SSO route |
| The other vm-ingress guests through Traefik — plex, gitlab, nextcloud, immich | step 5 (when each drops its old address) | step 7 | Same EndpointSlice mechanism. Reaching them by address inside VLAN 10 keeps working throughout |

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

**1. Disarm Proxmox HA — before any host, quorum or reboot work.** Phase 2 is
its own window, raised after Phase 1 validates, so HA is armed again when it
opens (cutover step 9 re-armed all four resources and made that the closing
condition of that window). Everything from step 2.7 onwards either reboots an
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

**4. Confirm the out-of-band admin path before the first command.** wg-easy is
down for the inbound gap (§ Why this cannot be a rolling change) and step 9
puts Tailscale in flux, so the fallback has to be something this plan is not
touching that hour — plus a physical console.

**5. Keep a Home-VLAN admin station** (`10.0.20.8/29`, already in `admin_lan`).
It reaches the homelab through the gateway, so it survives the flip as soon as
the hosts carry their new addresses — which is exactly why step 1 comes first.
Every Ansible and `kubectl` step below runs from here.

**6. Keep a console path** to pve-nas-01 and at least one other host.
IPMI/monitor + keyboard; the whole plan assumes you can recover a host whose
`interfaces` file you have just broken.

**7. Take the backups**: UniFi `.unf`, `task k3s:backup` (etcd snapshot), and a
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

Verify before going further: from any host, ping every other host **and every
guest** on **both** addresses. A machine that answers on only one is a machine
you are about to lose.

The list below is every statically addressed machine in VLAN 10 except `.154`
(HAOS, which step 2.7 flips instead). Cross-check it against
`scripts/hosts.env` (`task hosts:sync` regenerates that file from the
inventory) rather than trusting this copy — an omission here is invisible until
the gateway has already moved.

```bash
for n in 102 103 104 105 106 107 150 151 152 153 155 156 157 158 160 \
         202 203 204 205 206 207 222 223 227; do
  for net in 192.168.0 10.0.10; do
    ping -c1 -W1 "$net.$n" >/dev/null 2>&1 || echo "MISSING $net.$n"
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

**2.1 — move the controller's own address.** Update the `UniFi Controller`
1Password item's `url` field **first**; every plan after the flip connects
there.

```bash
op item edit "UniFi Controller" --vault Homelab url=https://10.0.10.1
```

**2.2 — plan.** From the Home-VLAN admin station: `task terraform:unifi-plan`.
The homelab network must show an **in-place update** of `subnet` and the DHCP
scope, plus the port-forward target updates. A `-/+ replace` on
`unifi_network.this["homelab"]` means `prevent_destroy` is about to abort the
apply — stop and re-read; a replace loses the network's ID and every reference
to it.

**2.3 — apply.** Supervised `task terraform:unifi-apply`. From this second, the
gateway answers on `10.0.10.1` and nothing else in the estate has a default
route.

**2.4 — host default routes.** On each Proxmox host (the permanent edit is step
3):

```bash
ip route replace default via 10.0.10.1
ping -c1 1.1.1.1 && getent hosts deb.debian.org    # egress + resolution
```

**2.5 — guest default routes, resolvers first.** Every guest still carries
`gateway 192.168.0.1`, which no longer exists. Repair them here, not in step 5:
until this is done dns-01/dns-02 cannot reach their DoT upstreams, so *every
non-rewritten name in the estate stops resolving* — and smtp-relay (alert mail),
acme.sh, GitLab, Nextcloud, Immich and every k3s node's egress go with it.

Order is not negotiable — **the two resolvers first**, then gate on them:

```bash
# dns-01 (.150) and dns-02 (.160) — from their Proxmox hosts
pct exec 150 -- ip route replace default via 10.0.10.1
pct exec 160 -- ip route replace default via 10.0.10.1

# GATE: real recursion through each resolver before anything else moves
dig @10.0.10.150 example.com +short
dig @10.0.10.160 example.com +short
```

Both must return an address. If either does not, fix it before continuing —
everything below assumes working resolution.

Then the rest, same command per class as step 1:

```bash
# remaining LXC guests: smtp-relay 151, plex 152, immich-ml 158
for v in 151 152 158; do pct exec "$v" -- ip route replace default via 10.0.10.1; done

# cloud-init VMs and k3s nodes, over SSH to their new addresses
for n in 153 156 157 202 203 204 205 206 207 222 223 227; do
  ssh "eric@10.0.10.$n" 'sudo ip route replace default via 10.0.10.1' || echo "FAILED .$n"
done

# windows .155, at an elevated prompt — one command sets address, mask and the
# adapter's CONFIGURED default gateway together, which is what survives a
# reboot. Do not use `route -p add`: a persistent route leaves the adapter's
# configured gateway at 192.168.0.1, which is re-applied at boot with no
# 192.168.0.x address left to reach it from.
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

**2.6 — make the client VLANs re-DHCP.** IoT, Guest and Work clients hold leases
naming `192.168.0.150`/`.160`. Their own VLAN did not change, so they will not
re-DHCP on their own — and the gateway now has no route to that subnet at all,
so those three VLANs have *no* DNS until each lease renews (24 h by default).
The DNS allow policies moving to the new resolver addresses in the same apply is
a detail; the missing route is the outage.

Force the renewal instead of waiting:

```
Controller → Settings → WiFi → 3601-IoT / kugel-tikka-masala / 3601-Work:
  toggle "Enable" off, save, on, save.        # every client re-associates and re-DHCPs
Controller → the wired IoT access ports (2.5G-3, and any other IoT/Work port):
  toggle the port off/on, or power-cycle PoE. # wired IoT gear, e.g. the Hue bridge
```

Then **walk one client per VLAN** and confirm both halves:

```bash
# on a client joined to each of iot / guest / work
ipconfig /all | findstr "DNS"    # or: resolvectl status / cat /etc/resolv.conf
dig @10.0.10.150 git.esweiss.com +short
```

The lease must name `10.0.10.150`/`.160` and the query must answer. If
re-associating the whole house is impractical, the alternative is to shorten
`dhcp.leasetime` on those three networks (module input, default `24h0m0s`) in a
supervised apply a day *before* the window and put it back afterwards — two
extra applies, but no walking.

**2.7 — Home Assistant (coordinated flip).** HAOS cannot hold two addresses, so
it moves now, in one step, from its console (Proxmox → VM 154 → Console):

```
ha > network info                       # note the interface name (e.g. enp0s18)
ha > network update <interface> --ipv4-method static \
       --ipv4-address 10.0.10.154/24 \
       --ipv4-gateway 10.0.10.1 \
       --ipv4-nameserver 10.0.10.150 --ipv4-nameserver 10.0.10.160
ha > host reboot
```

Then from a host: `ping -c1 10.0.10.154` and `curl -sk -o /dev/null -w '%{http_code}\n' http://10.0.10.154:8123`.
HA is a Proxmox HA resource — confirm it is still `ignored`/paused per the
cutover HA-pause procedure, or that a reboot will not trigger a recovery, before
issuing `host reboot`.

**2.8 — restore inbound early (strongly recommended).** All five WAN forwards
are dead from 2.3 until MetalLB announces the VIPs on the new subnet. Left to
step 7 that is hours. The nodes are already dual-addressed, so the VIPs can move
now. These patches hold only because **all five** suspends from § Repo/CI
posture item 2 are in place — in particular `traefik/helmrelease/traefik`,
without which helm-controller drift-corrects the public VIP annotation back
within 30 minutes. Re-check before patching:

```bash
flux get kustomizations -A | grep -E 'flux-system|infrastructure-configs|infrastructure-controllers|^apps'
flux get helmreleases -n traefik
```

With those suspended the patches stand until step 7 reconciles the same values
from `cluster-config` and makes them a no-op:

```bash
# pools first, then the Services that claim them; wg-easy first so the VPN
# fallback comes back before anything else.
kubectl -n metallb-system patch ipaddresspool vpn-pool      --type merge -p '{"spec":{"addresses":["10.0.10.99/32"]}}'
kubectl -n metallb-system patch ipaddresspool public-pool   --type merge -p '{"spec":{"addresses":["10.0.10.100/32"]}}'
kubectl -n metallb-system patch ipaddresspool internal-pool --type merge -p '{"spec":{"addresses":["10.0.10.101/32"]}}'

kubectl -n wg-easy annotate svc wg-easy          metallb.io/loadBalancerIPs=10.0.10.99  --overwrite
kubectl -n traefik annotate svc traefik          metallb.io/loadBalancerIPs=10.0.10.100 --overwrite
kubectl -n traefik annotate svc traefik-internal metallb.io/loadBalancerIPs=10.0.10.101 --overwrite

kubectl get svc -A -o wide | grep LoadBalancer   # EXTERNAL-IP on all three is 10.0.10.x
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

**2.9 — widen the host-side allowlists to BOTH subnets.** This is the step that
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
  That covers every k3s NFS PV, all six hosts' `/export/backups-proxmox`, and
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
firewall_ipset_special_entries:
  pve_hosts:
    - {ip: 192.168.0.0/24, comment: TRANSITION old homelab subnet}
  core-cluster:
    - {ip: 192.168.0.0/24, comment: TRANSITION old homelab subnet}
  nfs_clients:
    - {ip: 192.168.0.0/24, comment: TRANSITION old homelab subnet}
  k3s_nodes:
    - {ip: 10.0.10.161, comment: k3s API VIP}
    - {ip: 192.168.0.161, comment: TRANSITION old k3s API VIP}
    - {ip: 192.168.0.0/24, comment: TRANSITION old homelab subnet}
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
`pvecm status` still 6/6, `kubectl get nodes` still 9/9 Ready, and an existing
NFS-backed pod still reads its volume.

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

If quorum is lost mid-edit the recovery is `pvecm expected 1` on one node,
restore `/root/corosync.conf.pre-renumber`, restart `corosync` + `pve-cluster`.

### Step 4 — move the k3s API VIP (once, before any node is redeployed)

**This is the single API-VIP step, and it comes before step 5, not after step
6.** Every node config on this branch — server *and* agent — templates
`server: https://{{ k3s_api_vip }}:6443` from one variable, which is
`10.0.10.161` here. So the first node redeployed from the branch already tries
to reach the new VIP: if it does not exist yet, that node hangs. There is no
per-node override to bridge with (see step 6).

All three servers are still up and, since step 1, dual-addressed — `10.0.10.161`
is announceable on the same L2 they already sit on. So move it now, while
nothing is drained:

```bash
# etcd gate first (step 6 has the full command block) — 3 healthy members
sudo -E etcdctl endpoint health --cluster --write-out=table

ansible-playbook ansible/playbooks/k3s.yml --limit k3s_servers   # from the branch

# kube-vip re-elects and ARPs the new VIP; the old one stops being announced
curl -sk https://10.0.10.161:6443/readyz          # "ok"
task k3s:kubeconfig                                # kubeconfig follows the VIP
kubectl get nodes                                  # all 9 still Ready
sudo -E etcdctl endpoint health --cluster --write-out=table
```

The play already runs `k3s_servers` at `serial: 1`, so expect a few seconds of
API unavailability per server rather than all at once; do not proceed until
`kubectl get nodes` is clean. Rollback is the same play from `main`, which puts
`k3s_api_vip` back to `192.168.0.161` — the server VMs still hold both addresses
until step 5, so this stays reversible up to that point.

### Step 5 — guests, then k3s agents (one at a time)

Routes and DNS are already correct inside every guest — step 2.5 did that, and
Home Assistant took its whole flip at 2.7. What is left here is making the new
address **permanent** (so a reboot or a rebuild keeps it) and dropping the old
one. Per class:

| Class | Permanent form |
|---|---|
| LXC | on the host: `pct config <vmid> \| grep ^net0` → re-set that *whole* line with only `ip=`/`gw=` changed (`pct set <vmid> --net0 name=eth0,bridge=vmbr0,hwaddr=<unchanged>,ip=10.0.10.<n>/24,gw=10.0.10.1`), then `pct reboot <vmid>`. Keeping `hwaddr` matters — a new MAC invalidates the UniFi reservation and the DHCP-independent identity the firewall aliases assume |
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

**dns-01 and dns-02 come first** — everything downstream resolves through them,
and their own `acme_certs_distribution_targets` list (in `host_vars/dns-01.yml`)
now names the new addresses, so the cert push has to be re-run after they move:
`task dns:deploy`, then the cert distribution.

Then run the playbook that owns each guest, from the branch.

Then the AdGuard rewrites: `group_vars/dns.yml` carries ~50 A rewrites plus the
PTR rules in `adguard_home_filters`, which have moved from
`<n>.0.168.192.in-addr.arpa` to `<n>.10.0.10.in-addr.arpa`. Push them with
`task dns:deploy` once both resolvers
are on their new addresses — before this point clients resolve to addresses that
are still secondary-only, which works but hides mistakes.

k3s agents, **one node at a time**:

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
# re-IP the VM (console or recreate per docs/19), then from the branch:
ansible-playbook ansible/playbooks/k3s.yml --limit <node>       # or task k3s:deploy
kubectl get node <node> -o wide     # INTERNAL-IP is the new address
kubectl uncordon <node>
```

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
   and wipe `/var/lib/rancher/k3s/server/db/etcd` so it rejoins as a fresh
   member rather than an unreachable one.
3. Re-IP the VM (step 5's cloud-init form), then re-run the k3s play for that
   host from the branch:

   ```bash
   ansible-playbook ansible/playbooks/k3s.yml --limit <server>
   ```

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
`backups-proxmox` storage inactive on the other five hosts, and any pod holding
a stale handle needs **deleting**, not restarting (docs/12; a Flux-managed
workload drift-reverts a `rollout restart`). Do it last, and expect to work
through the remount list at step 8:

```bash
pvesm status                       # on each host: backups-proxmox active again
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
converged by hand.

1. Merge, then confirm Flux can see it:

   ```bash
   flux get sources git flux-system      # revision is the merge commit
   ```

2. Resume in the reverse order of § Repo/CI posture item 2, then reconcile:

   ```bash
   task flux:resume -- traefik/helmrelease/traefik
   task flux:resume -- flux-system/kustomization/apps
   task flux:resume -- flux-system/kustomization/infrastructure-controllers
   task flux:resume -- flux-system/kustomization/infrastructure-configs
   task flux:resume -- flux-system/kustomization/flux-system
   task flux:reconcile
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
   actually down). Expect a burst of `EndpointDown`/`BlackboxProbeFailed` while
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
pvesm status          # on each host: backups-proxmox active
```

### Step 9 — Tailscale (supervised, can sever remote admin)

Order matters: apply the **policy first**, then change what the subnet routers
advertise, so the new route is auto-approved rather than sitting pending.

1. `task terraform:tailscale-plan` → review → supervised apply. The policy now
   names `10.0.10.0/24` in ACL rule 2's `dst` and in `autoApprovers.routes`.
2. Re-run the Proxmox play so `tailscale_advertise_routes` advertises
   `10.0.10.0/24`.
3. `tailscale status` on a host and, from a tailnet client, reach a homelab
   service by its internal name. Only then remove the old route from the
   admin console if it lingers.

### Step 9b — re-arm Proxmox HA

The mirror of § Repo/CI posture item 1, and the window is not closed until it is
done and verified. Do it here, after the last reboot and the last quorum
change — but do **not** leave it to "sometime after validation": a window that
ends with HA still `ignored` looks healthy and has no failover.

```bash
for sid in ct:150 ct:151 ct:160 vm:154; do sudo ha-manager set $sid --state started; done
sudo ha-manager status          # all four "started", homes as configured (docs/25)
```

### Step 10 — final validation

The MR merged at step 7; this step is validation only. Re-run the § Validation
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

Grep 1 legitimately keeps hits in `docs/` and `terraform/unifi/README.md`: the
cutover runbook and the Phase 2 narrative describe the pre-renumber world on
purpose. Every one of those must read as a labelled historical or cutover-night
statement — if a hit is a live instruction, it is a straggler.

The MR is already merged — step 7 needed it, because Flux reconciles `main`.
What closes the window is this matrix plus row 21: `ha-manager status` showing
all four resources `started`.

### Rollback

The further in, the more this is a roll-*forward* migration — but each step has
its own reversal, and steps 1-2 are cheap:

- **Before step 3**: revert the gateway (`local.networks.homelab.subnet` back to
  `192.168.0.1/24`, supervised apply, `url` field back), undo the 2.8 MetalLB
  patches the same way (`kubectl patch`/`annotate` back to `192.168.0.x`, or
  just resume the five suspends and reconcile from `main`), and put HAOS back
  with the same `ha network update` at 2.7. Hosts and guests still hold both
  addresses; nothing else changed. HAOS is the only piece that took a one-way
  flip, and its reversal is one console command plus a reboot. The 2.9
  transition pushes are supersets — they need no reversal, but re-running them
  without `-e` restores the `main` values.
- **Step 3** (corosync): restore the saved `/root/corosync.conf.pre-renumber`,
  `pvecm expected 1` if quorum is gone, restart `corosync` and `pve-cluster`.
  Every host still holds both addresses at this point, which is why this
  reversal is cheap.
- **Step 4** (API VIP): re-run the k3s play for `k3s_servers` from `main` — the
  VIP goes back to `192.168.0.161`, which is still announceable while the server
  VMs hold both addresses (i.e. until step 5).
- **Steps 5-6**: a drained node that will not rejoin is a rebuild, not a
  rollback — `task k3s:deploy` for that node against the old address, or restore
  the etcd snapshot from `task k3s:backup` (docs/19).
- **Step 6b** (host address drop): `cp /root/interfaces.pre-drop
  /etc/network/interfaces && ifreload -a` on the affected host, from the
  console. This is the last cheap reversal on the wire.
- **Step 7**: re-suspend the same five resources (§ Repo/CI posture item 2) to
  stop the bleeding; the previous `cluster-config` is one `git revert` on `main`
  away, and re-applying the 2.8 patches restores inbound in the meantime.
- **Step 9b**: nothing to reverse — but a rolled-back window still leaves the
  four HA resources `ignored` until someone sets them back.

One requirement on that MR belongs here, because it is the trap this repo has
already hit once: the straggler sweep for `192.168.0.` must match the
**escaped-regex spelling** as well as the plain one. Prometheus rule
expressions, promtool fixtures and Grafana dashboard JSON carry addresses as
`192\\.168\\.0\\.` (two literal backslashes per dot), so a
`grep -rIn "192\.168\.0\."` finds every easy occurrence and none of the hard
ones. Validation row P2-1 above runs both greps; that is the check, not this
paragraph.

---

## Related documentation

- [`terraform/unifi/README.md`](../terraform/unifi/README.md) — the root's own reference (managed objects, credentials, import recipes, provider caveats)
- [docs/01-overview.md](01-overview.md) — topology and the VLAN table
- [docs/08-dns.md](08-dns.md) — resolvers, rewrites, and the per-VLAN DHCP DNS
- [docs/11-firewall.md](11-firewall.md) — the `admin_lan` / `lan_clients` / `dns_clients` split
- [docs/12-runbooks.md](12-runbooks.md) — HA drain/maintenance and the stale-NFS-handle recovery the cutover references
- [docs/20-plex-deployment.md](20-plex-deployment.md) — Plex's LAN Networks setting and the `:32400` forward
- [docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md) — bond and NIC-offload behaviour to re-verify after cutover
- [docs/38-wireguard-vpn.md](38-wireguard-vpn.md) — the wg-easy VIP the DHCP pool excludes
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the `UniFi Controller` and `WiFi *` 1Password items
- [docs/16-next-steps.md](16-next-steps.md) — the follow-ups this work opened (6 GHz, unpoller, IPS)
