# UniFi Network

The gateway/switch/AP tier — UniFi Cloud Gateway Fiber (UCG-Fiber),
USW-Pro-XG-8-PoE and U7 Pro XGS — carries the VLAN segmentation for the whole
house. This page is the current-state reference for it.

Everything the `ubiquiti-community/unifi` provider supports is codified in
[`terraform/unifi/`](../terraform/unifi/), a thin caller of the weisssrv-lib
`unifi-network` module at a pinned `?ref=`, exactly like the other three
Terraform roots. Everything the provider cannot express is a **console
change**. The split is the contract: § Codified vs manual says who owns what,
and a UI change to something in the codified column is drift that the next plan
will try to revert.

**Addressing.** The homelab moved from `192.168.0.0/24` to `10.0.10.0/24` on
2026-08-25/26, preserving every last octet. Every address on this page is the
current one. How the tier was brought up, how the renumber was run, and the
2026-08 configuration audit whose `ZBF-xx` / `PORT-xx` / `ADM-xx` finding IDs
are cited below are all in
[docs/48-unifi-audit-and-migration.md](48-unifi-audit-and-migration.md).

---

## Ground rules

Same posture as `terraform/tailscale` and `terraform/authentik`:

- **Apply is a supervised manual step.** `task terraform:unifi-apply` refuses
  `-auto-approve` and CI never applies. A wrong VLAN or zone-policy field can
  cut the LAN off from its gateway — including the machine you are typing on.
- **Plan is always safe.** `task terraform:unifi-plan` is read-only against the
  controller API and the GitLab-hosted state (`terraform/state/unifi`); the
  `unifi-drift-plan` CI job re-plans post-merge and on the schedule. It passes
  only on exit 2, so a yellow badge is drift and a broken plan — auth failure,
  unreachable gateway, state lock — fails red.
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

Findings cited below as `ZBF-xx` / `PORT-xx` / `ADM-xx` / `GW-xx` come from the
2026-08 UniFi configuration audit — the table of what each one said and whether
it is closed is
[docs/48](48-unifi-audit-and-migration.md) § 2026-08 configuration audit.

### Physical port map

Port numbers are the controller's, and this table is the live cabling.

UCG-Fiber (every port role is software-assigned):

| Port | Role |
|---|---|
| 1 (2.5G) | Hue bridge — access, native VLAN 30 |
| 2 (2.5G) | pve-laptop-01 — access, native VLAN 10 |
| 3 (2.5G) | pve-prec-01 — access, native VLAN 10 |
| 4 (2.5G, PoE+) | spare |
| 5 (10G RJ45) | **WAN** — symmetric gigabit ethernet handoff |
| 6 (SFP+ 1) | **Trunk to the switch** (DAC): native VLAN 1, tagged 10/20/30/40/50 |
| 7 (SFP+ 2) | spare — but pre-bound to the **WAN2** group (Internet 2, failover-only, currently disabled and empty). This is "spare WAN", not a spare LAN drop: anything plugged in is treated as an internet uplink and becomes a failover candidate, not an inert port |

USW-Pro-XG-8-PoE (155 W PoE budget; the AP draws ≤29 W). **PoE is disabled on
every port except 8** — nothing else on this switch is powered over ethernet,
and an enabled port that feeds a non-PoE device is a fault waiting for a
mis-patch:

| Port | Role |
|---|---|
| 1-2 | pve-opt-01 `nic0`/`nic1` — active-backup bond, **no LACP**, both access native VLAN 10 |
| 3-4 | pve-opt-02 — same |
| 5-6 | pve-opt-03 — same |
| 7 | **Connection A** — native VLAN 20 (Home), tagged VLAN 10 (Homelab) + VLAN 30 (IoT), detailed below |
| 8 | U7 Pro XGS (PoE++) — trunk, native VLAN 1, **forward: all** (the profile passes every VLAN, incl. tagged Homelab; the AP broadcasts home/iot/guest/work on 20/30/40/50) |
| 9 (SFP+ 1) | spare |
| 10 (SFP+ 2) | Uplink from the UCG — trunk all, native VLAN 1 |

