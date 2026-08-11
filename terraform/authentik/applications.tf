# Applications — the tiles on the authentik library page plus their provider
# bindings. One map entry per app, keyed by slug (which is also the OIDC issuer
# path). Adding an app = one entry here + its provider; see README "Adding a new
# application".
#
# Access is enforced by the group policy bindings in policy_bindings.tf. Roles
# beyond access (admin vs user) are resolved inside each app from its group
# claims — see that app's doc.

locals {
  applications = {
    # Home
    bar = {
      name        = "Bar Assistant"
      group       = "Home"
      provider_id = authentik_provider_oauth2.bar_assistant.id
      launch_url  = "https://bar.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/bar-assistant.svg"
    }
    cloud = {
      name        = "Nextcloud"
      group       = "Home"
      provider_id = authentik_provider_oauth2.nextcloud.id
      launch_url  = "https://cloud.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/nextcloud.svg"
    }
    food = {
      name        = "Mealie"
      group       = "Home"
      provider_id = authentik_provider_oauth2.mealie.id
      launch_url  = "https://food.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/mealie.svg"
    }
    home = {
      name        = "Home Assistant"
      group       = "Home"
      provider_id = authentik_provider_oauth2.home_assistant.id
      launch_url  = "https://home.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/home-assistant.svg"
    }
    photos = {
      name        = "immich"
      group       = "Home"
      provider_id = authentik_provider_oauth2.immich.id
      launch_url  = "https://photos.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/immich.svg"
    }

    # Software
    agent = {
      name        = "Hermes"
      group       = "Software"
      provider_id = authentik_provider_oauth2.hermes_dashboard.id
      launch_url  = "https://agent.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/selfhst/icons/png/hermes-agent.png"
    }
    git = {
      name        = "GitLab"
      group       = "Software"
      provider_id = authentik_provider_saml.gitlab.id
      launch_url  = "https://git.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/gitlab.svg"
    }
    grafana = {
      name        = "Grafana"
      group       = "Software"
      provider_id = authentik_provider_oauth2.grafana.id
      launch_url  = "https://grafana.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/grafana.svg"
    }
    vpn = {
      name        = "WireGuard Easy"
      group       = "Software"
      provider_id = authentik_provider_proxy.wireguard_easy.id
      launch_url  = "https://vpn.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/wireguard.svg"
    }

    # Downloads
    movies = {
      name        = "Radarr"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.radarr.id
      launch_url  = "https://movies.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/radarr.svg"
    }
    music = {
      name        = "Lidarr"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.lidarr.id
      launch_url  = "https://music.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/lidarr.svg"
    }
    nzbget = {
      name        = "NZBGet"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.nzbget.id
      launch_url  = "https://nzbget.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/nzbget.svg"
    }
    prowlarr = {
      name        = "Prowlarr"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.prowlarr.id
      launch_url  = "https://prowlarr.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/prowlarr.svg"
    }
    pulsarr = {
      name        = "Pulsarr"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.pulsarr.id
      launch_url  = "https://pulsarr.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/pulsarr.svg"
    }
    qbittorrent = {
      name        = "qBittorrent"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.qbittorrent.id
      launch_url  = "https://qbittorrent.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/qbittorrent.svg"
    }
    tv = {
      name        = "Sonarr"
      group       = "Downloads"
      provider_id = authentik_provider_proxy.sonarr.id
      launch_url  = "https://tv.esweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/sonarr.svg"
    }
  }
}

resource "authentik_application" "app" {
  for_each = local.applications

  name              = each.value.name
  slug              = each.key
  group             = each.value.group
  protocol_provider = each.value.provider_id

  meta_launch_url  = each.value.launch_url
  meta_icon        = each.value.meta_icon
  meta_description = ""
  meta_publisher   = ""
  meta_hide        = false

  open_in_new_tab    = true
  policy_engine_mode = "any"

  lifecycle {
    # The slug is the OIDC issuer path, so a map-key rename would plan as
    # destroy+create and break that app's logins. Removal is a two-step change:
    # drop this block, then the entry.
    prevent_destroy = true
  }
}

# AdGuard Home SSO dashboards — one application per forward-auth provider
# (forward_single matches exactly one external host; docs/08). Library group
# "Software", with the other operator dashboards.

resource "authentik_application" "adguard_01" {
  name              = "AdGuard Home dns-01"
  slug              = "adguard-01"
  group             = "Software"
  protocol_provider = authentik_provider_proxy.adguard_01.id

  meta_launch_url  = "https://adguard.esweiss.com"
  meta_icon        = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/adguard-home.svg"
  meta_description = ""
  meta_publisher   = ""
  meta_hide        = false

  open_in_new_tab    = true
  policy_engine_mode = "any"

  lifecycle {
    prevent_destroy = true
  }
}

resource "authentik_application" "adguard_02" {
  name              = "AdGuard Home dns-02"
  slug              = "adguard-02"
  group             = "Software"
  protocol_provider = authentik_provider_proxy.adguard_02.id

  meta_launch_url  = "https://adguard-02.esweiss.com"
  meta_icon        = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/adguard-home.svg"
  meta_description = ""
  meta_publisher   = ""
  meta_hide        = false

  open_in_new_tab    = true
  policy_engine_mode = "any"

  lifecycle {
    prevent_destroy = true
  }
}

# Homarr dashboard. Slug `dashboard` -> issuer path /application/o/dashboard/
# (matches homarr AUTH_OIDC_ISSUER). Library group "Home", alongside the other
# household-facing tiles.
resource "authentik_application" "homarr" {
  name              = "Homarr"
  slug              = "dashboard"
  group             = "Home"
  protocol_provider = authentik_provider_oauth2.homarr.id

  meta_launch_url  = "https://dashboard.ericsweiss.com"
  meta_icon        = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/homarr.svg"
  meta_description = ""
  meta_publisher   = ""
  meta_hide        = false

  open_in_new_tab    = true
  policy_engine_mode = "any"

  lifecycle {
    prevent_destroy = true
  }
}
