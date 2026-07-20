# Applications (the tiles on the authentik library page + the provider
# bindings). One map entry per app, keyed by slug; the resource shape is
# identical across all sixteen. Adding an app = one entry here + its provider
# (see README "Adding a new application").
#
# Access policy: every application carries exactly one group policy binding
# (policy_bindings.tf) — with policy_engine_mode "any", membership of the
# bound group is required for authorization. App-level roles beyond access
# (admin vs user) still happen in the apps themselves (group claims) — see
# docs per app.

locals {
  applications = {
    # --- Home ---
    bar = {
      name        = "Bar Assistant"
      group       = "Home"
      provider_id = authentik_provider_oauth2.bar_assistant.id
      launch_url  = "https://bar.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/bar-assistant.svg"
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

    # --- Software ---
    agent = {
      name        = "Hermes"
      group       = "Software"
      provider_id = authentik_provider_proxy.hermes.id
      launch_url  = "https://agent.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/selfhst/icons/png/hermes-agent.png"
    }
    cloud = {
      name        = "Nextcloud"
      group       = "Software"
      provider_id = authentik_provider_oauth2.nextcloud.id
      launch_url  = "https://cloud.ericsweiss.com"
      meta_icon   = "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/nextcloud.svg"
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

    # --- Downloads ---
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
}

# Hermes Dashboard SSO — the one application outside the for_each map, and
# (with its provider) the first authored in Terraform rather than imported.
# It is a SECOND application for Hermes because authentik allows one provider
# per application: app["agent"] keeps the forward-auth proxy provider (the
# perimeter — authentik_provider_proxy.hermes), while this app carries the
# dashboard's OAuth2 provider and exists to give it its issuer slug
# (/application/o/agent-sso/). meta_launch_url is empty on purpose: this is a
# non-clickable utility app, not a library tile — the real entry point is the
# `agent` tile. Kept out of local.applications so the for_each import blocks
# in imports.tf never try to import a live object that predates Terraform.
resource "authentik_application" "agent_sso" {
  name              = "Hermes Dashboard SSO"
  slug              = "agent-sso"
  group             = "Software"
  protocol_provider = authentik_provider_oauth2.hermes_dashboard.id

  meta_launch_url  = ""
  meta_icon        = ""
  meta_description = ""
  meta_publisher   = ""
  meta_hide        = false

  open_in_new_tab    = true
  policy_engine_mode = "any"
}

# AdGuard Home SSO dashboards — one application per forward-auth provider
# (forward_single = one external host each; docs/08). Terraform-authored, so
# — like agent_sso — they live OUTSIDE local.applications to keep imports.tf's
# for_each import blocks away from objects that never existed in the Admin UI.
# Library group "Software": these are operator dashboards, the same bucket as
# Grafana / GitLab / the wg-easy admin UI (no dedicated "Infrastructure" group
# exists, and two tiles don't justify inventing one).

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
}
