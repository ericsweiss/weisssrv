# Site data only: the VLAN, zone and policy inventory this gateway serves. The
# resource shape lives in the library module pinned in main.tf. Map keys are
# state addresses, so a rename needs a `moved {}` block (README § Guardrails).
locals {
  # The two AdGuard/Unbound resolvers (docs/08-dns.md), handed to every client
  # VLAN by DHCP. Renumbering VLAN 10 moves these locals, the `homelab` subnet
  # and every `port_forwards` target together (README § Apply).
  dns_ips = ["10.0.10.150", "10.0.10.160"]
  plex_ip = "10.0.10.152"
  # Home Assistant (docs/24). Named here because the IoT VLAN gets a policy to
  # it: cast/TTS and webhook callbacks are device-initiated, so the outbound
  # `homelab-to-iot` allow does not cover them.
  ha_ip = "10.0.10.154"
  # Traefik's public MetalLB VIP — the target of the 80/443 WAN port forwards
  # below and of the `homelab-to-homelab-hairpin` allow.
  traefik_public_vip = "10.0.10.100"

  # The two adopted UniFi devices on the management VLAN. Also named by the ICMP
  # policy pair below, their client reservations, and the blackbox probes in
  # kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml.
  mgmt_device_ips = ["10.0.1.2", "10.0.1.3"]

  # `subnet` is GATEWAY form: the host part is the gateway address, so
  # "10.0.30.1/24" means the network 10.0.30.0/24 with the gateway on .1. The
  # network-address form hands every DHCP client .0 (docs/46 § Networks).
  networks = {
    # The built-in network (VLAN 1, untagged): UniFi management only, no `vlan`,
    # imported rather than created (README § Adopting the live site). DHCP hands
    # out public resolvers to avoid a bootstrap loop (docs/46 § Networks).
    default = {
      name        = "Default"
      subnet      = "10.0.1.1/24"
      domain_name = "esweiss.com"
      dhcp        = { start = "10.0.1.100", stop = "10.0.1.199", dns_servers = ["1.1.1.1", "9.9.9.9"] }
    }

    # Hosts, guests, k3s nodes and every MetalLB/kube-vip VIP. The pool stays
    # below .99 so the VIPs (.99-.101, .161, .162) and the statically addressed
    # hosts and guests are never handed out by DHCP.
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

    # Per-network IGMP snooping covers older controllers; on Network 10.3+ the
    # effective toggle is `site_settings.igmp_snooping_networks` in main.tf,
    # listing the same two networks (docs/46 § Site settings).
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

    # `purpose` stays "corporate": the controller rewrites `guest` outside its
    # own Hotspot zone and the apply fails inconsistent-result. Isolation is the
    # custom zone plus the WLAN's `l2_isolation` (docs/46 § Networks).
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

  # One zone per VLAN, inter-zone default deny, so every allowance is a
  # `policies` entry below and nothing depends on rule order. Keys are the
  # controller display names; the management VLAN stays in built-in Internal.
  zones = {
    homelab = { networks = ["homelab"] }
    home    = { networks = ["home"] }
    iot     = { networks = ["iot"] }
    guest   = { networks = ["guest"] }
    work    = { networks = ["work"] }
  }

  # ALLOW entries open holes in the inter-zone default deny; BLOCK entries
  # narrow the two paths UniFi allows by default (every zone to External, every
  # zone to Gateway). The full matrix is docs/46 § Zones and policies.
  policies = [
    # Trusted household network reaches the homelab as it does today; per-port
    # enforcement stays with the Proxmox firewall and the k8s NetworkPolicies,
    # which is where it is already reviewed.
    {
      name        = "home-to-homelab"
      source      = { zone = "home" }
      destination = { zone = "homelab" }
    },
    # Casting and control: the AirPlay/Chromecast data path to the TVs and
    # speakers on IoT. Discovery is multicast and does not cross the VLAN
    # boundary (docs/46 § Codified vs manual).
    {
      name        = "home-to-iot"
      source      = { zone = "home" }
      destination = { zone = "iot" }
    },
    # Home Assistant (.154) is the only VLAN 10 consumer of IoT. Source-scoped
    # to it because every k3s pod SNATs to a VLAN 10 address and the IoT device
    # APIs are unauthenticated.
    {
      name        = "homelab-to-iot"
      source      = { zone = "homelab", ips = [local.ha_ip] }
      destination = { zone = "iot" }
    },
    # Plex -> HDHomeRun (10.0.20.200) and Home Assistant -> the TVs. Source-
    # scoped to those two for the same reason as `homelab-to-iot` above.
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
    # Home Assistant's device-initiated paths, which `homelab-to-iot` does not
    # cover: a Cast speaker fetching the TTS proxy URL, and integration webhooks
    # posting back to HA's internal_url. Both are :8123 to .154.
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
    # `internal` is a `builtin_zone_names` key: the management VLAN sits in the
    # built-in Internal zone. The pair carries the blackbox ICMP probes both
    # ways, because the controller rejects `create_allow_respond` on icmp.
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
    # Hairpin NAT for grey-cloud names: a homelab source dialing the WAN address
    # is DNAT'd back into this zone, which would otherwise hit the intra-zone
    # Block All. Only hairpinned flows can match (docs/46 row 12).
    {
      name        = "homelab-to-homelab-hairpin"
      protocol    = "tcp"
      source      = { zone = "homelab" }
      destination = { zone = "homelab", ips = [local.traefik_public_vip], port = "80,443" }
    },

    # Gateway hardening: every zone reaches Gateway by default, and on a Cloud
    # Gateway the console answers on each VLAN's own gateway address. All tcp
    # rather than a port list; DHCP, NTP and ICMP are unaffected (docs/46).
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
    # The trusted-VLAN half: home and homelab keep exactly :443 (console UI,
    # the terraform/CI API path, the `router.esweiss.com` backend) and lose all
    # other tcp. The residual reach to :443 is accepted in docs/46.
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

    # DNS containment: DHCP option 6 is a suggestion, so these BLOCKs close both
    # default-allow bypasses, a public resolver through External and the
    # gateway's own forwarder. DoH on :443 is not covered (docs/46).
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
    # because DNS is udp first; DHCP (udp 67/68) is untouched. These VLANs get
    # .150/.160 from DHCP as their only resolvers.
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

  # New entries: MAC in lowercase (ForceNew and case-sensitive, enforced by
  # scripts/test_terraform_roots.py) and outside the pool in local.networks.
  # Changing one is a -replace (README § Changing one is a replace).
  clients = {
    # Home (VLAN 20)
    hdhr = {
      mac      = "00:18:dd:0a:37:45"
      name     = "hdhr"
      fixed_ip = "10.0.20.200"
      network  = "home"
    }
    # CRITICAL: the admin workstation the Proxmox firewall's `admin_lan`
    # 10.0.20.8/29 block is sized for. The MAC is the macOS per-network "Fixed"
    # private Wi-Fi address, regenerated if the network is forgotten and
    # rejoined. Then the MacBook drops out of the /29 and loses SSH, :8006,
    # :6443 and RDP at once: re-read it from the controller and `-replace` this
    # entry, with Tailscale as the way back in (docs/46 § DHCP reservations).
    macbook = {
      mac      = "a2:30:58:e7:62:f2"
      name     = "macbook"
      fixed_ip = "10.0.20.10"
      network  = "home"
    }

    # No dock entry: 9c:7b:ef:9e:e6:46 is the DOCK's MAC, shared by every docked
    # laptop. Per-laptop wired steering needs MAC-passthrough in firmware (docs/16).

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
    # Wired-only Pi that tags itself into VLAN 30 (NM eth0.30), so the override's
    # tagged delivery is exactly what it consumes (docs/46).
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
    # Levoit appliances. These entries are what holds them on IoT: a reservation
    # names the VLAN a wireless client joins regardless of the SSID it
    # associates with.
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

    # TVs and streaming devices on IoT: home-to-iot and homelab-to-iot are full
    # allows, the reverse only DNS/Plex/HA. vizio-cast-display stays on Home,
    # wired behind a tag-unaware switch chain. Keys are reported hostnames.
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

  # WAN port forwards on the primary WAN, any source: source restriction lives
  # in the Proxmox firewall and the k8s NetworkPolicies. The map key is the
  # forward's name in the UI; targets are homelab addresses.
  port_forwards = {
    # Traefik's public MetalLB VIP — every *.ericsweiss.com name.
    http  = { wan_port = "80", ip = local.traefik_public_vip, port = "80" }
    https = { wan_port = "443", ip = local.traefik_public_vip, port = "443" }
    # The three forwards below carry `logging = true`: 80/443 have Traefik's
    # access log, these have no application-level record of a WAN hit.
    #
    # Plex direct-connect (docs/20). The LXC is not behind Traefik.
    plex = { wan_port = "32400", ip = local.plex_ip, port = "32400", logging = true }
    # Git-over-SSH, deliberately WAN-open (docs/27 § Git SSH). The GitLab guest
    # redirects 2222 -> 22 with its own iptables rule, so the forward is
    # 2222 -> 2222 and the port translation happens on the guest.
    "gitlab-ssh" = { wan_port = "2222", ip = "10.0.10.153", port = "2222", logging = true }
    # wg-easy's WireGuard endpoint VIP (.99), UDP (docs/38). The admin UI is not
    # forwarded. Same VIP as `cluster_wg_easy_vip` in cluster-config.yaml and
    # `wg_easy_vip` in group_vars/all.yml; move all three together.
    wg = { protocol = "udp", wan_port = "51820", ip = "10.0.10.99", port = "51820", logging = true }
  }
}
