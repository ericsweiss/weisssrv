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
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/unifi-network?ref=v0.15.0"

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
  # All four are WPA-PSK. `bands` sets each SSID's radio set explicitly
  # (lib v0.14.0). The two WPA3 SSIDs carry 6 GHz; Panopticon (WPA2, no 6 GHz
  # radio) and guest are ["2g","5g"]. All four are pinned rather than left
  # null: this provider's `wlan_bands` is Optional+Computed, but a null on an
  # EXISTING resource does not release ownership cleanly — the plan reconciles
  # to the provider's 2g/5g default and strips a console-set 6g. Pinning the
  # exact live set is what actually holds it: config equals the console value,
  # so no write is issued and upstream #406 (which fails "6g" only at CREATE)
  # never triggers. If a future apply must CREATE one of these WLANs from
  # scratch while #406 stands, drop 6g from that create, apply, then re-add it.
  wlans = {
    home = {
      ssid       = "TheRevengers"
      network    = "home"
      passphrase = var.wlan_passphrase_home
      bands      = ["2g", "5g", "6g"]
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
      bands                = ["2g", "5g"]
    }
    # Guests reach the internet and the resolvers, never each other: the zone
    # policies stop the routed paths, `l2_isolation` stops the bridged one.
    guest = {
      ssid         = "kugel-tikka-masala"
      network      = "guest"
      passphrase   = var.wlan_passphrase_guest
      l2_isolation = true
      bands        = ["2g", "5g"]
    }
    work = {
      ssid       = "DunderMiffLAN"
      network    = "work"
      passphrase = var.wlan_passphrase_work
      bands      = ["2g", "5g", "6g"]
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
    # DELIBERATELY TRUE (operator ruling, 2026-08-30): the switch and AP take
    # firmware nightly at 1 AM — hands-off patching for the Wi-Fi gear was
    # chosen over the repo's pin-everything default when the audit surfaced
    # the drift. This covers DEVICE firmware only; console/application
    # updates are a separate console-owned surface (docs/46 § Codified vs
    # manual).
    auto_upgrade         = true
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
