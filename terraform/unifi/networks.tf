# Site data — the VLAN/zone/policy inventory this gateway serves. The SHAPE
# (every resource, its `prevent_destroy` guard, the derived `matching_target`,
# the hardened setting defaults) lives in the library module pinned in main.tf;
# what follows is data only.
#
# Map keys are STATE ADDRESSES and the name every other map references, so a
# rename is a `moved {}` block — `unifi_network` and `unifi_firewall_zone` carry
# `prevent_destroy` module-side and refuse the destroy half of a rename.
locals {
  # The two AdGuard/Unbound resolvers (docs/08-dns.md). Handed to every client
  # VLAN by DHCP and allowed through the zone policies below from the VLANs that
  # get nothing else.
  #
  # These three addresses, the `homelab` subnet/DHCP scope below and all five
  # `port_forwards` targets moved together in the Phase 2 renumber — README
  # § Phase 2 is the complete list, not this comment.
  dns_ips = ["10.0.10.150", "10.0.10.160"]
  plex_ip = "10.0.10.152"
  # Home Assistant (docs/24). Named here because the IoT VLAN gets a policy to
  # it: cast/TTS and webhook callbacks are device-initiated, so the outbound
  # `homelab-to-iot` allow does not cover them.
  ha_ip = "10.0.10.154"

  # The two adopted UniFi devices on the management VLAN, single-sourced because
  # three things name them: the ICMP policy pair below, the `usw-pro-xg-8` and
  # `u7-pro-xgs` client reservations further down, and the blackbox probes in
  # kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml.
  mgmt_device_ips = ["10.0.1.2", "10.0.1.3"]

  # `subnet` is GATEWAY form: the host part is the gateway address, so
  # "10.0.30.1/24" is the network 10.0.30.0/24 with the gateway on .1. The
  # module rejects the network-address form — writing "10.0.30.0/24" applies
  # cleanly and hands every DHCP client .0 as its gateway.
  networks = {
    # The controller's built-in network (VLAN 1, untagged): UniFi management
    # only — the gateway, the switch and the AP. It carries no `vlan`, which is
    # what marks it as the built-in, and it is IMPORTED rather than created
    # (`terraform import ... name=Default`, README § Adopting the live site).
    #
    # DHCP hands out PUBLIC resolvers, not `local.dns_ips`: the switch and the
    # AP reach the network through themselves, and the resolvers are LXC guests
    # two layers below them, so pointing this VLAN at .150/.160 is the same
    # bootstrap loop docs/46 rejects for the gateway's own WAN DNS. Nothing on
    # this VLAN needs split-horizon answers — it resolves ui.com for adoption,
    # firmware and telemetry and nothing else.
    default = {
      name        = "Default"
      subnet      = "10.0.1.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "10.0.1.100", stop = "10.0.1.199", dns_servers = ["1.1.1.1", "9.9.9.9"] }
    }

    # Hosts, guests, k3s nodes and every MetalLB/kube-vip VIP. Renumbered from
    # 192.168.0.0/24 in Phase 2 (docs/46 § Phase 2) with every last octet
    # preserved; the pool stays below .99 so the VIPs .99-.101 and .161, and the
    # statically addressed hosts and guests, are never handed out by DHCP.
    homelab = {
      name        = "Homelab"
      vlan        = 10
      subnet      = "10.0.10.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "10.0.10.2", stop = "10.0.10.98", dns_servers = local.dns_ips }
    }

    # `igmp_snooping` — see the note on `iot` below; Home and IoT are the two
    # ends of the casting path, so they carry it and nothing else does.
    home = {
      name          = "Home"
      vlan          = 20
      subnet        = "10.0.20.1/24"
      domain_name   = "esweiss.com"
      igmp_snooping = true
      # The pool stops at .199 so the two Home reservations (.200, .211) and the
      # admin block at .8-.15 both sit outside it — see `clients` below.
      dhcp = { start = "10.0.20.50", stop = "10.0.20.199", dns_servers = local.dns_ips }
    }

    # Per-network IGMP snooping is set here for older controllers; the effective
    # toggle on Network 10.3+ is `site_settings.igmp_snooping_networks` in
    # main.tf, which lists exactly the same two networks. Homelab is
    # deliberately NOT snooped: nothing multicast-critical runs on VLAN 10 that
    # a missing querier could prune (Proxmox corosync is unicast knet), and
    # snooping a cluster VLAN is risk without a benefit.
    iot = {
      name          = "IoT"
      vlan          = 30
      subnet        = "10.0.30.1/24"
      domain_name   = "esweiss.com"
      igmp_snooping = true
      # A 50-address dynamic range, because every IoT device that matters is
      # already reserved: the pool stops at .99 so the Hue bridge (.3) sits
      # below it and the Kasa/Hyperion/WLED reservations (.120-.215) above it.
      dhcp = { start = "10.0.30.50", stop = "10.0.30.99", dns_servers = local.dns_ips }
    }

    # `purpose` stays "corporate" even though this is the guest VLAN: the
    # controller rewrites `guest` to `corporate` for any network outside its own
    # Hotspot zone, and the apply then fails with an inconsistent-result error.
    # Guest ISOLATION here is the custom zone (default inter-zone deny) plus the
    # WLAN's `l2_isolation`, not the network's purpose flag.
    guest = {
      name        = "Guest"
      vlan        = 40
      subnet      = "10.0.40.1/24"
      domain_name = "esweiss.com"
      purpose     = "corporate"
      dhcp        = { start = "10.0.40.50", stop = "10.0.40.249", dns_servers = local.dns_ips }
    }

    work = {
      name        = "Work"
      vlan        = 50
      subnet      = "10.0.50.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "10.0.50.50", stop = "10.0.50.249", dns_servers = local.dns_ips }
    }
  }

  # One zone per VLAN. Inter-zone traffic is denied by default, so every
  # allowance is an explicit `policies` entry below and nothing is load-bearing
  # on rule ORDER — which this provider cannot manage (`index` is read-only).
  #
  # The map key is the zone's DISPLAY NAME on the controller. The built-in
  # zones (Internal/External/Gateway) are read, never managed — see
  # `builtin_zone_names` in main.tf. `default` is deliberately absent: the
  # management VLAN stays in the built-in Internal zone.
  zones = {
    homelab = { networks = ["homelab"] }
    home    = { networks = ["home"] }
    iot     = { networks = ["iot"] }
    guest   = { networks = ["guest"] }
    work    = { networks = ["work"] }
  }

  # The policy set. Two kinds of entry live here:
  #
  #   ALLOW — an allowance against the inter-zone default deny. Everything NOT
  #   listed is denied: iot->home, iot->work, work->any internal zone but the
  #   resolvers, guest->any internal zone but the resolvers, home->work, and
  #   every reverse direction that is not a stateful response. (Internet egress
  #   is the External default-allow, minus the DNS BLOCKs below.)
  #
  #   BLOCK — a narrowing of the two paths UniFi allows by DEFAULT and no ALLOW
  #   entry can take away: every zone reaches External, and every zone reaches
  #   Gateway. Those defaults are what expose the console login on each VLAN's
  #   own gateway address and let a device ignore DHCP option 6, so the last two
  #   groups below fence them off explicitly.
  #
  # `name` is the state address AND the rule name in the UI, so it must be
  # unique and a rename replaces the policy.
  #
  # `create_allow_respond` writes the matching established/related return rule.
  # The module forces it off for anything that is not an ALLOW, so the BLOCK
  # entries below say nothing about it; it is spelled out only on the ICMP pair,
  # where the controller REJECTS it and the reverse direction therefore has to
  # be a policy of its own.
  policies = [
    # Trusted household network reaches the homelab as it does today; per-port
    # enforcement stays with the Proxmox firewall and the k8s NetworkPolicies,
    # which is where it is already reviewed.
    {
      name        = "home-to-homelab"
      source      = { zone = "home" }
      destination = { zone = "homelab" }
    },
    # Casting and control: the AirPlay/Chromecast data path from phones and
    # laptops to the TVs and speakers on IoT. Discovery itself is multicast and
    # does NOT cross the VLAN boundary — mDNS reflection is a UI step and SSDP
    # has no reflector at all (docs/46 § Codified vs manual).
    {
      name        = "home-to-iot"
      source      = { zone = "home" }
      destination = { zone = "iot" }
    },
    # Home Assistant (.154) is the IoT controller: it polls and pushes to every
    # device on VLAN 30, over a different protocol per integration.
    {
      name        = "homelab-to-iot"
      source      = { zone = "homelab" }
      destination = { zone = "iot" }
    },
    # Plex -> HDHomeRun (10.0.20.200), Home Assistant -> the TVs, and admin
    # reach from a homelab jump host.
    {
      name        = "homelab-to-home"
      source      = { zone = "homelab" }
      destination = { zone = "home" }
    },
    # IoT gets the homelab resolvers and nothing else; `ips` pins the two
    # AdGuard hosts rather than the whole VLAN.
    {
      name        = "iot-to-homelab-dns"
      protocol    = "tcp_udp"
      source      = { zone = "iot" }
      destination = { zone = "homelab", ips = local.dns_ips, port = "53" }
    },
    # Smart TVs stream from Plex directly. Server discovery is by IP in the Plex
    # client (multicast does not cross zones).
    {
      name        = "iot-to-homelab-plex"
      protocol    = "tcp"
      source      = { zone = "iot" }
      destination = { zone = "homelab", ips = [local.plex_ip], port = "32400" }
    },
    # Home Assistant's DEVICE-INITIATED paths. `homelab-to-iot` covers the polls
    # and pushes HA makes; it does not cover the flows where the IoT device is
    # the client — a Cast speaker fetching the TTS proxy URL HA handed it, and
    # any integration webhook posting back to HA's internal_url. Both are :8123
    # to .154, so the allowance is that one address and that one port.
    {
      name        = "iot-to-homelab-ha"
      protocol    = "tcp"
      source      = { zone = "iot" }
      destination = { zone = "homelab", ips = [local.ha_ip], port = "8123" }
    },
    {
      name        = "work-to-homelab-dns"
      protocol    = "tcp_udp"
      source      = { zone = "work" }
      destination = { zone = "homelab", ips = local.dns_ips, port = "53" }
    },
    {
      name        = "guest-to-homelab-dns"
      protocol    = "tcp_udp"
      source      = { zone = "guest" }
      destination = { zone = "homelab", ips = local.dns_ips, port = "53" }
    },
    # The management VLAN is in the built-in Internal zone, so `internal` is a
    # `builtin_zone_names` key rather than a `zones` one — both resolve from the
    # same namespace.
    #
    # This pair is what makes the blackbox ICMP probes for the switch and the AP
    # work at all: blackbox runs as a pod on a k3s node, so its echo requests
    # SNAT to a homelab (VLAN 10) address and cross homelab -> Internal, which
    # the default deny drops. Two policies because the controller REJECTS
    # `create_allow_respond` on icmp — the reply direction has to be written
    # explicitly. Scoped to the two device addresses, not the whole VLAN.
    #
    # There is deliberately NO Internal -> homelab DNS policy: the mgmt VLAN's
    # DHCP hands out public resolvers (see the `default` network above), so the
    # switch and the AP never ask .150/.160 for anything.
    {
      name                 = "homelab-to-internal-icmp"
      protocol             = "icmp"
      create_allow_respond = false
      source               = { zone = "homelab" }
      destination          = { zone = "internal", ips = local.mgmt_device_ips }
    },
    {
      name                 = "internal-to-homelab-icmp"
      protocol             = "icmp"
      create_allow_respond = false
      source               = { zone = "internal", ips = local.mgmt_device_ips }
      destination          = { zone = "homelab" }
    },

    # ---- Gateway hardening ----
    #
    # UniFi allows every zone -> Gateway by default, and on a Cloud Gateway the
    # console (and therefore the Network application, and therefore the local
    # admin whose API key rewrites this whole file) is a gateway service on
    # EVERY VLAN's own gateway address. A guest with the WLAN PSK otherwise gets
    # an unthrottled login form at https://10.0.40.1.
    #
    # tcp only, and only the console ports: DHCP is broadcast before the client
    # has an address and is unaffected, and ICMP to the gateway stays up for
    # troubleshooting. The gateway's own DNS forwarder is a separate default
    # -allow path and is fenced by the `*-to-gateway-dns` BLOCKs below.
    {
      name        = "guest-to-gateway-mgmt"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "guest" }
      destination = { zone = "gateway", port = "22,80,443" }
    },
    {
      name        = "iot-to-gateway-mgmt"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "iot" }
      destination = { zone = "gateway", port = "22,80,443" }
    },
    {
      name        = "work-to-gateway-mgmt"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "work" }
      destination = { zone = "gateway", port = "22,80,443" }
    },

    # ---- DNS containment ----
    #
    # DHCP option 6 is a suggestion. Chromecast/Google Home hardware queries
    # 8.8.8.8 regardless, and most smart TVs ship a vendor resolver — so without
    # these the VLANs that are supposed to be filtered and logged by AdGuard are
    # the ones most likely to bypass it.
    #
    # There are TWO ways off the resolvers, and both are default-allow: a public
    # resolver out through External, and the gateway's own forwarder, which a
    # UniFi OS gateway answers on EVERY VLAN's .1 and which forwards to the WAN
    # DNS servers (1.1.1.1/9.9.9.9 — docs/46 § Site settings), i.e. straight
    # past AdGuard. Both are blocked below; together they are what makes
    # "the weisssrv resolvers or nothing" true on these three VLANs.
    #
    # Logged, like the gateway-console BLOCKs above: a device that has quietly
    # lost name resolution is diagnosed from the gateway's firewall log, and
    # these are the drops that explain it.
    #
    # DoH on :443 is NOT covered — it is indistinguishable from ordinary HTTPS
    # at this layer and closing it needs the IDS/IPS or a blocklist (docs/46).
    # Homelab is exempt: Unbound itself has to reach the internet, and the mgmt
    # VLAN is deliberately pointed at public resolvers (see `default` above).
    {
      name        = "guest-to-external-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "guest" }
      destination = { zone = "external", port = "53,853" }
    },
    {
      name        = "iot-to-external-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "iot" }
      destination = { zone = "external", port = "53,853" }
    },
    {
      name        = "work-to-external-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "work" }
      destination = { zone = "external", port = "53,853" }
    },
    # The gateway half. tcp_udp rather than the tcp of the console BLOCKs
    # because DNS is udp first; DHCP (udp 67/68) is untouched, and nothing on
    # these VLANs may use the gateway as a resolver — DHCP hands them
    # .150/.160 and that is the only answer they are meant to get.
    {
      name        = "guest-to-gateway-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "guest" }
      destination = { zone = "gateway", port = "53,853" }
    },
    {
      name        = "iot-to-gateway-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "iot" }
      destination = { zone = "gateway", port = "53,853" }
    },
    {
      name        = "work-to-gateway-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "work" }
      destination = { zone = "gateway", port = "53,853" }
    },
  ]

  # MAC CASE MATTERS: the provider compares MACs case-SENSITIVELY against the
  # controller's lowercase spellings (GetClientByMAC is a plain string match
  # over /rest/user), and `mac` is ForceNew — so an adopted or imported entry
  # must spell its MAC in LOWERCASE or every apply plans a replacement and the
  # MacUsed->adopt path dies with "not found: type=". Entries created fresh by
  # the provider carry their config spelling in STATE, which is why the
  # cutover-era entries below are uppercase and must stay that way until they
  # are ever re-imported. New entries: lowercase, always.
  # Fixed-IP reservations. Every entry ADOPTS the client the controller already
  # knows (`allow_existing` is module-side), so these do not create anything.
  #
  # A reservation also STEERS a client's VLAN, which makes this map the standing
  # mechanism for putting a device on the right network: add an entry naming the
  # target network and a WIRELESS device moves on its next association — no SSID
  # re-join, no touching the device. That is how the WLED controllers and the
  # Kasa plugs reached IoT at cutover without ever joining `3601-IoT`. Steering
  # is placement, not authorization: a device that keeps the Home PSK falls back
  # to Home if its MAC ever stops matching, so IoT-class devices still get
  # re-onboarded onto `3601-IoT` over time (docs/46 § DHCP reservations).
  #
  # WIRED devices behind the unmanaged switches on Connection A are the caveat:
  # steering them needs the USW to assign a VLAN by MAC to a device it does not
  # see on its own port. Where that does not take, the entry is inert and the
  # device stays on the port's native VLAN. Entries in that position are marked
  # individually below.
  #
  # CHANGING ONE IS A REPLACE, not an update: upstream #428 fails every in-place
  # `unifi_client` update with "inconsistent result after apply: .last_ip". Use
  # `terraform apply -replace='module.network.unifi_client.this["<key>"]'`; the
  # device is untouched, only the controller-side object is recreated.
  #
  # APPLIES ON THIS ROOT WERE FROZEN while the module sat at v0.13.0: that
  # version left `setting_preference` at the provider default `auto`, and every
  # write to a network then made the controller reset the manual DHCP fields
  # (dns_enabled, domain_name) it was being told to keep. The v0.13.1 pin in
  # main.tf fixes that; the first supervised unfreeze apply is in README.md.
  #
  # The 2026-08-22/23 additions touch fourteen resources at that first apply:
  # thirteen creates, plus one replacement. Carrying the creates meanwhile costs
  # nothing, because a reservation the controller has not been told about is
  # simply a pool lease. **`eric-bedroom-hyperion` is the replacement and needs
  # a flag**: it is an existing client MOVED from
  # home/.20.211 to iot/.30.211, so #428 applies and the unfreeze apply must
  # carry
  # `-replace='module.network.unifi_client.this["eric-bedroom-hyperion"]'`.
  # Everything else added is a create. docs/46 § Cutover as executed.
  #
  # Homelab hosts and guests are statically addressed by Ansible and are
  # deliberately absent — a reservation for an address the host also configures
  # itself is two sources of truth for one address.
  #
  # INVARIANT: every reservation sits OUTSIDE its network's DHCP pool, and the
  # pool bounds in `local.networks` are what enforce it. A reservation inside
  # the pool can collide with a lease the server has already handed out; being
  # in the same SUBNET is the only thing the server itself requires.
  #
  # The reservations are the last octets these devices already had on the flat
  # LAN, so the pools are bounded around them rather than the other way round:
  #
  #   home  .50-.199 — macbook .10 below; hdhr .200 above
  #   iot   .50-.99  — hue .3 below; the Kasa plugs .120-.127 above, then one
  #                    device block .210-.225 (.212 unused): both Hyperion Pis
  #                    .210-.211, the WLED controllers .213-.215, the Levoit
  #                    appliances .216-.217, and the TVs and Echoes .218-.225
  #   mgmt  .100-.199 — the switch .2 and the AP .3 below
  #
  # ADDING ONE: pick an address outside the pool for its network, or move the
  # pool bound in `local.networks` first. Both halves are one plan.
  clients = {
    # Home (VLAN 20)
    hdhr = {
      mac      = "00:18:dd:0a:37:45"
      name     = "hdhr"
      fixed_ip = "10.0.20.200"
      network  = "home"
    }
    # The admin workstation, which the Proxmox firewall's `admin_lan`
    # 10.0.20.8/29 block is sized for. The MAC is the macOS PER-NETWORK "Fixed"
    # private Wi-Fi address, not the hardware one: it is stable for as long as
    # TheRevengers is remembered, and is regenerated if the network is forgotten
    # and rejoined or the setting is toggled. If that happens the MacBook drops
    # out of the /29 and loses SSH, :8006, :6443, the appliance UIs and RDP at
    # once — re-read the address from the controller's client list and
    # `-replace` this entry (Tailscale is the way back in meanwhile).
    macbook = {
      mac      = "a2:30:58:e7:62:f2"
      name     = "macbook"
      fixed_ip = "10.0.20.10"
      network  = "home"
    }

    # IoT (VLAN 30)
    hue = {
      mac      = "00:17:88:7e:c7:a2"
      name     = "hue"
      fixed_ip = "10.0.30.3"
      network  = "iot"
    }
    # Kasa KP125M smart plugs, .120-.127 in adoption order.
    k125m-0 = {
      mac      = "6C:4C:BC:B0:0D:FE"
      name     = "K125M-0"
      fixed_ip = "10.0.30.120"
      network  = "iot"
    }
    k125m-1 = {
      mac      = "6C:4C:BC:B0:0D:DD"
      name     = "K125M-1"
      fixed_ip = "10.0.30.121"
      network  = "iot"
    }
    k125m-2 = {
      mac      = "6C:4C:BC:AF:F9:03"
      name     = "K125M-2"
      fixed_ip = "10.0.30.122"
      network  = "iot"
    }
    k125m-3 = {
      mac      = "6C:4C:BC:AF:F0:AD"
      name     = "K125M-3"
      fixed_ip = "10.0.30.123"
      network  = "iot"
    }
    k125m-4 = {
      mac      = "6C:4C:BC:AF:ED:23"
      name     = "K125M-4"
      fixed_ip = "10.0.30.124"
      network  = "iot"
    }
    k125m-5 = {
      mac      = "6C:4C:BC:AF:E9:08"
      name     = "K125M-5"
      fixed_ip = "10.0.30.125"
      network  = "iot"
    }
    k125m-6 = {
      mac      = "6C:4C:BC:AF:F0:DB"
      name     = "K125M-6"
      fixed_ip = "10.0.30.126"
      network  = "iot"
    }
    k125m-7 = {
      mac      = "6C:4C:BC:B0:01:C8"
      name     = "K125M-7"
      fixed_ip = "10.0.30.127"
      network  = "iot"
    }
    living-room-hyperion = {
      mac      = "B8:27:EB:A8:93:27"
      name     = "living-room-hyperion"
      fixed_ip = "10.0.30.210"
      network  = "iot"
    }
    # WIRED, behind the unmanaged switches on Connection A — unlike its
    # living-room sibling, which is wireless. Per-MAC steering for a wired
    # client depends on the USW assigning a VLAN by MAC to a device it does not
    # see on its own port; if that takes, this moves to IoT like the wireless
    # devices. If it does not, the entry is inert and the Pi stays on the port's
    # native VLAN — Homelab today, Home after the Connection A finale — and
    # placing it on IoT then needs a managed switch at the far end (docs/16).
    eric-bedroom-hyperion = {
      mac      = "b8:27:eb:17:7d:dc"
      name     = "eric-bedroom-hyperion"
      fixed_ip = "10.0.30.211"
      network  = "iot"
    }
    wled-kitchen-island = {
      mac      = "9C:9C:1F:45:76:FE"
      name     = "wled-kitchen-island"
      fixed_ip = "10.0.30.213"
      network  = "iot"
    }
    wled-kitchen-cabinets = {
      mac      = "9C:9C:1F:45:6B:5E"
      name     = "wled-kitchen-cabinets"
      fixed_ip = "10.0.30.214"
      network  = "iot"
    }
    wled-bar = {
      mac      = "9C:9C:1F:45:CF:F9"
      name     = "wled-bar"
      fixed_ip = "10.0.30.215"
      network  = "iot"
    }
    # Levoit appliances. Both were on the flat LAN without reservations and
    # landed on Home at cutover; these entries are what moves them, since a
    # reservation names the VLAN a wireless client joins regardless of the SSID
    # it associates with (docs/46 § Cutover as executed).
    levoit-purifier = {
      mac      = "a8:48:fa:34:3e:88"
      name     = "levoit-purifier"
      fixed_ip = "10.0.30.216"
      network  = "iot"
    }
    levoit-humidifier = {
      mac      = "1c:9d:c2:73:00:b8"
      name     = "levoit-humidifier"
      fixed_ip = "10.0.30.217"
      network  = "iot"
    }

    # TVs and streaming devices. Policy-wise IoT is the right home for them:
    # `home-to-iot` and `homelab-to-iot` are full allows, so phones still cast
    # and Home Assistant still drives them, while the reverse direction is only
    # DNS, Plex :32400 and HA :8123.
    #
    # The amazon-* keys are the controller's reported hostnames, kept as-is
    # until the units are mapped to rooms (docs/16). vizio-cast-display is
    # WIRED on the MoCA leg and carries the same caveat as
    # eric-bedroom-hyperion above; the rest are wireless and move on their next
    # association.
    vizio-cast-display = {
      mac      = "3c:9b:d6:7a:36:a3"
      name     = "vizio-cast-display"
      fixed_ip = "10.0.30.218"
      network  = "iot"
    }
    vizio-wifi = {
      mac      = "a0:6a:44:50:ee:95"
      name     = "vizio-wifi"
      fixed_ip = "10.0.30.225"
      network  = "iot"
    }
    amazon-01f20c070 = {
      mac      = "fc:49:2d:c3:d5:24"
      name     = "amazon-01f20c070"
      fixed_ip = "10.0.30.219"
      network  = "iot"
    }
    amazon-5b51cd6d9 = {
      mac      = "38:f7:3d:11:a1:11"
      name     = "amazon-5b51cd6d9"
      fixed_ip = "10.0.30.220"
      network  = "iot"
    }
    amazon-a70f51c2d = {
      mac      = "dc:91:bf:d5:7e:e4"
      name     = "amazon-a70f51c2d"
      fixed_ip = "10.0.30.221"
      network  = "iot"
    }
    amazon-a9c5657f8 = {
      mac      = "fc:49:2d:ea:f0:aa"
      name     = "amazon-a9c5657f8"
      fixed_ip = "10.0.30.222"
      network  = "iot"
    }
    # Amazon OUI, no hostname reported by the controller — presumed Echoes.
    amazon-f57e91 = {
      mac      = "40:a2:db:f5:7e:91"
      name     = "amazon-f57e91"
      fixed_ip = "10.0.30.223"
      network  = "iot"
    }
    amazon-c7d8bc = {
      mac      = "34:d2:70:c7:d8:bc"
      name     = "amazon-c7d8bc"
      fixed_ip = "10.0.30.224"
      network  = "iot"
    }

    # Management (VLAN 1) — the switch and the AP, so the blackbox ICMP probes
    # in kubernetes/infrastructure/observability have stable targets. MACs read
    # from the controller after adoption (Devices -> the device -> MAC).
    usw-pro-xg-8 = {
      mac      = "74:F9:2C:A6:A2:57"
      name     = "usw-pro-xg-8"
      fixed_ip = "10.0.1.2"
      network  = "default"
    }
    u7-pro-xgs = {
      mac      = "90:41:B2:C8:86:65"
      name     = "u7-pro-xgs"
      fixed_ip = "10.0.1.3"
      network  = "default"
    }
  }

  # WAN port forwards. Every one is on the primary WAN and accepts any source:
  # source restriction lives in the Proxmox firewall and the k8s
  # NetworkPolicies, not in a per-forward allowlist nothing else can see.
  #
  # The map key is the forward's name in the UI. Targets are homelab addresses;
  # all five moved in the Phase 2 renumber (docs/46 § Phase 2).
  port_forwards = {
    # Traefik's public MetalLB VIP (10.0.10.100) — every *.ericsweiss.com name.
    http  = { wan_port = "80", ip = "10.0.10.100", port = "80" }
    https = { wan_port = "443", ip = "10.0.10.100", port = "443" }
    # Plex direct-connect (docs/20). The LXC is not behind Traefik.
    plex = { wan_port = "32400", ip = local.plex_ip, port = "32400" }
    # Git-over-SSH, deliberately WAN-open (docs/27 § Git SSH). The GitLab guest
    # redirects 2222 -> 22 with its own iptables rule, so the forward is
    # 2222 -> 2222 and the port translation happens on the guest.
    "gitlab-ssh" = { wan_port = "2222", ip = "10.0.10.153", port = "2222" }
    # wg-easy's WireGuard endpoint VIP (.99), UDP (docs/38). The admin UI is
    # NOT forwarded — it is reachable over the tunnel and the internal name.
    wg = { protocol = "udp", wan_port = "51820", ip = "10.0.10.99", port = "51820" }
  }
}
