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
needs a tagged sub-interface (§ Cutover, step 5) while everything else on that
run stays untagged on Home.

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
| `default` | Default | 1 (built-in) | `10.0.1.1/24` | `.100`-`.199` | Management: gateway, switch, AP. Imported, not created (`name=Default`) |
| `homelab` | Homelab | 10 | `192.168.0.1/24` | `.2`-`.98` | Hosts, guests, k3s, VIPs. Phase 2 → `10.0.10.1/24` |
| `home` | Home | 20 | `10.0.20.1/24` | `.50`-`.249` | Personal client devices |
| `iot` | IoT | 30 | `10.0.30.1/24` | `.50`-`.249` | IGMP snooping on |
| `guest` | Guest | 40 | `10.0.40.1/24` | `.50`-`.249` | `purpose = corporate` — see below |
| `work` | Work | 50 | `10.0.50.1/24` | `.50`-`.249` | |

Every network hands out `192.168.0.150` / `192.168.0.160` as DHCP DNS and
`esweiss.com` as the domain, so split-horizon resolution
([docs/08-dns.md](08-dns.md)) works identically on every VLAN.

The homelab pool deliberately stops at `.98`: `.99` is the wg-easy MetalLB VIP
([docs/38-wireguard-vpn.md](38-wireguard-vpn.md)) and `.100`/`.101`/`.161` are
the other VIPs. That exclusion used to live only in router config; it is now a
codified pool bound.

