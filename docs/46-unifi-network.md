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

UCG-Fiber (every port role is software-assigned):

| Port | Role |
|---|---|
| 10G RJ45 | **WAN** — symmetric gigabit ethernet handoff |
| SFP+ 1 | **Trunk to the switch** (DAC): native VLAN 1, tagged 10/20/30/40/50 |
| 2.5G-1 | pve-laptop-01 — access, native VLAN 10 |
| 2.5G-2 | pve-prec-01 — access, native VLAN 10 |
| 2.5G-3 | Hue bridge — access, native VLAN 30 |
| 2.5G-4 (PoE+) | spare |

USW-Pro-XG-8-PoE (155 W PoE budget; the AP draws ≤29 W):

| Port | Role |
|---|---|
| SFP+ 1 | Uplink from the UCG — trunk all, native VLAN 1 |
| 1-2 | pve-opt-01 `nic0`/`nic1` — active-backup bond, **no LACP**, both access native VLAN 10 |
| 3-4 | pve-opt-02 — same |
| 5-6 | pve-opt-03 — same |
| 7 | U7 Pro XGS (PoE++) — trunk: native VLAN 1, tagged 20/30/40/50 |
| 8 | **Connection A** — native VLAN 20 (Home), tagged VLAN 10 |
| SFP+ 2 | spare |

"Connection A" is the run to the dumb 10G TP-Link switch, which fans out to
pve-nas-01 (VLAN 10 tagged), a 1G dumb switch (laptop dock, HDHomeRun), the
bedroom Hyperion Pi, and the MoCA leg feeding the living-room devices. It is
the one port carrying two VLANs to unmanaged gear, which is why pve-nas-01
needs a tagged sub-interface (§ Cutover, step 7) while everything else on that
run stays untagged on Home.

#### Accepted trust decisions on the Connection A run

The far end of port 8 is unmanaged, so **anything plugged in there that does
not tag its own frames lands on Home (VLAN 20)** — and Home reaches the homelab
in full (policy 1). That is the settled design, not an oversight, but it has
consequences worth naming so nobody re-derives them at 2 a.m.:

- **Wired TVs, streamers and game consoles are on Home, not IoT.** Only
  *wireless* devices can practically be placed on IoT, because that is the only
  place an SSID decides the VLAN. The wired ones keep full homelab reach — an
  accepted residual. Tagging those ports would need a managed switch at the far
  end of Connection A (tracked in [docs/16](16-next-steps.md), not a Phase 1
  blocker).
- **The work laptop's containment depends on how it is attached.** On the
  `3601-Work` SSID it is on VLAN 50 and gets DNS and nothing else; in the dock
  (1G dumb switch off Connection A) it is untagged Home and inherits
  `home → homelab any`. Work is therefore "the work laptop when mobile", which
  is the case the VLAN was created for. Validation row 6 tests the wireless
  path deliberately.
- **The laptop dock, the HDHomeRun and the bedroom Hyperion Pi are Home
  devices** and are reserved as such (§ DHCP reservations).

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
| `home` | Home | 20 | `10.0.20.1/24` | `.50`-`.249` | Personal client devices |
| `iot` | IoT | 30 | `10.0.30.1/24` | `.50`-`.249` | IGMP snooping on (as does Home — the two ends of the casting path) |
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

**Six `BLOCK` entries** — narrowing the two default-allow paths:

| # | From → To | Scope | Why |
|---|---|---|---|
| 12-14 | {guest,iot,work} → Gateway | tcp `22,80,443`, logged | On a Cloud Gateway the console is a gateway service on *every* VLAN's own gateway address, so without these a guest with the WLAN PSK gets a login form at `https://10.0.40.1`. DHCP is broadcast before the client has an address and is unaffected; ICMP to the gateway stays up for troubleshooting |
| 15-17 | {guest,iot,work} → External | tcp/udp `53,853`, logged | DHCP option 6 is a suggestion: Chromecast hardware queries `8.8.8.8` regardless and most TVs ship a vendor resolver. Blocking `:53`/`:853` outbound forces the weisssrv resolvers or nothing. Homelab is exempt — Unbound itself has to reach the internet |

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
here touches. If that ever stops being true — an `ALLOW` and a `BLOCK` on the
same zone-pair — the ordering becomes a UI step and this page must record it.

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
| home | hdhr | `10.0.20.200` | `00:18:DD:0A:37:45` |
| home | eric-bedroom-hyperion | `10.0.20.211` | `B8:27:EB:17:7D:DC` |
| iot | hue | `10.0.30.3` | `00:17:88:7E:C7:A2` |
| iot | K125M-0 … K125M-7 | `10.0.30.120`-`.127` | `6C:4C:BC:B0:0D:FE`, `:B0:0D:DD`, `:AF:F9:03`, `:AF:F0:AD`, `:AF:ED:23`, `:AF:E9:08`, `:AF:F0:DB`, `:B0:01:C8` |
| iot | living-room-hyperion | `10.0.30.210` | `B8:27:EB:A8:93:27` — see the note below |
| iot | wled-kitchen-island | `10.0.30.213` | `9C:9C:1F:45:76:FE` |
| iot | wled-kitchen-cabinets | `10.0.30.214` | `9C:9C:1F:45:6B:5E` |
| iot | wled-bar | `10.0.30.215` | `9C:9C:1F:45:CF:F9` |

