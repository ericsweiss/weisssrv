# CRITICAL: apply is SUPERVISED (README.md). This rewrites the UCG-Fiber
# gateway's own segmentation — VLANs, firewall zones and policies, WLANs, DHCP
# reservations, WAN port forwards, site settings — so a bad apply is a LAN you
# cannot reach the controller from, not a failed pipeline.
# The shape comes from the weisssrv-lib `unifi-network` module at the `?ref=`
# below; the inventory is site data in networks.tf and the `wlans` map here.
# The `?ref=` is bumped by hand with `variables.WEISSSRV_LIB_REF`.
# Segmentation and what stays console-owned: docs/46-unifi-network.md.
module "network" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/unifi-network?ref=v0.17.1"

  networks = local.networks
  zones    = local.zones
  policies = local.policies

  # Built-in zone DISPLAY NAMES on this controller, pinned rather than inherited:
  # they are locale- and controller-dependent, and a library default change must
  # not repoint a live policy on a ref bump (docs/46).
  builtin_zone_names = {
    internal = "Internal"
    external = "External"
    gateway  = "Gateway"
  }

  # WLANs live here rather than in networks.tf because each one names its
  # passphrase variable, and the module's whole `wlans` input is sensitive.
  # `bands` pins each SSID's live radio set (README.md § Provider quirks).
  wlans = {
    home = {
      ssid       = "TheRevengers"
      network    = "home"
      passphrase = var.wlan_passphrase_home
      bands      = ["2g", "5g", "6g"]
    }
    # Plain WPA2 with PMF disabled and no steering off 2.4 GHz: ESP32/Kasa class
    # gear cannot hold a WPA3-transition BSS. `allow_2ghz_high_perf` clears
    # UniFi's "connect high-performance clients to 5 GHz only".
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

  # Hardened posture pinned here, not inherited: a library default flip must not
  # re-enable UPnP or auto-firmware on a ref bump. `ips_mode = "ips"` is inline
  # blocking and create-time only; day-2 mode is console-owned (docs/46).
  site_settings = {
    # Device firmware is hands-off: the switch and AP take it nightly. Console
    # and application updates are a separate, console-owned surface (docs/46).
    auto_upgrade         = true
    network_optimization = false
    upnp                 = false
    ips_mode             = "ips"

    # The effective IGMP-snooping toggle on Network 10.3+: the two ends of the
    # casting path, matching the per-network `igmp_snooping` fallbacks in
    # networks.tf. Homelab is out — no elected querier, nothing to gain.
    igmp_snooping_networks = ["home", "iot"]
  }
}
