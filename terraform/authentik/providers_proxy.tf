# Forward-auth (single application) proxy providers, served by the embedded
# outpost (outpost.tf). They share one shape — only name + external_host (and
# the basic-auth block, below) vary — so they are now ONE map consumed by the
# library module's `for_each`, where they used to be ten explicit resources.
#
# WHY THEY USED TO BE EXPLICIT, and why the map is safe. The recorded rationale
# was that import.sh walks one address at a time during a DR rebuild, leaving the
# provider set only PARTIALLY in state, and that indexing a for_each map in that
# window fails with "Invalid index". That does not hold: a for_each key set is
# derived from CONFIGURATION, not state, so `…this["sonarr"]` resolves whether or
# not the object has been imported yet. What a half-imported state does produce
# is a plan full of creates for objects that already exist — which is why
# import.sh must run to completion, and `terraform plan` must be clean, before
# anything is applied. import.sh and imports.tf name the keys below.
#
# Shared shape notes:
# - property_mappings is deliberately NOT set (the module never sets it):
#   authentik auto-assigns the five default scope mappings to every proxy
#   provider, and the goauthentik provider only manages this field when it is
#   explicitly configured (its Read skips the field otherwise) — configuring it
#   would leave a permanent phantom "+ property_mappings" diff on the imported
#   state. See README "Provider quirks".
# - Basic-auth INJECTION (basic_auth_enabled true + the two *_attribute names)
#   is on for providers whose upstream keeps its own credential check: the
#   outpost reads the named attributes from the user (group attributes merge
#   into user attributes — groups.tf stores them on the app's access group)
#   and sends them as the Authorization header; the dedicated
#   authentik-auth-basic Traefik middleware forwards it upstream. Currently:
#   nzbget (nzbget_user/nzbget_password on media-admins) and the two AdGuard
#   providers (adguard_user/adguard_password on dns-admins). All other
#   providers keep injection disabled with both attribute fields empty. These
#   fields name ATTRIBUTES — never put a literal credential in them.
# - every provider carries the module's prevent_destroy: a renamed key plans as
#   destroy+create and breaks that app's forward-auth (README § Guardrails).

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
    # and has no External auth mode, so injection kills the double-login. Values
    # live on the media-admins group (groups.tf). The nzbget IngressRoute must use
    # the authentik-auth-basic middleware or the header is stripped.
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

    # AdGuard Home SSO dashboards (Terraform-authored, docs/08)
    # One provider per hostname (forward_single matches exactly one external
    # host): adguard.esweiss.com -> dns-01, adguard-02.esweiss.com -> dns-02.
    # Both inject the AdGuard admin credentials (adguard_user/adguard_password
    # attributes on the dns-admins group, from the 'AdGuard Home' 1Password item)
    # — AdGuard has no external-auth mode, so injection is what makes the SSO
    # hostnames log straight in. The raw dns-01/dns-02.esweiss.com routes and the
    # direct IPs stay untouched as the cluster-outage break-glass path.
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
  }

  proxy_providers = {
    for key, provider in local.proxy_provider_data :
    key => merge(local.proxy_provider_defaults, provider)
  }
}
