# CRITICAL: per-app access bindings. Authorization fails open, so an application with
# zero bindings is reachable by every authenticated user; the module's precondition
# fails the plan for any slug missing here. Every app carries at least one binding, and
# with policy_engine_mode "any" membership of one bound group is enough. Bindings are
# the one object class without prevent_destroy, since they are how access is changed.

locals {
  # Binding key -> {application slug, group gating it}. The Downloads apps share
  # media-admins; every other app has its own group. The key is the state address, so
  # it stays stable per binding rather than being derived.
  policy_bindings = {
    # Home
    bar    = { application = "bar", group = "bar-assistant-users" }
    cloud  = { application = "cloud", group = "nextcloud-users" }
    food   = { application = "food", group = "mealie-users" }
    home   = { application = "home", group = "home-assistant-users" }
    photos = { application = "photos", group = "immich-users" }

    # Homarr has two tiers: homarr-admins is also the group Homarr matches from the
    # OIDC `groups` claim to grant board-admin, while homarr-users members pass the
    # Authentik gate as regular users.
    dashboard-admins = { application = "dashboard", group = "homarr-admins", order = 0 }
    dashboard-users  = { application = "dashboard", group = "homarr-users", order = 1 }

    # Software
    agent   = { application = "agent", group = "hermes-users" }
    git     = { application = "git", group = "gitlab-users" }
    grafana = { application = "grafana", group = "grafana-users" }
    status  = { application = "status", group = "status-admins" }
    traefik = { application = "traefik", group = "traefik-admins" }
    vpn     = { application = "vpn", group = "vpn-admins" }

    # The two AdGuard SSO dashboards: dns-admins (which also carries the
    # injected AdGuard credentials — groups.tf).
    adguard-01 = { application = "adguard-01", group = "dns-admins" }
    adguard-02 = { application = "adguard-02", group = "dns-admins" }

    # Downloads
    movies      = { application = "movies", group = "media-admins" }
    music       = { application = "music", group = "media-admins" }
    nzbget      = { application = "nzbget", group = "media-admins" }
    prowlarr    = { application = "prowlarr", group = "media-admins" }
    pulsarr     = { application = "pulsarr", group = "media-admins" }
    qbittorrent = { application = "qbittorrent", group = "media-admins" }
    tv          = { application = "tv", group = "media-admins" }
  }
}