The access ports (UCG 1-3, USW 1-6) carry the controller's default **All**
port profile — the listed native VLAN, but forwarding every VLAN — rather than a
strict access profile. The attached devices send only untagged frames so they
behave as access ports; the "access" label above is the intent, and the live
profile is trunk-all. **This is an open segmentation gap** (audit
PORT-01/ZBF-06): the All profile means a compromised device on one of these
ports could VLAN-hop by emitting tagged frames, reaching a VLAN the zone
policies would otherwise fence it out of. On the host ports (UCG 2-3, USW 1-6)
the blast radius is bounded — Proxmox hosts and the NAS are already fully
trusted on VLAN 10, so a compromise there is game-over on the homelab plane
regardless — but **UCG 1 carries the Hue bridge, an untrusted IoT appliance**,
which is the case that actually motivates this, and the ConnA run (port 7) fans
out to more untrusted devices still. Defence-in-depth wants native-VLAN-only
profiles wherever a port is a genuine access port. It stays a **console**
change, not codified: the audit established that `unifi_device.port_override` is
unsafe at provider 0.55.0 (#438 wipes live overrides on an empty set,
#430/#431). The genuine trunks that must stay
All are USW **7** (ConnA, native Home + tagged 10/30), **8** (AP) and **10** (the
SFP+ uplink — its other end is UCG **6**, the DAC to the switch); tightening the
pure access ports (UCG 1-3, USW 1-6, which is where pve-opt-03 lands on USW 5-6)
to native-only is tracked in [docs/16](16-next-steps.md).

"Connection A" is the run to the dumb 10G TP-Link switch, which fans out to
pve-nas-01, a 1G dumb switch (laptop dock, HDHomeRun), the bedroom Hyperion Pi,
and the MoCA leg feeding the living-room devices.

**Its port is native Home (20), tagged Homelab (10) and IoT (30).** pve-nas-01
rides a tagged `nic1.10` sub-interface while everything else on the run stays
untagged on Home; the bedroom Hyperion Pi self-tags `eth0.30` for its wired-IoT
leg, which is why VLAN 30 is tagged too.

#### Accepted trust decisions on the Connection A run

These describe the live design, with port 7 native Home.

The far end of port 7 is unmanaged, so **anything plugged in there that does
not tag its own frames lands on Home (VLAN 20)** — and Home reaches the homelab
in full (policy 1). That is the settled design, not an oversight, but it has
consequences worth naming so nobody re-derives them at 2 a.m.:

- **Wired TVs, streamers and game consoles land on Home by default**, and keep
  full homelab reach there — an accepted residual. This was written as
  unavoidable, on the reasoning that an SSID is the only thing that decides a
  VLAN. A reservation steers by MAC (§ DHCP reservations), so two wired devices
  are reserved onto IoT instead. What is genuinely unavoidable is only the
  fallback: where MAC-based assignment does not reach a device behind an
  unmanaged switch, the fix is a managed switch at the far end of Connection A
  (tracked in [docs/16](16-next-steps.md)).
- **The work laptop's containment depends on how it is attached.** On the
  `DunderMiffLAN` SSID it is on VLAN 50 and gets DNS and nothing else; in the dock
  (1G dumb switch off Connection A) it is untagged Home and inherits
  `home → homelab any`. Work is therefore "the work laptop when mobile", which
  is the case the VLAN was created for. Validation row 6 tests the wireless
  path deliberately.
- **The laptop dock and the HDHomeRun are Home devices** and are reserved as
  such (§ DHCP reservations). Of the two IoT candidates on this run, the
  bedroom Hyperion Pi holds its IoT reservation by self-tagging `eth0.30`
  (tag-aware, so the override's tagged delivery is exactly right), while the
  wired Vizio proved the failure mode — steered onto IoT it sat on APIPA,
  because a TV cannot decode tagged frames — and is reserved back onto
  **Home** until the Flex Mini plan (docs/16) gives that drop a managed port.
- **Any Home or homelab device can reach the gateway console's login page** on
  `:443` — the `*-to-gateway-mgmt` BLOCKs deliberately cover only
  guest/IoT/work, and the `{home,homelab}-to-gateway-extras` BLOCKs fence
  every *other* TCP port (the complement of 443, so a future listener is
  covered too) while leaving `:443` open. Fencing `:443` for Home *except* the
  `/29` admin block would need an ALLOW-before-BLOCK pair on one zone-pair, i.e.
  rule
  ordering, which this design refuses to depend on (the provider cannot manage
  it) — and blocking all of Home would cut the admin station's break-glass
  path to the console when Traefik (the `/29`-restricted `router.esweiss.com`
  route) is down; homelab needs it for the CI drift plan and that same
  Traefik backend. The console's own authentication plus the `terraform`-scoped
  API key are the gate; the residual is a login page, not access. One
  consequence of blocking `:80`: type `https://` explicitly — the plain-HTTP
  redirect nicety is gone.
- **The `/29` admin block is a DHCP-reservation boundary, not an
  authenticated one.** A Home device that statically claims `10.0.20.10`
  inherits the block's L3 trust (`admin_lan`, `lan-tailscale-strict`). Every
  admin surface behind it still authenticates (SSH keys, Proxmox/AdGuard/
  Connect logins), so this narrows exposure rather than granting access — but
  a real identity boundary needs a dedicated admin SSID/VLAN, tracked in
  [docs/16](16-next-steps.md) § UniFi network follow-ups. The reserved MACs in
  § DHCP reservations, the admin station's included, are public on the GitHub
  mirror by design: the reservation is placement, not authentication, and it is
  recorded here so nobody mistakes it for a secret.

Bonding note: ports 1-6 stay **plain access ports**. The opt nodes run
`active-backup`, not LACP, and `bond-all_slaves_active 0` is codified in
`nic_tuning` ([docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md)) — a
managed switch changes the link partner, so that doc's procedure is re-verified
against it (docs/16).

### Networks

Subnets are written in the provider's **gateway form** (the host part of
`subnet` is the gateway address).

| Key | Name | VLAN | Subnet | DHCP pool | Notes |
|---|---|---|---|---|---|
| `default` | Default | 1 (built-in) | `10.0.1.1/24` | `.100`-`.199` | Management: gateway, switch, AP. Imported, not created (`name=Default`). DHCP DNS is `1.1.1.1`/`9.9.9.9`, not the resolvers |
| `homelab` | Homelab | 10 | `10.0.10.1/24` | `.2`-`.98` | Hosts, guests, k3s, VIPs |
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
is an open door no plan shows. The gateway backs this up: both WANs carry
`wan_type_v6 = "disabled"`, so no upstream delegation arrives. That is what
makes the IPv4-only zone policy set safe. All 25 codified policies (12 ALLOW +
13 BLOCK) are IPv4-only, so a future `wan_type_v6` change must land the v6 half
of every one of them in the same MR, or every BLOCK is silently voided over
IPv6. Validation row 22 checks that clients really come up v4-only.

The homelab pool deliberately stops at `.98`. Everything from `.99` up is a
reserved VIP: `.99` wg-easy ([docs/38-wireguard-vpn.md](38-wireguard-vpn.md)),
`.100`/`.101` the MetalLB public and internal pools, `.161` the kube-vip API
VIP, `.162` the LAN-only `alloy-syslog` VIP (§ Day-2). The authoritative list is
`kubernetes/infrastructure/sources/cluster-config.yaml`; the pool bound here is
what keeps DHCP out of that range.

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
setting that never converges. Per-network guarding
(`dhcpguard_enabled`) is **off** on all six networks; switch-side DHCP snooping
(`global_switch.dhcp_snoop`) is on and is the rogue-DHCP protection actually in
place. Turning per-network guarding on with the gateway as the only allowed
server is an open decision (§ Codified vs manual, docs/16).

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

**Twelve `ALLOW` entries** — the complete list of what crosses a VLAN boundary
(`create_allow_respond = true`, so stateful returns are created automatically,
except where noted):

| # | From → To | Scope | Why |
|---|---|---|---|
| 1 | home → homelab | any | Status-quo trust; per-port enforcement stays at the Proxmox firewall + NetworkPolicies |
| 2 | home → iot | any | Casting and device control (AirPlay/Chromecast data path) |
| 3 | homelab → iot | any, from `.154` only | Home Assistant is the IoT controller — and the *only* VLAN-10 consumer of IoT, so the source is pinned: a zone-wide allow would be inherited by every k3s pod (they SNAT to node addresses), handing internet-facing workloads the unauthenticated device APIs (audit ZBF-04) |
| 4 | homelab → home | any, from `.154`/`.152` only | Plex → HDHomeRun, HA → TVs. Source-pinned for the same reason as row 3 |
| 5 | iot → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers |
| 6 | iot → homelab | tcp `:32400` → `.152` | Local Plex streaming from TVs |
| 7 | iot → homelab | tcp `:8123` → `.154` | HA's device-*initiated* paths: a Cast speaker fetching the TTS URL HA handed it, and integration webhooks posting back to `internal_url`. Policy 3 covers only what HA initiates |
| 8 | work → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers only |
| 9 | guest → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers only; everything else is internet-only |
| 10 | homelab → Internal | icmp → `10.0.1.2`/`.3` | The blackbox switch/AP probes: they run in a pod, so their echo requests arrive from VLAN 10 |
| 11 | Internal → homelab | icmp from `10.0.1.2`/`.3` | The echo *replies*. `create_allow_respond` is rejected for icmp, so the return direction is its own policy |
| 12 | homelab → homelab | tcp `80,443` → `.100` | Hairpin NAT: a homelab source dialing the WAN address is DNAT'd back into its own zone and hits the intra-zone Block All, which is how in-cluster probes of grey-cloud names fail. Intra-VLAN traffic never traverses the gateway, so only hairpinned flows can match (audit ZBF-01). This row is the whole fix: a hairpin from a homelab host returns 200/302 with it in place (validation row 8c), so same-subnet SNAT is not a blocker here. The AdGuard cross-domain rewrites (docs/08) remain the primary, WAN-round-trip-free mechanism; this is the backstop. Validation record: docs/48 |

**Thirteen `BLOCK` entries** — narrowing the two default-allow paths:

| # | From → To | Scope | Why |
|---|---|---|---|
| 13-15 | {guest,iot,work} → Gateway | **all tcp**, logged | On a Cloud Gateway the console is a gateway service on *every* VLAN's own gateway address, so without these a guest with the WLAN PSK gets a login form at `https://10.0.40.1`. All of tcp rather than a port list: the 2026-08 audit found five listeners (`8080,8443,8843,8880,6789`) beyond the original `22,80,443`, and nothing on these VLANs has any legitimate TCP need to its gateway. DHCP is broadcast before the client has an address and is unaffected; ICMP stays up for troubleshooting |
| 16-19 | {guest,iot,work,home} → External | tcp/udp `53,853`, logged | DHCP option 6 is a suggestion: Chromecast hardware queries `8.8.8.8` regardless and most TVs ship a vendor resolver, so `:53`/`:853` outbound is fenced. Home joined the set in the 2026-08 audit (ZBF-03) — it was silently exempt, losing split-horizon to any device that hard-codes a resolver. Homelab stays exempt — Unbound itself has to reach the internet |
| 20-23 | {guest,iot,work,home} → Gateway | tcp/udp `53,853`, logged | The other way off the resolvers: a UniFi OS gateway answers DNS on *every* VLAN's own `.1` and forwards to the WAN DNS servers (`1.1.1.1`/`9.9.9.9`, § Site settings), i.e. straight past AdGuard. Rows 16-19 and these together are what make "the weisssrv resolvers or nothing" true on all four client VLANs. DHCP (udp `67`/`68`) is untouched |
| 24-25 | {home,homelab} → Gateway | **tcp `1-442,444-65535`** (all tcp except 443), logged | The trusted-VLAN half: exactly `:443` stays open (console UI from admin devices, the CI drift plan, the `router.esweiss.com` Traefik backend); all other tcp is blocked as the complement of 443, so a listener a future firmware opens is fenced without a rule edit. DHCP/NTP are udp, homelab resolves via `.150`/`.160`, and `:22` has no listener |

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
- **Homelab workloads keep `:443` to the console** (rows 24-25 close everything
  else). The CI drift plan and the `router.esweiss.com` backend need it, and an
  allow-except-admin split would reintroduce the ordering dependency above. The
  bound on that is narrower than it looks but wider than "limited": the API key
  is minted under the local `terraform` admin rather than the ui.com Owner, so
  it carries no cloud path and no reach into other UniFi applications — but
  within the Network application it is unrestricted. `:443` reachability
  therefore equals full control of VLANs, zones, policies and WLANs for
  anything holding the key. That is the residual, accepted as such (audit
  ADM-07); see § Console admin accounts.

The resolver, Plex, Home Assistant and Traefik-VIP addresses and the two
management-device addresses are `locals` in the root (`dns_ips`, `plex_ip`,
`ha_ip`, `traefik_public_vip`, `mgmt_device_ips`) — the single edit point any
homelab re-address uses.

**Policy ordering is not codifiable.** `unifi_firewall_policy.index` is
read-only and new policies append to the end of their zone-pair (upstream
#407). The set above is order-independent by construction, on three grounds:
the `ALLOW`s are allowances against a default deny rather than entries in a
first-match list (custom policies always precede the predefined defaults, so
row 12's allow beats the intra-zone Block All regardless of its index); where
`BLOCK`s share a zone-pair they are all blocks, so their relative order is
irrelevant; and the one zone-pair carrying both an allow and blocks
(home → Gateway: the reflector's auto-generated mDNS allow, rows 20-23's DNS
block and row 24's tcp block) is disjoint by port and protocol (udp `5353`
against `53,853` and a tcp port list). If that ever stops being true — a
custom `ALLOW` and `BLOCK` overlapping on the same zone-pair — the ordering
becomes a UI step and this page must record it.

### Wireless

All four SSIDs are `wpapsk` on the U7 Pro XGS. Each names its radio set
explicitly with `bands` (lib v0.14.0): the two WPA3 SSIDs — TheRevengers and
DunderMiffLAN — carry `["2g","5g","6g"]`; Panopticon and the guest SSID are
`["2g","5g"]`.

| SSID | Network | WPA3 | Bands | Extras |
|---|---|---|---|---|
| TheRevengers | home | support + transition, PMF optional | 2/5/6 GHz | Same PSK as today — devices roam over without re-onboarding |
| Panopticon | iot | off (plain WPA2, PMF disabled) | 2/5 GHz | `allow_2ghz_high_perf = true` (the module inverts it to the provider's `no2ghz_oui = false`) — ESP32/Kasa-friendly 2.4 GHz. No 6 GHz radio (WPA3-only band) |
| kugel-tikka-masala | guest | support + transition, PMF optional | 2/5 GHz | `l2_isolation = true` |
| DunderMiffLAN | work | support + transition, PMF optional | 2/5/6 GHz | |

**6 GHz is live and Terraform-owned** since lib v0.14.0 added the per-SSID
`bands` input — the two WPA3 SSIDs carry `6g`, Panopticon and guest do not.
Each set is pinned to the live radios rather than left null: `wlan_bands` is
Optional+Computed, so a null on an existing WLAN reconciles to the provider's
`2g`/`5g` default and would strip the 6 GHz radio. Pinning means config equals
the console value, so no write is issued and upstream #406 (which rejects `6g`
only at CREATE) never fires; the exception is a from-scratch CREATE, which must
drop `6g`, apply, then re-add it (terraform/unifi/main.tf). Do NOT set
`enhanced_iot`.

PSKs come from four 1Password items via `TF_VAR_wlan_passphrase_*`
(docs/15 "Required 1Password Items"). They are never committed to git, but the
provider **does** persist every `passphrase` in Terraform state. The
GitLab-hosted HTTP state backend is therefore secret material: anyone who can
read it can read every WLAN key. `passphrase_wo` (write-only) is not usable at
provider 0.55.0 — it ships no matching version trigger, so a rotated PSK would
plan no diff. `user_group_id` resolves from
`data.unifi_client_qos_rate` name `"Default"` — verify that name on the
controller at first plan (upstream examples omit it, so there is no
authoritative default).

**The site guest-control policy is configured but inert — do not count it as a
guest layer.** Live `guest_access` has `portal_enabled: true` and the RFC1918
restricted-subnet list populated, but no WLAN carries `is_guest` (all four are
`false`, forced by `purpose = corporate`, see § Networks), so nothing engages
it. Guest containment rests entirely on the custom-zone default-deny plus the
guest WLAN's `l2_isolation` — both in place and verified. Do **not** set
`is_guest = true` to "activate" it: with `portal_enabled: true` that engages the
captive-portal authorization flow and breaks headless guest clients, for a layer
the zone policies already provide.

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

The MAC column is published with this repo, which is an **accepted residual**:
a client MAC is observable to anyone already on the segment, and the table is
what makes the steering auditable. Re-rolling the MacBook's per-network private
address is a cheap optional tidy-up if that ever stops being acceptable.

**A per-MAC override always delivers the VLAN TAGGED.** A client that expects an
untagged port black-holes; a managed port at the drop is the fix
(`terraform/unifi/README.md` § Client reservations).

**A reservation is also VLAN steering, and that makes this table the standing
mechanism for putting a device on the right network.** A wireless client lands
on its reservation's network whichever SSID it associates with, so moving one is
"add an entry naming the target network" — it takes effect on the device's next
association, with no SSID re-join and nothing to configure on the device itself.
That is how the WLED controllers and the Kasa plugs reached IoT without ever
joining `Panopticon`, and it is the immediate fix for every IoT-class device
that came up on Home: the Levoit pair, the TVs and the Echoes.

**Steering is placement, not authorization** — the reservation matches a
client-reported MAC, and a device still holding the `TheRevengers` PSK falls
back to Home the moment its MAC stops matching (randomized, spoofed after a
compromise, or replaced hardware). Re-onboarding onto `Panopticon` is therefore
the step that actually removes the Home credential from IoT-class devices, and
it stays a required follow-up
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
is wired to the Connection A run and sits on IoT; whether the
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

All three reservations — the switch, the AP and the MacBook — are filled in.
The device MACs were read at adoption; the MacBook's once it associated with the
Home SSID, which is what makes the `10.0.20.8/29` admin block in the Proxmox
firewall mean anything.

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
Ansible. Per-network DHCP guarding is off there (as everywhere) — switch-side
DHCP snooping is the protection in place, per the note under § Networks.

### Port forwards

| Name | WAN | → | Notes |
|---|---|---|---|
| http | tcp `80` | `10.0.10.100:80` | Public MetalLB VIP |
| https | tcp `443` | `10.0.10.100:443` | Public MetalLB VIP |
| plex | tcp `32400` | `10.0.10.152:32400` | docs/20 |
| gitlab-ssh | tcp `2222` | `10.0.10.153:2222` | Forwarded 2222→2222; the guest's own iptables PREROUTING rule redirects 2222→22, where sshd actually listens (docs/27 § Git SSH) |
| wg | **udp** `51820` | `10.0.10.99:51820` | wg-easy VIP — UDP, not TCP (docs/38) |

Per-forward hit logging is **on** for plex, gitlab-ssh and wg (`logging = true`
in `terraform/unifi/networks.tf` `local.port_forwards`) and deliberately **off**
for 80/443, where Traefik's own access log already records every connection with
the Host and the gateway lines would only duplicate it. Changing either needs a
supervised apply.

Only Plex rides a `local` (`plex_ip`); the other four targets are literals in
`networks.tf` — the two MetalLB VIP forwards (`http`/`https`), the GitLab
guest (`gitlab-ssh`), and the wg-easy VIP (`wg`). A renumber has to visit all
five edit points individually — there is no single subnet variable that moves
them.

### Site settings

| Setting | Value | Why |
|---|---|---|
| `mgmt.auto_upgrade` | `true` | **Deliberate.** The switch and AP take firmware nightly at 1 AM, which is hands-off patching for the Wi-Fi gear. Covers **device** firmware only; the console's own UniFi OS / application updates are a separate console-owned surface upgraded in a chosen window (§ Day-2). Ruling record: docs/48 |
| `network_optimization.enabled` | `false` | Auto-optimize rewrites exactly the settings this repo codifies |
| `usg.upnp_enabled` / `upnp_nat_pmp_enabled` | `false` | Port forwards are declared, never negotiated |
| `igmp_snooping_networks` | `["home", "iot"]` | The two ends of the casting path. Homelab is deliberately out: snooping without a reliably elected querier prunes groups after the membership timeout, and VLAN 10 has nothing multicast-critical to gain (corosync is unicast knet) |
| `ips.ips_mode` | `"ips"` | **Create-time intent only.** The module ignores the whole `ips` block (`ignore_changes = [ips]`), so editing this value produces no plan diff and no live change. The live posture is **inline blocking (prevention)**, set in the console (Settings → CyberSecure); the value is pinned here so a recreate or an `ignore_changes` removal cannot silently revert to detection — § Day-2 |

WAN DNS on the gateway is set to `1.1.1.1` + `9.9.9.9` plaintext in the UI, a
**deliberate divergence** from the DoT-to-internal-resolver arrangement the
ASUS ran: pointing the gateway at `.150`/`.160` creates a bootstrap loop
(resolvers are LXCs behind the gateway). LAN clients are unaffected — they get
the weisssrv resolvers by DHCP.

---

## Codified vs manual

| Area | Owner | Note |
|---|---|---|
| Networks / VLANs / DHCP pools | **Terraform** | `default` is imported, the rest created |
| Firewall zones + policies | **Terraform** | Ordering is UI-only (#407) |
| WLANs (SSID, PSK, bands, WPA3, isolation) | **Terraform** | 6 GHz per-SSID via `bands` (lib v0.14.0) |
| Client fixed IPs | **Terraform** | Updates need `-replace` (#428) |
| Port forwards | **Terraform** | |
| Site settings (auto-upgrade, optimization, UPnP, IGMP) | **Terraform** | § Site settings |
| Device adoption (switch, AP) | UI | The provider cannot create devices |
| Per-port native/tagged VLAN assignment | UI | Unsafe in Terraform — see below |
| mDNS reflection | UI | Site-level on Network 10.x — see below |
| Firewall-policy ordering | UI | Read-only in the provider (#407) |
| WAN DNS servers | UI | § Site settings |
| IPS mode | UI (Settings → CyberSecure) | The module ignores the `ips` block, so the mode is console-owned. Live posture: inline blocking (§ Day-2) |
| Gateway SYN-flood protection (`usg.syn_cookies`) | UI | Off. The module leaves the attribute unset and the provider round-trips it, so it is console-owned. Enable-or-accept is an open decision (docs/16) |
| Default Security Posture | UI | `ALLOW_ALL` — see below |
| DHCP guarding (per-network) | UI | Off on all six (`dhcpguard_enabled: false`); `dhcp_guarding.servers` is dropped on write for corporate/guest networks (#419). Switch-side DHCP snooping (`global_switch.dhcp_snoop: true`) is the rogue-DHCP protection in place; per-network guarding is an open decision (docs/16) |
| Local DNS records (gateway static-DNS) | UI | Empty; no provider resource (§ Day-2, docs/16) |
| ui.com Remote Access (cloud) | UI | On, deliberately — see below |
| Console admin accounts | UI | Three accounts — see below |
| TLS verification on every plan | **Accepted risk** | `unifi_allow_insecure = true` (`terraform/unifi/variables.tf`, whose heredoc carries the reasoning). Every plan sends the API key, and a WLAN apply sends the four PSKs, over a TLS session whose certificate is not verified — the scheduled `unifi-drift-plan` job included. The threat model is an attacker already on VLAN 10 with ARP-spoof capability. Closing it means a real certificate on the console plus an internal name for it, and a ~60-day renewal dependency that breaks every plan when it lapses. The variable is the seam |
| Device SSH | UI | Disabled; no automation depends on it |
| WAN event reporting / gateway DDNS | UI | `report_wan_event` off on both WANs (open item, docs/16). Gateway DDNS is deliberately unused: the four public A records are owned by the in-cluster `cloudflare-ddns` CronJob and alerted by `DDNSStale`, so arming it would fight that |

### Default Security Posture — and why adding a VLAN is not a UI-safe operation

**A network created in the UI lands in the built-in `Internal` zone**, which is
Allow-All to Gateway, Vpn, Hotspot and Dmz. The deny-by-default this design
relies on comes from Terraform putting each VLAN in its own custom zone, not
from the controller. Add VLANs through `terraform/unifi/`, never through the
console.

The live posture value is `ALLOW_ALL`. Whether to keep it or flip to Block All
is an open decision (docs/16). It is tolerable today only because the sole
network left in `Internal` is the mgmt VLAN.

### Console admin accounts

Three accounts:

- **Owner** — `ericsweiss1@gmail.com`, ui.com SSO, cloud access. The only
  account with a cloud path.
- **`terraform`** — local, no 2FA. The API key used by `terraform/unifi/` and by
  the `unifi-drift-plan` CI job is minted under it.
- **`homeassistant`** — local, no 2FA, consumed by the Home Assistant UniFi
  Network integration for its write actions (client block, PoE, WLAN toggle).
  Kept **deliberately not vaulted**: the credential lives only in HA's own
  store.

Both local accounts hold full rights **within the Network application**. Say the
blast radius plainly: a compromise of Home Assistant is a compromise of the
whole UniFi network configuration. The operator accepted that knowingly, judging
the rotation burden not worth it for this account; the standing mitigations are
HA's own perimeter (Authentik SSO, no external ingress) and the option to
disable the account in seconds. Narrowing both to a scoped role and vaulting the
HA one are the obvious hardening steps (docs/16).

One thing to confirm in the console rather than from this page: whether either
local account is still a **Super Admin** or has been narrowed to a Limited Admin
role. The API reports `is_super` for the key's own admin and nothing finer, so
the role assignment is a console fact. Local accounts carry no 2FA (docs/15).

### Remote access and the ui.com Owner account

Remote Access is **on, deliberately**: the intended remote-access path is a
Teleport VPN via a Ubiquiti travel router, so the console is reachable over
`id.ui.direct` — an outbound tunnel the zone firewall cannot see. Its only gate
is the ui.com Owner account's login.

**Whether that account carries strong MFA is not verified** (docs/16 open item;
the API cannot read it). Until it is confirmed with a non-SMS factor plus stored
recovery codes, treat this as an internet-reachable console behind a password
alone. That verification is the real control here, not the tunnel toggle.
Tailscale (Proxmox hosts) and wg-easy (`.99`) remain the other out-of-band
paths.

### Per-port VLAN assignment and `port_override`

`unifi_device.port_override` is unsafe at provider 0.55.0: #438 wipes live
overrides when the set is empty, #430 strips fields, #431 fails on unset
Optional+Computed attributes. **Do not manage the switch with Terraform.** Port
assignments are a console change, recorded in § Physical port map.

### mDNS reflection

Provider is read-only for this on UniFi OS gateways. Network 10.x moved it from
a per-network toggle to a **site-level** setting: Settings → Networks →
Multicast DNS, with three modes — Auto (reflect across all networks), Off, and
Custom (pick the services and the networks each is reflected between). It is set
to reflect between homelab, home and iot (`mdns.enabled_for_network_ids` = Home,
IoT, Homelab; per-network `mdns_enabled` matches).

### UPnP off is a user-visible trade-off for the game consoles

With UPnP off and no console-specific forwards, consoles on Home report Strict /
Type 3 NAT, which degrades party chat and matchmaking. That is the right default
for a codified network — a forward nobody declared is a forward nobody reviews —
and the remedy when someone complains is a per-console `unifi_port_forward`
entry in `local.port_forwards`, not re-enabling UPnP.

### What the drift plan does not cover

**"Drift plan is green" is not "the controller matches the repo."** The table
above is the *manageable* surface; Terraform neither writes nor watches most of
the console. The rest of `rest/setting` was read during the 2026-08 audit
(docs/48) and the following are set as intended: UPnP/NAT-PMP off,
`broadcast_ping` off, ICMP redirects off both ways, DoH off, SSL inspection
off, DPI on, netflow off, and no scheduled reboot/upgrade task
(kured owns k3s reboots; `auto_upgrade` owns Wi-Fi-gear firmware). Two Ubiquiti
cloud data paths are on and **deliberate**: WiFiman (`wifiman_enabled`, a
separate path from Remote Access — it survives turning Remote Access off) and
console discoverability (`discoverable`, negligible on a console reachable only
from trusted VLANs). The switch `port_overrides` (port 7 is load-bearing for
Connection A and exists nowhere in git) and the `mgmt` SSH/cloud posture are the
highest-value unmanaged areas. A read-only `scripts/` checker against a
committed expectation — the `scripts/b2-bucket-drift.py` pattern — was
considered and **declined**: `rest/setting` returns the device-SSH password, its
hash and the site API token in cleartext to any API-key holder, so a checker
would put that material in CI logs and artefacts. The posture is
console-owned and accepted. For the same reason, never dump that endpoint into
a log or a report.

**The built-in zones are correctly fenced.** A custom zone
*does* remove its network from `Internal` on this controller (each VLAN network
sits in exactly one custom zone; `Internal` holds only the mgmt VLAN), which is
what makes the deny-by-default real — so the module's cross-zone precondition is
belt-and-braces on verified behaviour, not a hedge. The `Vpn`, `Hotspot` and
`Dmz` built-ins are empty. Record before anyone enables a **gateway-side** VPN
(Teleport/WireGuard/L2TP on the UCG): its clients arrive in `Vpn`, which by
default reaches the mgmt VLAN and the console but **none** of the five VLAN
zones — the opposite of what a remote-access VPN usually wants, and it will look
like a broken VPN rather than a firewall default. wg-easy is unaffected: it runs
in-cluster on the `.99` VIP behind the `wg` port forward, not on the gateway's
own VPN server. Site Magic / site-to-site is confirmed **not** configured.

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

## What this depends on in the rest of the repo

Four places in this repo assume the VLAN layout above; each is documented where
it lives.

- **Proxmox firewall** ([docs/11-firewall.md](11-firewall.md) § Client scopes):
  `admin_lan` covers true admin surfaces and the `10.0.20.8/29` admin-device
  block; `lan_clients` and `dns_clients` carry the service and resolver scopes.
  The two rule groups the collection renders itself follow via role variables:
  `proxmox_firewall_dns_client_sources` puts `sg-dns`'s `:53` on
  `dns_clients`, and `proxmox_firewall_k3s_ingress_int_sources` puts
  `sg-k3s-ingress-int` on `lan_clients`, so every VLAN can resolve and Home
  can reach the internal Traefik VIP. `ssh_authorized_keys` `from=`,
  `base_fail2ban_ignoreip` and `gitlab_ssh_allowed_users` mirror the admin
  split at the sshd layer.
- **Traefik internal allowlists** (both keys from
  `kubernetes/infrastructure/sources/cluster-config.yaml`): `lan-tailscale-only`
  carries `${cluster_home_cidr}` (`10.0.20.0/24`) so Home-VLAN devices reach the
  internal routes, which are all behind forward-auth anyway.
  `lan-tailscale-strict` carries the narrower `${cluster_home_admin_cidr}`
  (`10.0.20.8/29`) instead — it fronts the routes whose *only* gates are this
  list plus the backend's own login (1Password Connect, the router and AdGuard
  appliance UIs), and Traefik proxies from a k3s node that is itself inside
  `admin_lan`, so admitting the whole VLAN there would hand those UIs to every
  phone, TV and guest laptop that knows the house PSK. The /29 is exactly what
  `admin_lan`, the sshd `from=` restrictions and fail2ban's `ignoreip` carry, so
  the two layers say the same thing. No other client VLAN is allowlisted — the
  gateway's inter-zone deny is the first gate, the middleware the second.
- **`router.esweiss.com`** proxies the gateway's HTTPS-only UI on `:443`
  through the `unifi-self-signed` ServersTransport: the UCG serves a
  self-signed certificate the acme.sh wildcard cannot cover.
- **Observability** ([docs/31-observability.md](31-observability.md)): ICMP
  blackbox probes for `10.0.10.1`, `10.0.1.2` and `10.0.1.3` feed the
  `NetworkGearProbeFailed` alert (warning, 5m), with
  `NetworkGearProbeMissing` (warning, 15m) behind it as the drift guard for the
  pattern-matched target set. Those instances are excluded from the
  `EndpointDown` catch-all so one cause fires once.

---

## Validation

Re-runnable segmentation matrix. Run it after any change to the zone policies,
the port map or the VLAN layout, and after a gateway or switch replacement.
"Expected" is what a correct segmentation produces — several rows are *failures
by design*.

**The drift plan is clean.** A programmatic diff of `local.policies` against
the live custom policy set (action, protocol, logging, `create_allow_respond`,
both endpoints) matches all 25 codified policies (12 ALLOW + 13 BLOCK), and the
five port forwards match too. So the policy half of the configuration is
converged, and `unifi-drift-plan` green is a true statement about the manageable
surface; its limits are in § Codified vs manual. Verification record: docs/48.

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Per-VLAN DHCP | Join each SSID / plug into each access port | Address from the right pool, DNS `.150`/`.160`, domain `esweiss.com` (the **mgmt** VLAN is the exception: `1.1.1.1`/`9.9.9.9`) |
| 2 | Stranded wired leases | Controller client list | The two intended tagged clients on the Connection A run — pve-nas-01 (VLAN 10, via `nic1.10`) and the bedroom Hyperion Pi (VLAN 30, via `eth0.30`) — sit on their tagged VLANs by design. Every OTHER wired device on the run must show a `10.0.20.x` (Home) address: an untagged device on VLAN 10/30 would be a mis-assignment, not a self-tag |
| 3 | Resolver reach from every VLAN | `dig @10.0.10.150 git.esweiss.com` from home/iot/guest/work | Answer on all four |
| 4 | Guest containment | From guest: `curl -m5 https://git.esweiss.com`, ping another guest client | Both **fail** (DNS resolves, everything else denied; L2 isolation blocks the peer) |
| 5 | IoT containment | From an IoT device: reach anything on Home or `:443` on homelab | **Fails**; only `:53`, Plex `:32400` and HA `:8123` succeed |
| 6 | Work containment | Same from Work | Only `:53` succeeds |
| 7 | Gateway console fenced | From guest/iot/work: `curl -m5 -k https://<that VLAN's .1>` and `ssh <that VLAN's .1>` | Both **fail** (BLOCK rows 13-15). `ping <that VLAN's .1>` still works — icmp is deliberately left up |
| 7b | Gateway extras fenced on trusted VLANs | From home and a homelab host: `curl -m5 http://10.0.10.1/` and `nc -z -w3 10.0.10.1 8080` | Both **fail** (BLOCK rows 24-25) while `curl -k https://10.0.10.1` still answers — `:443` is the one listener the trusted VLANs keep |
| 8 | External DNS fenced | From guest/iot/work/home: `dig @8.8.8.8 example.com`, `dig +tls @8.8.8.8 example.com` | Both **fail/time out** (BLOCK rows 16-19); `dig @10.0.10.150` still answers |
| 8b | Gateway resolver fenced | From guest/iot/work/home: `dig @<that VLAN's .1> example.com` | **Fails/times out** (BLOCK rows 20-23). The rows exist because a UniFi OS gateway answers DNS on every VLAN's own `.1` by default |
| 8c | Hairpin from homelab | From a homelab host: `curl -sk -m5 --resolve photos.ericsweiss.com:443:<WAN IP> https://photos.ericsweiss.com/` | **PASSES**: `photos` returns 200 and `ide.git` 302 from `10.0.10.102`, in ~20 ms. The `homelab → homelab` intra-zone Block All is the entire obstacle, so same-subnet SNAT is **not** a blocker on this gateway and in-cluster probes of grey-cloud names work without a rewrite. The AdGuard cross-domain rewrites (docs/08) stay in place as the primary, WAN-round-trip-free path; ALLOW row 12 is the backstop that also covers the `ide.git`/`photos` EndpointDown probes. Validation record: docs/48 |
| 9a | Casting — the half that works | Cast a YouTube or Plex stream from a Home phone to an IoT TV/speaker | Device is discovered (site-level mDNS reflection) and plays |
| 9b | Casting — the half that does not | Screen-mirror / cast a local photo from the same phone; AirPlay to two speakers at once | **Fails, by design** — the receiver would have to open a connection back to Home, and AirPlay 2 needs PTP multicast that does not route |
| 10 | Plex local stream | Play from a TV — the Vizio pair and the Amazon units are reserved onto IoT, though the wired one only lands there if MAC-based assignment takes (§ DHCP reservations); check the client list for which VLAN it is actually on, then test | Direct play from `10.0.10.152:32400`, no transcode-over-WAN, from **either** VLAN — `iot-to-homelab-plex` and `home → homelab` both allow it. If it transcodes, check Plex's LAN Networks setting (docs/20) before suspecting the network |
| 11 | HDHomeRun | Live TV in Plex | Tuner reachable at `10.0.20.200` (configured by IP) |
| 12 | Internal ingress from Home | Browse `https://grafana.esweiss.com` from a Home laptop | 200 — the `cluster_home_cidr` allowlist entry |
| 13 | Appliance UIs from Home | Browse `https://router.esweiss.com` from a **non**-admin Home device, then from the admin MacBook | Non-admin **fails** (`lan-tailscale-strict` is the `10.0.20.8/29` block); admin succeeds |
| 14 | Admin surfaces from Home | SSH to a Proxmox host from a **non**-admin Home device | **Refused** — only `10.0.20.8/29` is in `admin_lan` |
| 15 | Port forwards | From off-net: `curl -I https://<public>`, Plex remote, `ssh -p 2222 git@git.ericsweiss.com` | All succeed |
| 16 | wg-easy | Connect a WireGuard client from cellular | Handshake completes, internet egress works (docs/38) |
| 17 | Tailscale | `tailscale status` on a host; reach a guest over the tailnet | Subnet route still advertised and approved (docs/05) |
| 18 | Network gear probes | Grafana / Prometheus | `NetworkGearProbeFailed` clear for all three targets. Needs ALLOW rows 10/11 and the two mgmt reservations applied, so a probe red on `.2`/`.3` means the reservations did not converge |
| 19 | Bond health | `cat /proc/net/bonding/bond0` on each opt node | `all_slaves_active 0`, one active leg (docs/34) |
| 20 | e1000e / AQC113 watch | Loki, over 48 h: `{job="journal"} \|= "Hardware Unit Hang"` | No hits (docs/34) |
| 21 | HA re-armed | `ha-manager status` | All four resources `started`, none `ignored` — a maintenance window that disarmed HA has to re-arm it |
| 22 | No IPv6 on clients | `ip -6 addr` on a client from each VLAN | Link-local `fe80::` only — no GUA, no ULA (§ Networks) |
| 23 | Drift plan | Next scheduled pipeline after an apply | `unifi-drift-plan` **green**; a yellow is real drift or a broken credential (§ Day-2) |
| 24 | Cluster health | `task flux:status`, `task infra:verify`, `task k3s:status` | Clean |

## Day-2 operations

- **Drift.** `unifi-drift-plan` (advisory) re-plans on merge and on schedule. A
  yellow job means the controller and the code disagree: either the change was
  an intended UI hot-fix (codify it, MR it, next plan is clean — do **not**
  apply first, apply would revert it) or nobody meant to change anything, which
  is an incident.
- **IPS is inline (prevention).** `ips_mode = "ips"`: the gateway **drops**
  matched traffic on all six VLANs rather than only alerting. Mode is a console
  action (Settings → CyberSecure) — the module ignores the `ips` block, so
  `main.tf` holds
  create-time intent only (§ Site settings). No plan sees that surface, so
  `scripts/unifi-settings-drift.py` reads
  `GET /proxy/network/api/s/default/get/setting/ips` on a schedule and fails on
  drift from `scripts/unifi-settings.json`, which pins the whole IPS section:
  `ips_mode`, the 34 enabled ET categories, the six enabled networks, and the
  `honeypot_enabled`, `restrict_torrents`, `memory_optimized`,
  `advanced_filtering_preference` and `endpoint_scanning` flags. Volatile keys
  (`_id`, `key`, `site_id`, `utm_token`, `last_alert_id`) are excluded, and
  lists compare order-insensitively. The networks are pinned by UniFi object
  id, so re-baseline the file if one is ever recreated. The enabled set is 34 of ~53 ET
  categories with no current-events category, and `memory_optimized` trims the
  loaded ruleset. The engine is still Suricata **6** and inverting the
  documented sequencing — upgrade the engine, then flip — was deliberate. The
  upgrade is blocked device-side with no repo-side lever: the gateway reports
  `EVT_GW_UpgradeSuricata status:INSUFFICIENT_MEMORY target_version:8` hourly at
  Info level, and `enabled_categories` are console-owned by module design.
  Re-baseline the category set once the engine lands (docs/16). One syslog line
  to not re-open: `ipset[ips] add failed ... it's already added, ignore` is the
  daemon's benign idempotent re-add on a repeat hit from an already-blocked
  source, not an alerting signal. **Standing risk:** an inline false
  positive drops packets across every VLAN, so the gateway is the first thing to
  check for unexplained per-VLAN drops. Visibility on a block is native site
  alerts plus the gateway syslog stream in Loki (both below). Upstream #381:
  `ips.suppression_alerts` is not persisted, so suppressions are a UI concern
  with a permanent diff if codified.
- **Firmware.** Device (switch + AP) auto-upgrade is **on** by choice — nightly
  at 1 AM (§ Site settings). The **console's** own UniFi OS / application
  updates are separate and operator-driven: upgrade in a chosen window, gateway
  last. `NetworkGearProbeFailed` fires during the reboot, which is why it is a
  warning rather than a page.
- **Gateway Local DNS records.** Empty today (`static-dns` returns `[]`), and the
  provider has no resource for them. Worth adding for the GitLab family (git /
  registry.git / pages.git .ericsweiss.com → `10.0.10.101`) as a second layer
  under the AdGuard split-horizon rewrites: a pod or host that falls back to the
  gateway resolver otherwise gets the public answer and reproduces the hairpin
  outage. Console change; tracked in docs/16.
- **Alerting is split, deliberately.** Site alerting (`mgmt.alert_enabled`) is
  **on**: the gateway's own device-down / WAN-failover / IDS notifications fire,
  which is the visibility net under inline IPS. Gateway reachability is covered
  independently by this repo's blackbox probes and `NetworkGearProbeFailed`.
  Gateway syslog reaches Loki via the `alloy-syslog` VIP (below), so console
  events are Grafana-queryable, and `UnifiSyslogStale` watches the feed itself —
  no line accepted in an hour, or the Alloy syslog component gone.
  `SyslogLogShippingStale` watches the other half: the receiver still accepting
  datagrams while its `loki.write` pushes nothing for 45 minutes. Content
  alerting on that stream is three Loki ruler rules in
  `kubernetes/infrastructure/observability/loki/unifi-syslog.yaml`:
  `UnifiIpsBlockFailed` means inline IPS matched traffic and then failed to write
  the block, so the detection was real but nothing was dropped.
  `UnifiIdsEngineFailure` means the IDS engine is logging `INSUFFICIENT_MEMORY`,
  so inspection is degraded while the gateway still forwards.
  `UnifiGatewayErrorBurst` is about 15x the baseline error rate; read the raw
  stream as `{job="unifi-syslog"}` in Grafana to see which subsystem is failing.
  Console-side events also reach ui.com cloud email/push — an
  out-of-band fallback that does not depend on the network it reports on.
- **Gateway syslog → Loki.** The gateway forwards syslog to the `alloy-syslog`
  MetalLB VIP `10.0.10.162:514/udp` (CyberSecure "Activity Logging" and
  Integrations "System Logging / SIEM", both pointed at that target). Two allows
  are load-bearing and easy to lose: the per-guest Proxmox security group
  `sg-syslog-vip` (`group_vars/all.yml`), assigned to every ingress agent in
  `hosts.yml` — a `cluster.fw [RULES]` entry does **not** work, it compiles into
  `PVEFW-HOST-IN` and never sees a guest-forwarded VIP frame — and the
  in-cluster allow
  `kubernetes/infrastructure/observability/alloy-syslog/networkpolicy.yaml`.
  The pod is ingress-scheduled but not node-pinned, so MetalLB can announce
  `.162` from any ingress agent and the group is opened on all of them (the same
  set that carries the wg-easy `.99:51820` rule). Confirm with
  `{job="unifi-syslog"}` in Loki. Diagnosis for a VIP that goes silent:
  [docs/12-runbooks.md](12-runbooks.md).
- **Adding a device to a VLAN** is DHCP — no repo change. Adding a *reservation*
  is a `unifi_client` entry (remember `-replace` for edits, #428).
- **Anything the switch does per-port** stays a UI change, recorded here.

### Client housekeeping

Per-device follow-ups live here rather than in docs/16, which carries only work
with an infrastructure consequence. A rename is an in-place `unifi_client`
change, so each one needs `-replace` (upstream #428, § DHCP reservations).

- **Give the TVs and Echoes friendly names.** Seven IoT reservations still carry
  the controller's reported hostname: `amazon-01f20c070`, `amazon-5b51cd6d9`,
  `amazon-a70f51c2d`, `amazon-a9c5657f8`, plus `amazon-f57e91` and
  `amazon-c7d8bc` (Amazon OUI, no hostname reported, presumed Echoes), and
  `vizio-wifi`. Two are worth resolving together: `vizio-wifi`
  (`A0:6A:44:50:EE:95`) is most likely the living-room Vizio soundbar, and
  `vizio-cast-display` (`3C:9B:D6:7A:36:A3`, wired on the Connection A/MoCA run)
  is believed to be the living-room TV. Confirm before renaming. Keep both
  reservations regardless — steering is per-MAC — and add entries for the
  remaining TVs and Fire TV sticks as they appear.
- **Confirm and reserve the two `ESP_*` devices.** `ESP_70688C`
  (`48:3F:DA:70:68:8C`) and `ESP_719BF2` (`48:3F:DA:71:9B:F2`) are on Home with
  pool leases, and are almost certainly the two Tuya smart-blinds drivers: every
  Levoit and WLED unit announces a real hostname, and no other Wi-Fi Espressif
  device exists in the apartment. Confirm by power-cycling one blind and watching
  which ESP drops, then add both as IoT reservations — a reservation is what
  moves a wireless device's VLAN (§ DHCP reservations). They stay out of
  `terraform/unifi/networks.tf` until confirmed: reserving a misidentified device
  onto a VLAN that denies it everything breaks something nobody can name.
- **Re-onboard the remaining IoT devices onto `Panopticon`.** Per-MAC steering
  places them on IoT today, but placement is not authorization: a device still
  holding the `TheRevengers` PSK falls back to Home if its MAC ever stops
  matching the reservation. Re-joining `Panopticon` removes the Home credential
  and the reservation keeps steering identically afterward. Remaining holders of
  the Home PSK: the Kasa plugs and anything not yet re-joined by hand.
- **Clear the pre-renumber `config_network` on the switch and AP (optional).**
  Both still record their old `192.168.0.x` in the Configure-IP field. Inert
  while DHCP, but it is the value either would take if flipped to static, on a
  subnet the gateway no longer routes. Clear it in the console.
- **USW Flex Mini for the Connection A drops (optional).** Two standing limits of
  the dumb TP-Link chain behind port 7: tag-unaware wired devices cannot be
  steered to IoT (a per-MAC override forces tagged delivery, which black-holes
  them), and the chain is invisible to the controller — it dropped the NAS uplink
  for four hours with nothing observable but the carrier flap on the NAS. A
  managed Flex Mini at the TV/bedroom drops would tag per port and show up in the
  controller. Everything works without it.
- **Dock MAC-passthrough experiment (optional).** The HP dock on the Connection A
  run presents its own MAC (`9c:7b:ef:9e:e6:46`) for whichever laptop is docked,
  so per-laptop wired steering is impossible; the work laptop docks onto Home and
  uses `DunderMiffLAN` over Wi-Fi for its own VLAN. If its firmware supports MAC
  address pass-through, the dock would present the laptop's built-in MAC and a
  Work steering reservation becomes possible. Firmware toggle plus one dock-in to
  read the resulting MAC.

---

## Related documentation

- [`terraform/unifi/README.md`](../terraform/unifi/README.md) — the root's own reference (managed objects, credentials, import recipes, provider caveats)
- [docs/01-overview.md](01-overview.md) — topology and the VLAN table
- [docs/08-dns.md](08-dns.md) — resolvers, rewrites, and the per-VLAN DHCP DNS
- [docs/11-firewall.md](11-firewall.md) — the `admin_lan` / `lan_clients` / `dns_clients` split
- [docs/12-runbooks.md](12-runbooks.md) — HA drain/maintenance and the stale-NFS-handle recovery
- [docs/20-plex-deployment.md](20-plex-deployment.md) — Plex's LAN Networks setting and the `:32400` forward
- [docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md) — bond and NIC-offload behaviour against the managed switch
- [docs/38-wireguard-vpn.md](38-wireguard-vpn.md) — the wg-easy VIP the DHCP pool excludes
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the `UniFi Controller` and `WiFi *` 1Password items
- [docs/16-next-steps.md](16-next-steps.md) — the open follow-ups from this work (UniFi metrics into Prometheus, the IPS engine upgrade and category re-baseline)
- [docs/48-unifi-audit-and-migration.md](48-unifi-audit-and-migration.md) — the 2026-08 audit findings and the bring-up / renumber record