**`living-room-hyperion` is reserved on IoT, which only takes effect if it is
wireless.** Its bedroom sibling is on Home because that one is wired to the
Connection A run, and a wired device there is untagged Home no matter what a
reservation says. If the living-room Pi turns out to be wired too, the
reservation is inert (it will take a Home pool address): move the entry to
`network = "home"` with a `10.0.20.x` address and re-plan. If it is wireless,
it joins the WLED controllers and the Kasa plugs in the step-10 re-onboarding
list.

**The admin MacBook's reservation depends on a MAC macOS randomises.** "Private
Wi-Fi Address" is on by default and per-SSID: the randomised address is stable
for a given SSID, so the reservation holds — until the network is forgotten and
rejoined, or the private address is rotated, at which point the MacBook falls
out of `10.0.20.8/29` and loses SSH, `:8006`, `:6443`, the appliance UIs and
RDP **at once**, because `admin_lan`, the sshd `from=`, fail2ban's `ignoreip`
and the `lan-tailscale-strict` middleware all key off that /29. Either turn
Private Wi-Fi Address off for TheRevengers on that machine, or reserve the
per-network random MAC exactly as the controller reports it. Way back in if it
happens anyway: **Tailscale** (`admin_ts` is unaffected by any of this).

Three reservations exist as **commented exemplars** in the root because their
MACs are unknown until the hardware is in hand: the switch at `10.0.1.2`, the
AP at `10.0.1.3` (both learned at adoption) and the admin MacBook at
`10.0.20.10`. Fill them in during bench pre-provisioning — the first two are
the blackbox probe targets, and the third is what makes the `10.0.20.8/29`
admin block in the Proxmox firewall mean anything.

Reservations deliberately sit **outside** each network's DHCP pool (pools start
at `.50`, `.100` on mgmt; reservations live below that in the same subnet). A
reservation inside the pool can collide with a lease the server has already
handed out; being in the subnet is the only thing the server actually requires.

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
| mDNS reflector (homelab + home + iot) | UI | Provider is read-only for this on UniFi OS gateways — Settings → Networks → Multicast DNS |
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
  itself follow via role variables (`weisssrv.infra` v0.13.0):
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

### Expected breakage between merge and cutover

The repo lands before the hardware does. Three things are wrong in that window
on purpose; none is an incident, and none needs a revert.

- **`NetworkGearProbeFailed` fires for `10.0.1.2` and `10.0.1.3`.** Nothing
  answers at the switch and AP addresses until the gear is cabled in
  (`192.168.0.1` keeps answering — the ASUS holds that address until step 5 of
  the cutover). It is a warning, so it does not page and letting it sit is a
  valid choice; silence it if the noise is in the way, renewing until the
  window:

  ```bash
  task observability:silence ALERT=NetworkGearProbeFailed DURATION=7d
  ```

- **`router.esweiss.com` returns 502.** The `vm-ingress` backend for that one
  hostname now expects the UCG's HTTPS UI on `:443` (via the
  `unifi-self-signed` ServersTransport), and the ASUS serves its UI on
  plaintext `:80`. The old UI stays reachable at `http://192.168.0.1`
  meanwhile; no other route touches this backend. It clears itself the moment
  the UCG takes over the gateway address.

