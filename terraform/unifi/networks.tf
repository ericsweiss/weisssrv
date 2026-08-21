# Site data — the VLAN/zone/policy inventory this gateway serves. The SHAPE
# (every resource, its `prevent_destroy` guard, the derived `matching_target`,
# the hardened setting defaults) lives in the library module pinned in main.tf;
# what follows is data only.
#
# Map keys are STATE ADDRESSES and the name every other map references, so a
# rename is a `moved {}` block — `unifi_network` and `unifi_firewall_zone` carry
# `prevent_destroy` module-side and refuse the destroy half of a rename.
locals {
  # The two AdGuard/Unbound resolvers (docs/04-dns.md). Handed to every VLAN by
  # DHCP and allowed through the zone policies below from the VLANs that get
  # nothing else. Phase 2 (homelab renumber) edits these two addresses and
  # `plex_ip` — nothing else in this file names a homelab host.
  dns_ips = ["192.168.0.150", "192.168.0.160"]
  plex_ip = "192.168.0.152"

  # `subnet` is GATEWAY form: the host part is the gateway address, so
  # "10.0.30.1/24" is the network 10.0.30.0/24 with the gateway on .1. The
  # module rejects the network-address form — writing "10.0.30.0/24" applies
  # cleanly and hands every DHCP client .0 as its gateway.
  networks = {
    # The controller's built-in network (VLAN 1, untagged): UniFi management
    # only — the gateway, the switch and the AP. It carries no `vlan`, which is
    # what marks it as the built-in, and it is IMPORTED rather than created
    # (`terraform import ... name=Default`, README § Adopting the live site).
    default = {
      name        = "Default"
      subnet      = "10.0.1.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "10.0.1.100", stop = "10.0.1.199", dns_servers = local.dns_ips }
    }

    # Phase 1 keeps the homelab on its existing 192.168.0.0/24 so nothing in
    # ansible/, kubernetes/ or any guest has to be renumbered on cutover night;
    # the VLAN tag is new. Phase 2 flips this one subnet to 10.0.10.1/24 (pool
    # .2-.98) in its own MR — docs/46 § Phase 2.
    homelab = {
      name        = "Homelab"
      vlan        = 10
      subnet      = "192.168.0.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "192.168.0.2", stop = "192.168.0.98", dns_servers = local.dns_ips }
    }

    home = {
      name        = "Home"
      vlan        = 20
      subnet      = "10.0.20.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "10.0.20.50", stop = "10.0.20.249", dns_servers = local.dns_ips }
    }

    # Per-network IGMP snooping is set here for older controllers; the effective
    # toggle on Network 10.3+ is `site_settings.igmp_snooping_networks` in
    # main.tf, which lists this network too.
    iot = {
      name          = "IoT"
      vlan          = 30
      subnet        = "10.0.30.1/24"
      domain_name   = "esweiss.com"
      igmp_snooping = true
      dhcp          = { start = "10.0.30.50", stop = "10.0.30.249", dns_servers = local.dns_ips }
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

  # Allowances against the default deny. `name` is the state address AND the
  # rule name in the UI, so it must be unique and a rename replaces the policy.
  # `create_allow_respond` (default true) writes the matching established/
  # related return rule; the controller REJECTS it for icmp/icmpv6, and nothing
  # here is ICMP.
  #
  # Everything NOT listed is denied: iot->home, iot->work, work->anything but
  # DNS, guest->anything but DNS, home->work, and every reverse direction that
  # is not a stateful response.
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
    # has no reflector at all (docs/46 § Discovery).
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
    # same namespace. Without this the switch and the AP cannot resolve names.
    {
      name        = "internal-to-homelab-dns"
      protocol    = "tcp_udp"
      source      = { zone = "internal" }
      destination = { zone = "homelab", ips = local.dns_ips, port = "53" }
    },
  ]

  # Fixed-IP reservations. Every entry ADOPTS the client the controller already
  # knows (`allow_existing` is module-side), so these do not create anything.
  #
  # CHANGING ONE IS A REPLACE, not an update: upstream #428 fails every in-place
  # `unifi_client` update with "inconsistent result after apply: .last_ip". Use
  # `terraform apply -replace='module.network.unifi_client.this["<key>"]'`; the
  # device is untouched, only the controller-side object is recreated.
  #
  # Homelab hosts and guests are statically addressed by Ansible and are
  # deliberately absent — a reservation for an address the host also configures
  # itself is two sources of truth for one address.
  clients = {
    # Home (VLAN 20)
    hdhr = {
      mac      = "00:18:DD:0A:37:45"
      name     = "hdhr"
      fixed_ip = "10.0.20.200"
      network  = "home"
    }
    eric-bedroom-hyperion = {
      mac      = "B8:27:EB:17:7D:DC"
      name     = "eric-bedroom-hyperion"
      fixed_ip = "10.0.20.211"
      network  = "home"
    }

    # IoT (VLAN 30)
    hue = {
      mac      = "00:17:88:7E:C7:A2"
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

    # Management (VLAN 1) — the switch and the AP, so the blackbox ICMP probes
    # in kubernetes/infrastructure/observability have stable targets. Their MACs
    # are unknown until the devices are adopted: read each one from the
    # controller (Devices -> the device -> MAC) and uncomment, then apply. Until
    # then the two addresses are held by the DHCP pool's first leases, which is
    # why the probe targets are documented in docs/46 as pending.
    #
    # usw-pro-xg-8 = {
    #   mac      = "00:00:00:00:00:00"
    #   name     = "usw-pro-xg-8"
    #   fixed_ip = "10.0.1.2"
    #   network  = "default"
    # }
    # u7-pro-xgs = {
    #   mac      = "00:00:00:00:00:00"
    #   name     = "u7-pro-xgs"
    #   fixed_ip = "10.0.1.3"
    #   network  = "default"
    # }

    # The admin workstation's reservation, which the Proxmox firewall's
    # `admin_lan` 10.0.20.8/29 block is sized for. MAC unknown until the machine
    # associates with the new Home SSID — read it from the controller's client
    # list and uncomment (docs/46 § Cutover).
    #
    # macbook = {
    #   mac      = "00:00:00:00:00:00"
    #   name     = "macbook"
    #   fixed_ip = "10.0.20.10"
    #   network  = "home"
    # }
  }

  # WAN port forwards. Every one is on the primary WAN and accepts any source:
  # source restriction lives in the Proxmox firewall and the k8s
  # NetworkPolicies, not in a per-forward allowlist nothing else can see.
  #
  # The map key is the forward's name in the UI. Targets are homelab addresses,
  # so Phase 2 renumbers all five.
  port_forwards = {
    # Traefik's public MetalLB VIP (192.168.0.100) — every *.ericsweiss.com name.
    http  = { wan_port = "80", ip = "192.168.0.100", port = "80" }
    https = { wan_port = "443", ip = "192.168.0.100", port = "443" }
    # Plex direct-connect (docs/20). The LXC is not behind Traefik.
    plex = { wan_port = "32400", ip = local.plex_ip, port = "32400" }
    # Git-over-SSH, deliberately WAN-open (docs/27 § Git SSH). The GitLab guest
    # redirects 2222 -> 22 with its own iptables rule, so the forward is
    # 2222 -> 2222 and the port translation happens on the guest.
    "gitlab-ssh" = { wan_port = "2222", ip = "192.168.0.153", port = "2222" }
    # wg-easy's WireGuard endpoint VIP (.99), UDP (docs/38). The admin UI is
    # NOT forwarded — it is reachable over the tunnel and the internal name.
    wg = { protocol = "udp", wan_port = "51820", ip = "192.168.0.99", port = "51820" }
  }
}