**Guest uses `purpose = corporate` and a custom zone, not the guest/Hotspot
pair.** A guest-purpose network implies the captive-portal model, and its
natural zone (`Hotspot`) is a built-in the provider cannot import by name at
0.55.0 (upstream #396) — so the pair would move guest containment out of
Terraform and into the UI. It is kept in code instead, enforced by the two
things that actually do the work: the custom `guest` zone, whose only allow out
is DNS, and `l2_isolation` on the guest WLAN, which stops guest devices talking
to each other.

DHCP guarding (gateway-only DHCP server) is a **UI setting**, not a codified
one: the provider silently drops `dhcp_guarding.servers` on write for
corporate- and guest-purpose networks (#419), so declaring it would produce a
setting that never converges. Set it in the UI and record it here.

### Zones and policies

Every network gets **its own zone**, so the zone-based firewall's inter-zone
default-deny does the segmentation work: `homelab`, `home`, `iot`, `work`,
`guest` are custom zones created by Terraform, and the built-ins (`Internal`,
`External`, `Gateway`) are referenced through `data.unifi_firewall_zone` by
name. Default posture: inter-zone deny, every zone → External allow, every
zone → Gateway allow.

Explicit allows (`create_allow_respond = true`, so stateful returns are
created automatically):

| # | From → To | Scope | Why |
|---|---|---|---|
| 1 | home → homelab | any | Status-quo trust; per-port enforcement stays at the Proxmox firewall + NetworkPolicies |
| 2 | home → iot | any | Casting and device control (AirPlay/Chromecast data path) |
| 3 | homelab → iot | any | Home Assistant (.154) is the IoT controller |
| 4 | homelab → home | any | Plex → HDHomeRun, HA → TVs, admin reach |
| 5 | iot → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers |
| 6 | iot → homelab | tcp `:32400` → `.152` | Local Plex streaming from TVs |
| 7 | work → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers only |
| 8 | guest → homelab | tcp/udp `:53` → `.150`/`.160` | Resolvers only; everything else is internet-only |
| 9 | Internal → homelab | tcp/udp `:53` → `.150`/`.160` | Management-VLAN devices resolve too |

Blocked by the default deny, deliberately: iot → home, iot → work, work →
anything but DNS, guest → anything but DNS, home → work.

The resolver addresses and the Plex address are `locals` in the root
(`dns_ips`, `plex_ip`) — one edit point for Phase 2.

**Policy ordering is not codifiable.** `unifi_firewall_policy.index` is
read-only and new policies append to the end of their zone-pair (upstream
#407). The list above is order-independent by construction (all allows, no
overlapping denies), but if a deny is ever added, its position is a UI step and
this page must record it.

### Wireless

All four SSIDs are `wpapsk` on the U7 Pro XGS, `wlan_bands = ["2g","5g"]`.

| SSID | Network | WPA3 | Extras |
|---|---|---|---|
| TheRevengers | home | support + transition, PMF optional | Same PSK as today — devices roam over without re-onboarding |
| 3601-IoT | iot | off (plain WPA2, PMF disabled) | `no2ghz_oui = false` — ESP32/Kasa-friendly 2.4 GHz |
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
| iot | living-room-hyperion | `10.0.30.210` | `B8:27:EB:A8:93:27` |
| iot | wled-kitchen-island | `10.0.30.213` | `9C:9C:1F:45:76:FE` |
| iot | wled-kitchen-cabinets | `10.0.30.214` | `9C:9C:1F:45:6B:5E` |
| iot | wled-bar | `10.0.30.215` | `9C:9C:1F:45:CF:F9` |

Three reservations exist as **commented exemplars** in the root because their
MACs are unknown until the hardware is in hand: the switch at `10.0.1.2`, the
AP at `10.0.1.3` (both learned at adoption) and the admin MacBook at
`10.0.20.10`. Fill them in during bench pre-provisioning — the first two are
the blackbox probe targets, and the third is what makes the `10.0.20.8/29`
admin block in the Proxmox firewall mean anything.

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
| gitlab-ssh | tcp `2222` | `192.168.0.153:2222` | Same port both sides; the guest's sshd listens on 2222 (docs/27) |
| wg | **udp** `51820` | `192.168.0.99:51820` | wg-easy VIP — UDP, not TCP (docs/38) |

Phase 2 keeps the same last octets in `10.0.10.0/24`.

### Site settings

| Setting | Value | Why |
|---|---|---|
| `mgmt.auto_upgrade` | `false` | Firmware is an operator-chosen window, not a surprise reboot of the whole network |
| `network_optimization.enabled` | `false` | Auto-optimize rewrites exactly the settings this repo codifies |
| `usg.upnp_enabled` / `upnp_nat_pmp_enabled` | `false` | Port forwards are declared, never negotiated |
| `igmp_snooping` | on for homelab/home/iot | Multicast (casting, WLED, Hyperion) without flooding the segment |
| `ips.ips_mode` | `"ids"` | Detection first; flip to `ips` after burn-in — § Day-2 |

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

---

## weisssrv-side changes that ride with this

Phase 1 touches the repo in four places; each is documented where it lives.

- **Proxmox firewall** ([docs/11-firewall.md](11-firewall.md) § Client scopes):
  `admin_lan` shrinks to true admin surfaces and gains the `10.0.20.8/29`
  admin-device block; new `lan_clients` and `dns_clients` ipsets carry the
  service and resolver scopes. `ssh_authorized_keys` `from=`,
  `base_fail2ban_ignoreip` and `gitlab_ssh_allowed_users` mirror the admin
  split at the sshd layer.
- **Traefik internal allowlists**: `lan-tailscale-only` and
  `lan-tailscale-strict` gain `${cluster_home_cidr}`
  (`10.0.20.0/24`, from `kubernetes/infrastructure/sources/cluster-config.yaml`),
  so Home-VLAN devices keep reaching internal routes. No other client VLAN is
  allowlisted — the gateway's inter-zone deny is the first gate, the middleware
  the second.
- **`router.esweiss.com`** now proxies the gateway's HTTPS-only UI on `:443`
  through the `unifi-self-signed` ServersTransport (the UCG serves a
  self-signed certificate the acme.sh wildcard cannot cover), replacing the
  plaintext `:80` the ASUS served.
- **Observability** ([docs/31-observability.md](31-observability.md)): ICMP
  blackbox probes for `192.168.0.1`, `10.0.1.2` and `10.0.1.3` feed the
  `NetworkGearProbeFailed` alert (warning, 5m). Those instances are excluded
  from the `EndpointDown` catch-all so one cause fires once. **The switch and
  AP probes are expected RED until the cutover completes** — they do not exist
  at those addresses yet.

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

   op item create --category login --vault Homelab --title "WiFi TheRevengers" \
     password=<the existing house PSK, unchanged>
   op item create --category login --vault Homelab --title "WiFi 3601-IoT" \
     --generate-password='letters,digits,32'
   op item create --category login --vault Homelab --title "WiFi kugel-tikka-masala" \
     password=<guest PSK you are willing to read aloud>
   op item create --category login --vault Homelab --title "WiFi 3601-Work" \
     --generate-password='letters,digits,32'
   ```

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

### First Terraform apply

```bash
task terraform:unifi-init
task terraform:unifi-plan     # read every line
task terraform:unifi-apply    # supervised; type yes
```

Two things must happen in a specific order on the first run:

**Import the Default network** before anything else references it —
`unifi_network` is the only resource in this provider that accepts a `name=`
import id:

```bash
terraform import 'module.network.unifi_network.this["default"]' name=Default
```

Site settings import by **site name** (`id == site`), and clients import by
**colon-separated MAC only** (no `site:` prefix, dashes rejected):

```bash
terraform import 'module.network.unifi_setting.mgmt' default
terraform import 'module.network.unifi_client.this["hdhr"]' 00:18:DD:0A:37:45
```

**Probe zone membership before creating the second zone.** It is unverified
whether this controller moves a network out of `Internal` when a custom zone
claims it, and the provider neither does it nor detects it. So: apply **one**
custom zone, then read the built-in back without writing anything —

```bash
terraform console
> data.unifi_firewall_zone.internal.network_ids
```

If the network disappeared from `Internal`, the controller does the move and
the remaining zones can be applied normally. If it did not, the network sits in
two zones and policy evaluation is ambiguous — stop and resolve it in the UI
before continuing. Never import and manage `Internal` in the same apply as a
custom-zone create; two `unifi_firewall_zone` resources fighting over one
network is a loop, not a diff.

Also verify at first plan that the built-in zone display names really are
`Internal` / `External` / `Gateway` on this controller, and that the client QoS
rate is named `Default` — both are taken from upstream examples, not from a
guaranteed schema.

---

## Cutover

Disruptive; needs console access to pve-nas-01 and a window where the house can
lose the network. Everything before this point was bench work.

1. **Back up both sides.** Download the UniFi `.unf` backup (Settings → System
   → Backups) and export the ASUS configuration. The `.unf` is the only fast
   path back to a configured controller.
2. **Quiesce.** Stop or tolerate NFS-dependent workloads; a NAS uplink change
   mid-write is how stale handles happen. `task flux:status` before, so you
   know what "healthy" looked like.
3. **Move the WAN handoff** to the UCG's 10G RJ45 port. Verify from a wired
   client on Default: gateway reachable, internet reachable, WAN IP as
   expected.
4. **Re-cable the LAN.** UCG SFP+ 1 → switch SFP+ 1 (DAC); the three opt nodes
   onto ports 1-6; laptop and prec nodes onto 2.5G-1/2.5G-2; Hue onto 2.5G-3;
   AP onto port 7; **Connection A onto port 8** (native Home 20, tagged
   Homelab 10).
5. **Flip pve-nas-01 onto the tagged VLAN.** Its uplink is the one that must
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
6. **Bring the rest of the estate back**: confirm every Proxmox host and guest
   pings, `task flux:status` is clean, and NFS mounts are alive (a pod holding a
   stale handle needs deleting, not restarting — docs/12).
7. **Move the wireless clients.** TheRevengers keeps its PSK, so home devices
   roam over untouched. Everything that belongs on IoT/Work must be re-onboarded
   onto the new SSID — the WLED controllers, the Kasa plugs (`K125M-*`), and any
   phone/laptop that should sit on Work. The Hue bridge moves to the wired IoT
   port (2.5G-3) rather than an SSID.
8. **Re-point discovery-based integrations** per the SSDP table above.

---

## Validation

Run the whole matrix before declaring the cutover done. "Expected" is what a
correct segmentation produces — several rows are *failures by design*.

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Per-VLAN DHCP | Join each SSID / plug into each access port | Address from the right pool, DNS `.150`/`.160`, domain `esweiss.com` |
| 2 | Resolver reach from every VLAN | `dig @192.168.0.150 git.esweiss.com` from home/iot/guest/work | Answer on all four |
| 3 | Guest containment | From guest: `curl -m5 https://git.esweiss.com`, ping another guest client | Both **fail** (DNS resolves, everything else denied; L2 isolation blocks the peer) |
| 4 | IoT containment | From an IoT device: reach anything on Home or `:443` on homelab | **Fails**; only `:53` and Plex `:32400` succeed |
| 5 | Work containment | Same from Work | Only `:53` succeeds |
| 6 | Casting | Cast from a Home phone to an IoT TV/speaker | Device is discovered (mDNS reflector) and plays |
| 7 | Plex local stream | Play from a TV on IoT | Direct play from `192.168.0.152:32400`, no transcode-over-WAN |
| 8 | HDHomeRun | Live TV in Plex | Tuner reachable at `10.0.20.200` (configured by IP) |
| 9 | Internal ingress from Home | Browse `https://grafana.esweiss.com` from a Home laptop | 200 — the `cluster_home_cidr` allowlist entry |
| 10 | Admin surfaces from Home | SSH to a Proxmox host from a **non**-admin Home device | **Refused** — only `10.0.20.8/29` is in `admin_lan` |
| 11 | Port forwards | From off-net: `curl -I https://<public>`, Plex remote, `ssh -p 2222 git@git.ericsweiss.com` | All succeed |
| 12 | wg-easy | Connect a WireGuard client from cellular | Handshake completes, internet egress works (docs/38) |
| 13 | Tailscale | `tailscale status` on a host; reach a guest over the tailnet | Subnet route still advertised and approved (docs/05) |
| 14 | Network gear probes | Grafana / Prometheus | `NetworkGearProbeFailed` clear for all three targets |
| 15 | Bond health | `cat /proc/net/bonding/bond0` on each opt node | `all_slaves_active 0`, one active leg (docs/34) |
| 16 | e1000e / AQC113 watch | Loki, over the next 48 h: `{job="journal"} \|= "Hardware Unit Hang"` | No hits (docs/34) |
| 17 | Cluster health | `task flux:status`, `task infra:verify` | Clean |

## Rollback

The ASUS is untouched by any of this — no setting on it was changed, and it
keeps its own configuration export from step 1. Rollback is therefore physical:

1. Move the WAN handoff back to the ASUS.
2. Re-cable the hosts and the dumb switches to the old unmanaged switch.
3. Revert pve-nas-01's `/etc/network/interfaces` from `/root/interfaces.pre-vlan`
   and `ifreload -a`.
4. Leave the UniFi gear powered off; its state (and the `.unf` backup) survives
   for the next attempt.

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

---

## Related documentation

- [`terraform/unifi/README.md`](../terraform/unifi/README.md) — the root's own reference (managed objects, credentials, import recipes, provider caveats)
- [docs/01-overview.md](01-overview.md) — topology and the VLAN table
- [docs/08-dns.md](08-dns.md) — resolvers, rewrites, and the per-VLAN DHCP DNS
- [docs/11-firewall.md](11-firewall.md) — the `admin_lan` / `lan_clients` / `dns_clients` split
- [docs/34-bond-mac-flapping.md](34-bond-mac-flapping.md) — bond and NIC-offload behaviour to re-verify after cutover
- [docs/38-wireguard-vpn.md](38-wireguard-vpn.md) — the wg-easy VIP the DHCP pool excludes
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the `UniFi Controller` and `WiFi *` 1Password items
- [docs/16-next-steps.md](16-next-steps.md) — the follow-ups this work opened (6 GHz, unpoller, IPS)
