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

**Migration state.** Phase 1 (this document) introduces the VLANs while the
homelab stays on `192.168.0.0/24`. Phase 2 renumbers it to `10.0.10.0/24` and
ships as its own MR after Phase 1 has been validated — § Phase 2.

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
| `homelab` | Homelab | 10 | `192.168.0.1/24` | `.2`-`.98` | Hosts, guests, k3s, VIPs. Phase 2 → `10.0.10.1/24` |
| `home` | Home | 20 | `10.0.20.1/24` | `.50`-`.199` | Personal client devices. Pool stops at `.199` — the reservations sit above it (§ DHCP reservations) |
| `iot` | IoT | 30 | `10.0.30.1/24` | `.50`-`.99` | IGMP snooping on (as does Home — the two ends of the casting path). A 50-address dynamic range: every IoT device that matters is reserved at `.120`+ |
| `guest` | Guest | 40 | `10.0.40.1/24` | `.50`-`.249` | `purpose = corporate` — see below |
| `work` | Work | 50 | `10.0.50.1/24` | `.50`-`.249` | |

Every **client** VLAN hands out `192.168.0.150` / `192.168.0.160` as DHCP DNS
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
`mgmt_device_ips`) — one edit point for Phase 2.

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
| http | tcp `80` | `192.168.0.100:80` | Public MetalLB VIP |
| https | tcp `443` | `192.168.0.100:443` | Public MetalLB VIP |
| plex | tcp `32400` | `192.168.0.152:32400` | docs/20 |
| gitlab-ssh | tcp `2222` | `192.168.0.153:2222` | Forwarded 2222→2222; the guest's own iptables PREROUTING rule redirects 2222→22, where sshd actually listens (docs/27 § Git SSH) |
| wg | **udp** `51820` | `192.168.0.99:51820` | wg-easy VIP — UDP, not TCP (docs/38) |

Phase 2 keeps the same last octets in `10.0.10.0/24`.

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
  blackbox probes for `192.168.0.1`, `10.0.1.2` and `10.0.1.3` feed the
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
  `:443` (via the `unifi-self-signed` ServersTransport). **Resolved** — it
  cleared itself when the UCG took over the gateway address, as predicted. No
  repo change was involved.

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

  `192.168.0.1` (the gateway) is green and has been since cutover.

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
     username=terraform url=https://192.168.0.1 password=<local-admin-password> \
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

   `url` is the **production** address (`https://192.168.0.1`). While the
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

# Or the same fact from the API, with the key already in the vault:
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
   console**, never over SSH:

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
      `192.168.0.0/24,10.0.20.0/24,10.0.30.0/24` (add `10.0.10.0/24` at Phase 2).
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
| 2 | Stranded wired leases | Controller client list after step 6 | Every wired device on the Connection A run has a `10.0.20.x` address — no `192.168.0.x` client outside VLAN 10. **Gated:** while port 7 is native Homelab this run is legitimately on VLAN 10, so the row only becomes meaningful after the Connection A finale |
| 3 | Resolver reach from every VLAN | `dig @192.168.0.150 git.esweiss.com` from home/iot/guest/work | Answer on all four |
| 4 | Guest containment | From guest: `curl -m5 https://git.esweiss.com`, ping another guest client | Both **fail** (DNS resolves, everything else denied; L2 isolation blocks the peer) |
| 5 | IoT containment | From an IoT device: reach anything on Home or `:443` on homelab | **Fails**; only `:53`, Plex `:32400` and HA `:8123` succeed |
| 6 | Work containment | Same from Work | Only `:53` succeeds |
| 7 | Gateway console fenced | From guest/iot/work: `curl -m5 -k https://<that VLAN's .1>` and `ssh <that VLAN's .1>` | Both **fail** (BLOCK rows 12-14). `ping <that VLAN's .1>` still works — icmp is deliberately left up |
| 8 | External DNS fenced | From guest/iot/work: `dig @8.8.8.8 example.com`, `dig +tls @8.8.8.8 example.com` | Both **fail/time out** (BLOCK rows 15-17); `dig @192.168.0.150` still answers |
| 8b | Gateway resolver fenced | From guest/iot/work: `dig @<that VLAN's .1> example.com` | **Fails/times out** (BLOCK rows 18-20). Confirm on the bench *before* cutover that the gateway answers this at all with the BLOCKs removed — the rows exist because a UniFi OS gateway normally does, and a bench `dig` is how that is established rather than assumed |
| 9a | Casting — the half that works | Cast a YouTube or Plex stream from a Home phone to an IoT TV/speaker | Device is discovered (site-level mDNS reflection) and plays |
| 9b | Casting — the half that does not | Screen-mirror / cast a local photo from the same phone; AirPlay to two speakers at once | **Fails, by design** — the receiver would have to open a connection back to Home, and AirPlay 2 needs PTP multicast that does not route |
| 10 | Plex local stream | Play from a TV — the Vizio pair and the Amazon units are reserved onto IoT, though the wired one only lands there if MAC-based assignment takes (§ DHCP reservations); check the client list for which VLAN it is actually on, then test | Direct play from `192.168.0.152:32400`, no transcode-over-WAN, from **either** VLAN — `iot-to-homelab-plex` and `home → homelab` both allow it. If it transcodes, check Plex's LAN Networks setting (cutover step 11) before suspecting the network |
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

Phase 2 moves the homelab from `192.168.0.0/24` to `10.0.10.0/24`, preserving
every last octet (hosts `.102`-`.107`, guests `.150`-`.160`, k3s
`.202`-`.207`/`.222`/`.223`/`.227`, VIPs `.99`/`.100`/`.101`/`.161`). It is a
separate MR raised only after Phase 1 has been validated in production, because
it touches inventory, DNS (~50 rewrites plus the PTR rules), the k3s VIP,
NFS export matrices, ~15 NetworkPolicy `ipBlock` sets, observability literals,
the Tailscale policy and this repo's own `terraform/unifi` site data at once.

The full supervised ordering — UniFi subnet flip, Proxmox hosts one at a time
with the corosync re-IP procedure, guests, k3s agents drain-and-recreate, k3s
servers one at a time with etcd member replacement, MetalLB/kube-vip at the
coordinated moment, NFS remounts after DNS — is drafted on the
`feat/homelab-renumber` branch as a new section of this document. Read it there
when that MR opens; every step gets live re-verification before execution.

One requirement on that MR belongs here, because it is the trap this repo has
already hit once: the straggler sweep for `192.168.0.` must match the
**escaped-regex spelling** as well as the plain one. Prometheus rule
expressions carry addresses as `192\\.168\\.0\\.` (two literal backslashes per
dot in the YAML), so a `grep -rIn "192\.168\.0\."` finds every easy occurrence
and none of the hard ones.

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