- **`unifi-drift-plan` is yellow on every pipeline.** The job runs
  `terraform plan -detailed-exitcode` and carries `allow_failure: true`, so
  anything non-zero renders as one yellow badge. Before the `UniFi Controller`
  vault item exists the `op read`s yield empty strings and the plan dies on
  `unifi_api_key`'s length validation; once the item exists but the gateway is
  on a bench, the provider cannot reach `https://192.168.0.1`. Both look
  identical to real drift, which is exactly why the expected-yellow is written
  down here: **after the first supervised apply, the next scheduled
  `unifi-drift-plan` must go green. A yellow after that is real drift or a
  broken credential, and is investigated, not ignored.** (Narrowing the
  allowance to `exit_codes: 2` — so a broken plan is red and only drift is
  yellow — is a lib-wide follow-up for all three drift jobs, tracked in
  [docs/16](16-next-steps.md).)

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
   `~/.zsh_history` and is visible in `ps` for the duration of the call — so
   create the item with a generated placeholder and replace the field from a
   prompt:

   ```bash
   for item in "WiFi TheRevengers" "WiFi kugel-tikka-masala"; do
     op item create --category login --vault Homelab --title "$item" \
       --generate-password='letters,digits,32'
     printf 'PSK for %s: ' "$item"; read -rs psk; echo
     op item edit "$item" --vault Homelab "password=$psk"
   done
   unset psk
   ```

   The same rule applies to the `password=` and `api-key=` values in the
   `UniFi Controller` item above: paste them at a `read -rs` prompt, or run the
   command with a leading space if the shell is configured with
   `HISTCONTROL=ignorespace` / `setopt histignorespace`.

   `url` is the **production** address (`https://192.168.0.1`). While the
   gateway is still on the bench, override it per invocation with
   `TF_VAR_unifi_api_url=https://<bench-address>` rather than editing the item.

6. **Adopt the switch and the AP** (Settings → Devices). Both come up on the
   Default network by DHCP. Once adopted, read their MACs, give them fixed
   addresses `10.0.1.2` (switch) and `10.0.1.3` (AP), and fill in the commented
   client entries in `terraform/unifi/` so the addresses are codified rather
   than UI state.
7. **Assign the port map** (§ Physical port map) in the UI — native VLAN per
   access port, trunk profiles for SFP+ 1, port 7 and port 8. This is the step
   the provider cannot do safely; take a screenshot of the finished port list
   and keep it with the .unf backup.
8. **Turn on the mDNS reflector** for homelab, home and iot (Settings →
   Networks → Multicast DNS). Casting from Home to IoT depends on it.
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
task terraform:unifi-import -- 'module.network.unifi_client.this["hdhr"]' 00:18:DD:0A:37:45

# 2. Plan, and ASSERT: no `create` for unifi_network.this["default"].
task terraform:unifi-plan     # read every line

# 3. Apply ONE custom zone first — see the zone probe below.
task terraform:unifi-apply -- -target='module.network.unifi_firewall_zone.this["homelab"]'

# 4. …probe, then the rest.
task terraform:unifi-apply    # supervised: type "apply", then "yes" at terraform's own prompt
```

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
console`, which evaluates in root scope. Reading it needs the state-backend
variables the Taskfile anchor injects, which is why the console and the API are
the recipes given above.

Also verify at first plan that the built-in zone display names really are
`Internal` / `External` / `Gateway` on this controller, and that the client QoS
rate is named `Default` — both are taken from upstream examples, not from a
guaranteed schema.

---

## Cutover

Disruptive; needs console access to pve-nas-01 and a window where the house can
lose the network. Everything before this point was bench work.

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
   now delivers to switch port 8, they fight an ARP war over the default
   gateway of every Proxmox host and guest, and race each other to answer DHCP.
   That failure mode looks like nothing at all from the console and like
   intermittent connectivity everywhere else. Confirm only one answers:

   ```bash
   arping -D -I <nic> -c3 192.168.0.1     # no duplicate; MAC is the UCG's
   ```

   The ASUS keeps its *configuration* — nothing on it is changed, and rollback
   is re-cabling and re-powering it (§ Rollback).
