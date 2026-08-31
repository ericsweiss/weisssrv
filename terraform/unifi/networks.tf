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
  # Traefik's public MetalLB VIP — the target of the 80/443 port forwards in
  # main.tf and of the `homelab-to-homelab-hairpin` allow below.
  traefik_public_vip = "10.0.10.100"

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
    # device on VLAN 30, over a different protocol per integration. SOURCE-
    # SCOPED to HA alone (2026-08 audit ZBF-04): a zone-wide allow is inherited
    # by every k3s pod — they SNAT to node addresses in VLAN 10 — handing the
    # internet-facing workloads full reach into a VLAN whose device APIs are
    # unauthenticated by construction (Kasa :9999, WLED :80, Hyperion :19444).
    # A repo grep plus the live Uptime Kuma monitor list confirm HA is the only
    # IoT consumer on VLAN 10.
    {
      name        = "homelab-to-iot"
      source      = { zone = "homelab", ips = [local.ha_ip] }
      destination = { zone = "iot" }
    },
    # Plex -> HDHomeRun (10.0.20.200) and Home Assistant -> the TVs. Source-
    # scoped to those two for the same reason as `homelab-to-iot` above; ad-hoc
    # diagnostics from other homelab hosts ride ICMP or get a temporary console
    # rule, not a standing zone-wide allowance.
    {
      name        = "homelab-to-home"
      source      = { zone = "homelab", ips = [local.ha_ip, local.plex_ip] }
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
    # Hairpin NAT for grey-cloud names (2026-08 audit ZBF-01). A homelab source
    # dialing the WAN address is DNAT'd to the Traefik VIP, so the flow lands
    # back in this zone and the `homelab -> homelab` matrix cell — which is the
    # predefined Block All. From every OTHER VLAN hairpin works (their cells
    # carry allows); from homelab it timed out, which is what took the
    # in-cluster probes of photos/ide.git down post-renumber (docs/08
    # § Cross-domain rewrites is the primary fix; this closes the residual
    # class). Intra-VLAN traffic never traverses the gateway, so the ONLY flows
    # this can match are hairpin ones. If homelab-sourced hairpin still fails
    # with this in place, the blocker is same-subnet SNAT — a UniFi limitation
    # with no knob — and the rewrite path is the complete answer; record the
    # observed result in docs/46 either way.
    {
      name        = "homelab-to-homelab-hairpin"
      protocol    = "tcp"
      source      = { zone = "homelab" }
      destination = { zone = "homelab", ips = [local.traefik_public_vip], port = "80,443" }
    },

    # ---- Gateway hardening ----
    #
    # UniFi allows every zone -> Gateway by default, and on a Cloud Gateway the
    # console (and therefore the Network application, and therefore the local
    # admin whose API key rewrites this whole file) is a gateway service on
    # EVERY VLAN's own gateway address. A guest with the WLAN PSK otherwise gets
    # an unthrottled login form at https://10.0.40.1.
    #
    # tcp only, but ALL of tcp (2026-08 audit ZBF-02): a port scan found the
    # controller answering on 8080 (device-inform), 8443/8843/8880 (embedded
    # Tomcat/portal) and 6789 (throughput test) beyond the 22/80/443 the
    # original list named — nothing on these three VLANs has a legitimate TCP
    # need to its gateway address, so the rule stops naming ports and stops
    # needing re-audit every time Ubiquiti opens another listener. DHCP is
    # broadcast before the client has an address and is unaffected, NTP is udp,
    # and ICMP to the gateway stays up for troubleshooting. The gateway's own
    # DNS forwarder is a separate default-allow path and is fenced by the
    # `*-to-gateway-dns` BLOCKs below (udp included there).
    {
      name        = "guest-to-gateway-mgmt"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "guest" }
      destination = { zone = "gateway" }
    },
    {
      name        = "iot-to-gateway-mgmt"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "iot" }
      destination = { zone = "gateway" }
    },
    {
      name        = "work-to-gateway-mgmt"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "work" }
      destination = { zone = "gateway" }
    },
    # The trusted-VLAN half (2026-08 audit ZBF-07/GW-05/ADM-07): home and
    # homelab keep exactly :443 — the console UI from admin devices, the
    # terraform/CI API path, and the `router.esweiss.com` IngressRoute backend
    # (kubernetes/apps/vm-ingress/router.yaml, gateway :443 via the
    # unifi-self-signed transport) all ride it — and lose ALL other tcp. The
    # range is the complement of 443 rather than an enumerated listener list
    # (22, 80, inform/Tomcat/throughput), so a gateway service opened by a
    # future firmware is fenced without a rule edit — the same reasoning the
    # guest/iot/work blocks use, applied to the trusted side. Nothing on home
    # or homelab has a legitimate non-443 tcp need to the gateway: DHCP/NTP are
    # udp, homelab resolves via .150/.160 not the gateway, and :22 has no
    # listener. The residual — any homelab pod can still reach the console
    # login on 443 — is accepted and recorded in docs/46; the scoped API key
    # (audit ADM-01) is the control that matters there.
    {
      name        = "home-to-gateway-extras"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "home" }
      destination = { zone = "gateway", port = "1-442,444-65535" }
    },
    {
      name        = "homelab-to-gateway-extras"
      action      = "BLOCK"
      protocol    = "tcp"
      logging     = true
      source      = { zone = "homelab" }
      destination = { zone = "gateway", port = "1-442,444-65535" }
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
    # "the weisssrv resolvers or nothing" true on all four client VLANs — home
    # included since the 2026-08 audit (ZBF-03): it was silently exempt, and a
    # Home device hard-coding 8.8.8.8 lost split-horizon without a trace. The
    # diagnostic signature of these blocks is a device that resolves nothing:
    # point it back at DHCP.
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
    {
      name        = "home-to-external-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "home" }
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
    {
      name        = "home-to-gateway-dns"
      action      = "BLOCK"
      protocol    = "tcp_udp"
      logging     = true
      source      = { zone = "home" }
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
  # Kasa plugs reached IoT at cutover without ever joining the IoT SSID (now `Panopticon`). Steering
  # is placement, not authorization: a device that keeps the Home PSK falls back
  # to Home if its MAC ever stops matching, so IoT-class devices still get
  # re-onboarded onto `Panopticon` over time (docs/46 § DHCP reservations).
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
  # (Historical: this root was frozen while the module sat at v0.13.0, whose
  # `setting_preference = "auto"` default reset the manual DHCP fields
  # (dns_enabled, domain_name) on every network write. Resolved — the finishing
  # supervised apply ran 2026-08-30 and the plan is no-changes (docs/46
  # § Post-cutover checklist). The one move that needed the `-replace` above,
  # `eric-bedroom-hyperion` from home/.20.211 to iot/.30.211, is done.)
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

    # NO dock steering entry: 9c:7b:ef:9e:e6:46 turned out to be the DOCK's
    # own MAC, shared by whichever laptop is docked — steering it to Work
    # dropped the personal MacBook's wired leg into Work-VLAN limbo the next
    # time it docked (2026-08-30). Per-laptop wired steering through this dock
    # is impossible at L2 unless MAC-passthrough is enabled in each laptop's
    # firmware so the dock presents the host's MAC (docs/16). Until then the
    # work laptop's isolation is DunderMiffLAN when wireless.

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
    # RESOLVED (2026-08-30): the Pi (2B v1.1, wired-only) now tags ITSELF
    # into VLAN 30 — an NM-native eth0.30 vlan connection on the fresh
    # HyperBian, with eth0 left address-less as the carrier. Tagged delivery
    # on port 7 is therefore exactly what the device consumes, so this entry's
    # override and reservation are correct again. The earlier findings stand
    # for TAG-UNAWARE devices behind a shared port: an override always
    # delivers tagged, natively-untagged clients black-hole (two clean
    # experimental runs); a managed switch at the drop remains the fix for
    # such devices (docs/16).
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
    # WIRED on the MoCA leg behind the tag-unaware dumb-switch chain — the
    # 2026-08 audit (PORT-06) caught the anticipated failure ACTUALLY happening:
    # steered onto IoT it sat on APIPA for hours, because an override forces
    # tagged delivery a TV cannot decode. It stays on native Home (address
    # outside the .50-.199 pool) until the USW Flex Mini plan (docs/16) gives
    # that drop a managed port; the rest are wireless and move on their next
    # association.
    vizio-cast-display = {
      mac      = "3c:9b:d6:7a:36:a3"
      name     = "vizio-cast-display"
      fixed_ip = "10.0.20.218"
      network  = "home"
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
