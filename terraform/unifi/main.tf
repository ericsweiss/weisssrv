# UniFi network state as code — the UCG-Fiber gateway's VLANs, firewall zones
# and zone-based policies, WLANs, DHCP reservations, WAN port forwards and site
# settings (docs/46-unifi-network.md).
#
# The SHAPE — every resource, the `prevent_destroy` guards on networks and
# zones, the derived `matching_target`, the WPA3/PMF pairing and the hardened
# setting defaults — comes from the weisssrv-lib `unifi-network` module at the
# `?ref=` below; the inventory is this site's data in `networks.tf` (networks,
# zones, policies, clients, port forwards) and in the `wlans` map here, which
# has to name the passphrase variables.
#
# The `?ref=` is bumped BY HAND together with `variables.WEISSSRV_LIB_REF` —
# scripts/check-lib-pins.py reads only the `include:` block and
# ansible/requirements.yml — and scripts/test_site_configs.py fails the build
# when this root's pin is not equal to WEISSSRV_LIB_REF. A pin that lands before
# its tag exists is red on `terraform init` until the tag is cut, the same
# ordering every other library pin surface has.
#
# APPLY IS SUPERVISED — see README.md. This rewrites the gateway's own
# segmentation: a bad apply is not a failed pipeline, it is a LAN you cannot
# reach the controller from.
module "network" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/unifi-network?ref=v0.13.2"

  networks = local.networks
  zones    = local.zones
  policies = local.policies

  # Built-in zone DISPLAY NAMES on this controller, passed explicitly rather
  # than inherited from the module defaults: the names are locale- and
  # controller-dependent, `internal` is what the `homelab-to-internal-icmp` /
  # `internal-to-homelab-icmp` pair resolves against, and a library default
  # change must never repoint a live policy on a ref bump. Confirmed against
  # the console before the first apply (docs/46).
  builtin_zone_names = {
    internal = "Internal"
    external = "External"
    gateway  = "Gateway"
  }

  # WLANs live here rather than in networks.tf because each one names its
  # passphrase variable, and the module's whole `wlans` input is sensitive.
  # All four are WPA-PSK on 2.4 + 5 GHz — 6 GHz is not offered by the provider
  # (upstream #406), so the U7 Pro XGS's 6 GHz radio is a UI step (docs/46).
  wlans = {
    home = {
      ssid       = "TheRevengers"
      network    = "home"
      passphrase = var.wlan_passphrase_home
    }
    # Plain WPA2 with PMF disabled, and no steering off 2.4 GHz: ESP32/Kasa
    # class gear either cannot associate to a WPA3-transition BSS or drops off
    # it. `allow_2ghz_high_perf` clears UniFi's "connect high-performance
    # clients to 5 GHz only".
    iot = {
      ssid                 = "Panopticon"
      network              = "iot"
      passphrase           = var.wlan_passphrase_iot
      wpa3                 = false
      allow_2ghz_high_perf = true
    }
    # Guests reach the internet and the resolvers, never each other: the zone
    # policies stop the routed paths, `l2_isolation` stops the bridged one.
    guest = {
      ssid         = "kugel-tikka-masala"
      network      = "guest"
      passphrase   = var.wlan_passphrase_guest
      l2_isolation = true
    }
    work = {
      ssid       = "DunderMiffLAN"
      network    = "work"
      passphrase = var.wlan_passphrase_work
    }
  }

  clients       = local.clients
  port_forwards = local.port_forwards

  # The hardened posture is pinned here, not inherited: these are security
  # settings on the site's only gateway, and a library default flip must not be
  # able to re-enable UPnP or unattended firmware upgrades on a ref bump.
  # Same reasoning as terraform/cloudflare's zone_settings.
  #
  # `ips_mode` stays detection-only through the burn-in; moving it to "ips"
  # (inline blocking) is a deliberate post-burn-in step in docs/46.
  site_settings = {
    auto_upgrade         = false
    network_optimization = false
    upnp                 = false
    ips_mode             = "ids"

    # The effective IGMP-snooping toggle on Network 10.3+. Exactly the two ends
    # of the casting path, matching the per-network `igmp_snooping` fallbacks in
    # networks.tf. Homelab is deliberately out: snooping without a reliably
    # elected querier prunes groups after the membership timeout, and VLAN 10
    # has nothing multicast-critical to gain (corosync is unicast knet).
    igmp_snooping_networks = ["home", "iot"]
  }
}
