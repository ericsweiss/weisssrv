# Forward-auth proxy providers served by the embedded outpost (outpost.tf).
# Each entry is name, external_host and optional basic-auth attribute names,
# consumed by the library module's for_each. README § Basic-auth injection.

locals {
  # Shared posture, pinned here so a library default change cannot rewrite live
  # session behaviour on a ref bump.
  proxy_provider_defaults = {
    mode                  = "forward_single"
    intercept_header_auth = true

    internal_host                = ""
    internal_host_ssl_validation = true
    skip_path_regex              = ""
    cookie_domain                = ""

    basic_auth_enabled            = false
    basic_auth_username_attribute = ""
    basic_auth_password_attribute = ""

    access_token_validity  = "hours=24"
    refresh_token_validity = "days=30"
  }

  proxy_provider_data = {
    sonarr = {
      name          = "Sonarr"
      external_host = "https://tv.esweiss.com"
    }

    radarr = {
      name          = "Radarr"
      external_host = "https://movies.esweiss.com"
    }

    lidarr = {
      name          = "Lidarr"
      external_host = "https://music.esweiss.com"
    }

    qbittorrent = {
      name          = "qBittorrent"
      external_host = "https://qbittorrent.esweiss.com"
    }

    # NZBGet validates HTTP Basic against its own ControlUsername/ControlPassword
    # and has no External auth mode, so injection kills the double-login. Its
    # IngressRoute must use the authentik-auth-basic middleware.
    nzbget = {
      name          = "NZBGet"
      external_host = "https://nzbget.esweiss.com"

      basic_auth_enabled            = true
      basic_auth_username_attribute = "nzbget_user"
      basic_auth_password_attribute = "nzbget_password"
    }

    prowlarr = {
      name          = "Prowlarr"
      external_host = "https://prowlarr.esweiss.com"
    }

    pulsarr = {
      name          = "Pulsarr"
      external_host = "https://pulsarr.esweiss.com"
    }

    wireguard_easy = {
      name          = "WireGuard Easy"
      external_host = "https://vpn.esweiss.com"
    }

    # AdGuard Home SSO dashboards, one provider per hostname (forward_single
    # matches exactly one host). Both inject the admin credentials held on the
    # dns-admins group; the raw dns-01/dns-02 routes are break-glass (docs/08).
    adguard_01 = {
      name          = "AdGuard Home dns-01"
      external_host = "https://adguard.esweiss.com"

      basic_auth_enabled            = true
      basic_auth_username_attribute = "adguard_user"
      basic_auth_password_attribute = "adguard_password"
    }

    adguard_02 = {
      name          = "AdGuard Home dns-02"
      external_host = "https://adguard-02.esweiss.com"

      basic_auth_enabled            = true
      basic_auth_username_attribute = "adguard_user"
      basic_auth_password_attribute = "adguard_password"
    }

    # Traefik dashboard (api@internal). No injection: the dashboard has no
    # backend login, so forward-auth is its only identity gate. The route also
    # keeps lan-tailscale-strict in front of it.
    traefik_dashboard = {
      name          = "Traefik Dashboard"
      external_host = "https://traefik.esweiss.com"
    }

    # Uptime Kuma admin UI, internal hostname only: the external status router
    # publishes the read-only status page and has no admin path to gate. No
    # injection; Kuma's own login form is not HTTP Basic (docs/45).
    uptime_kuma = {
      name          = "Uptime Kuma"
      external_host = "https://status.esweiss.com"
    }
  }

  proxy_providers = {
    for key, provider in local.proxy_provider_data :
    key => merge(local.proxy_provider_defaults, provider)
  }
}
