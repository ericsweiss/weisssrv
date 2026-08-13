# Applications — the tiles on the authentik library page plus their provider
# bindings. One map entry per app, keyed by slug (which is also the OIDC issuer
# path). Adding an app = one entry here + its provider; see README "Adding a new
# application".
#
# Access is enforced by the group policy bindings in policy_bindings.tf, and the
# module fails the plan for any slug missing there (an unbound application is
# open to every authenticated user). Roles beyond access (admin vs user) are
# resolved inside each app from its group claims — see that app's doc.
#
# `provider_type` + `provider_key` name an entry of the provider maps in
# providers_{oauth2,proxy,saml}.tf. Every application carries the module's
# `prevent_destroy`: the slug IS the issuer path, so a renamed key would plan as
# destroy+create and break that app's logins (README § Guardrails).
locals {
  applications = {
    # Home
    bar = {
      name          = "Bar Assistant"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "bar_assistant"
      launch_url    = "https://bar.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/bar-assistant.svg"
    }
    cloud = {
      name          = "Nextcloud"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "nextcloud"
      launch_url    = "https://cloud.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/nextcloud.svg"
    }
    food = {
      name          = "Mealie"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "mealie"
      launch_url    = "https://food.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/mealie.svg"
    }
    home = {
      name          = "Home Assistant"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "home_assistant"
      launch_url    = "https://home.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/home-assistant.svg"
    }
    photos = {
      name          = "immich"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "immich"
      launch_url    = "https://photos.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/immich.svg"
    }

    # Homarr dashboard. Slug `dashboard` -> issuer path /application/o/dashboard/
    # (matches homarr AUTH_OIDC_ISSUER). Library group "Home", alongside the
    # other household-facing tiles.
    dashboard = {
      name          = "Homarr"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "homarr"
      launch_url    = "https://dashboard.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/homarr.svg"
    }

    # Software
    agent = {
      name          = "Hermes"
      group         = "Software"
      provider_type = "oauth2"
      provider_key  = "hermes_dashboard"
      launch_url    = "https://agent.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/selfhst/icons/png/hermes-agent.png"
    }
    git = {
      name          = "GitLab"
      group         = "Software"
      provider_type = "saml"
      provider_key  = "gitlab"
      launch_url    = "https://git.ericsweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/gitlab.svg"
    }
    grafana = {
      name          = "Grafana"
      group         = "Software"
      provider_type = "oauth2"
      provider_key  = "grafana"
      launch_url    = "https://grafana.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/grafana.svg"
    }
    vpn = {
      name          = "WireGuard Easy"
      group         = "Software"
      provider_type = "proxy"
      provider_key  = "wireguard_easy"
      launch_url    = "https://vpn.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/wireguard.svg"
    }

    # AdGuard Home SSO dashboards — one application per forward-auth provider
    # (forward_single matches exactly one external host; docs/08). Library group
    # "Software", with the other operator dashboards.
    "adguard-01" = {
      name          = "AdGuard Home dns-01"
      group         = "Software"
      provider_type = "proxy"
      provider_key  = "adguard_01"
      launch_url    = "https://adguard.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/adguard-home.svg"
    }
    "adguard-02" = {
      name          = "AdGuard Home dns-02"
      group         = "Software"
      provider_type = "proxy"
      provider_key  = "adguard_02"
      launch_url    = "https://adguard-02.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/adguard-home.svg"
    }

    # Downloads
    movies = {
      name          = "Radarr"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "radarr"
      launch_url    = "https://movies.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/radarr.svg"
    }
    music = {
      name          = "Lidarr"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "lidarr"
      launch_url    = "https://music.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/lidarr.svg"
    }
    nzbget = {
      name          = "NZBGet"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "nzbget"
      launch_url    = "https://nzbget.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/nzbget.svg"
    }
    prowlarr = {
      name          = "Prowlarr"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "prowlarr"
      launch_url    = "https://prowlarr.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/prowlarr.svg"
    }
    pulsarr = {
      name          = "Pulsarr"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "pulsarr"
      launch_url    = "https://pulsarr.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/pulsarr.svg"
    }
    qbittorrent = {
      name          = "qBittorrent"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "qbittorrent"
      launch_url    = "https://qbittorrent.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/qbittorrent.svg"
    }
    tv = {
      name          = "Sonarr"
      group         = "Downloads"
      provider_type = "proxy"
      provider_key  = "sonarr"
      launch_url    = "https://tv.esweiss.com"
      icon          = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/sonarr.svg"
    }
  }
}