6. **Re-cable the LAN.** UCG SFP+ 1 → switch SFP+ 1 (DAC); the three opt nodes
   onto ports 1-6; laptop and prec nodes onto 2.5G-1/2.5G-2; Hue onto 2.5G-3;
   AP onto port 7; **Connection A onto port 8** (native Home 20, tagged
   Homelab 10).

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
   ifreload -a`, and set port 8 back to untagged Homelab.

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
    roam over untouched. Everything that belongs on IoT/Work must be re-onboarded
    onto the new SSID — the WLED controllers, the Kasa plugs (`K125M-*`), any
    phone/laptop that should sit on Work, and `living-room-hyperion` if it turns
    out to be wireless (§ DHCP reservations). The Hue bridge moves to the wired
    IoT port (2.5G-3) rather than an SSID.
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

### Post-cutover checklist

The three items in § Expected breakage were true *on purpose* before the
window. Each one has to be actively retired, or the next person reads a stale
allowance as a sanctioned state:

- [ ] **Fill in the three commented reservations** (switch `10.0.1.2`, AP
  `10.0.1.3`, admin MacBook `10.0.20.10`) in `terraform/unifi/networks.tf` with
  the MACs the controller now reports, and supervised-apply. Until this lands
  the gear holds pool leases and `NetworkGearProbeFailed` stays red for the two
  device targets — a fill-in, not a cabling hunt.
- [ ] **Expire the `NetworkGearProbeFailed` silence** rather than renewing it,
  and confirm all three probes are green (validation row 18).
- [ ] **`router.esweiss.com` serves the UCG UI** over the `unifi-self-signed`
  transport — the 502 is gone (no repo change needed).
- [ ] **`unifi-drift-plan` is green on the next schedule** (validation row 23).
  From here on a yellow is drift or a broken credential.
- [ ] **Re-verify the bond procedure in [docs/34](34-bond-mac-flapping.md)** —
  the managed switch is a new link partner for all three bonded hosts.
- [ ] **Start the IPS burn-in clock** — a week of `ids` detections before
  flipping to `ips` (§ Day-2).

---

## Validation

Run the whole matrix before declaring the cutover done. "Expected" is what a
correct segmentation produces — several rows are *failures by design*.

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Per-VLAN DHCP | Join each SSID / plug into each access port | Address from the right pool, DNS `.150`/`.160`, domain `esweiss.com` (the **mgmt** VLAN is the exception: `1.1.1.1`/`9.9.9.9`) |
| 2 | Stranded wired leases | Controller client list after step 6 | Every wired device on the Connection A run has a `10.0.20.x` address — no `192.168.0.x` client outside VLAN 10 |
| 3 | Resolver reach from every VLAN | `dig @192.168.0.150 git.esweiss.com` from home/iot/guest/work | Answer on all four |
| 4 | Guest containment | From guest: `curl -m5 https://git.esweiss.com`, ping another guest client | Both **fail** (DNS resolves, everything else denied; L2 isolation blocks the peer) |
| 5 | IoT containment | From an IoT device: reach anything on Home or `:443` on homelab | **Fails**; only `:53`, Plex `:32400` and HA `:8123` succeed |
| 6 | Work containment | Same from Work | Only `:53` succeeds |
| 7 | Gateway console fenced | From guest/iot/work: `curl -m5 -k https://<that VLAN's .1>` and `ssh <that VLAN's .1>` | Both **fail** (BLOCK rows 12-14). `ping <that VLAN's .1>` still works — icmp is deliberately left up |
| 8 | External DNS fenced | From guest/iot/work: `dig @8.8.8.8 example.com`, `dig +tls @8.8.8.8 example.com` | Both **fail/time out** (BLOCK rows 15-17); `dig @192.168.0.150` still answers |
| 9a | Casting — the half that works | Cast a YouTube or Plex stream from a Home phone to an IoT TV/speaker | Device is discovered (mDNS reflector) and plays |
| 9b | Casting — the half that does not | Screen-mirror / cast a local photo from the same phone; AirPlay to two speakers at once | **Fails, by design** — the receiver would have to open a connection back to Home, and AirPlay 2 needs PTP multicast that does not route |
| 10 | Plex local stream | Play from a TV — **wireless TVs are on IoT, wired ones are on Home** (§ Accepted trust decisions); test whichever the household actually has | Direct play from `192.168.0.152:32400`, no transcode-over-WAN. If it transcodes, check Plex's LAN Networks setting (cutover step 11) before suspecting the network |
| 11 | HDHomeRun | Live TV in Plex | Tuner reachable at `10.0.20.200` (configured by IP) |
| 12 | Internal ingress from Home | Browse `https://grafana.esweiss.com` from a Home laptop | 200 — the `cluster_home_cidr` allowlist entry |
| 13 | Appliance UIs from Home | Browse `https://router.esweiss.com` from a **non**-admin Home device, then from the admin MacBook | Non-admin **fails** (`lan-tailscale-strict` is the `10.0.20.8/29` block); admin succeeds |
| 14 | Admin surfaces from Home | SSH to a Proxmox host from a **non**-admin Home device | **Refused** — only `10.0.20.8/29` is in `admin_lan` |
| 15 | Port forwards | From off-net: `curl -I https://<public>`, Plex remote, `ssh -p 2222 git@git.ericsweiss.com` | All succeed |
| 16 | wg-easy | Connect a WireGuard client from cellular | Handshake completes, internet egress works (docs/38) |
| 17 | Tailscale | `tailscale status` on a host; reach a guest over the tailnet | Subnet route still advertised and approved (docs/05) |
| 18 | Network gear probes | Grafana / Prometheus | `NetworkGearProbeFailed` clear for all three targets (needs ALLOW rows 10/11 *and* the two mgmt reservations filled in) |
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
