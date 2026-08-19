# Application access-policy group bindings — the enforcement structure for
# per-app access. Every application carries AT LEAST one group binding (order 0);
# with policy_engine_mode "any", membership of a bound group is required to pass
# authorization. Homarr is the one two-tier case (two bindings, below). All
# bindings are Terraform-created; none is imported.
#
# FAILS OPEN, so the coupling is asserted: an application with zero bindings is
# reachable by every authenticated user. The module's precondition on
# authentik_application fails the plan for any slug missing here, including in
# the read-only authentik-drift-plan job.
#
# Bindings are the one object class WITHOUT prevent_destroy (module-side, by
# design): they are cheap to recreate and are how access is widened or narrowed.

locals {
  # Binding key -> {application slug, group name gating it}. The seven Downloads
  # (media-client) apps share media-admins; every other app is gated by its own
  # per-app group. Key = state address, so it is stable per binding, not derived.
  policy_bindings = {
    # Home
    bar    = { application = "bar", group = "bar-assistant-users" }
    cloud  = { application = "cloud", group = "nextcloud-users" }
    food   = { application = "food", group = "mealie-users" }
    home   = { application = "home", group = "home-assistant-users" }
    photos = { application = "photos", group = "immich-users" }

    # Homarr dashboard: two access tiers. homarr-admins is also the group name
    # Homarr matches from the OIDC `groups` claim to grant board-admin;
    # homarr-users members pass the Authentik gate but land as regular
    # (non-admin) Homarr users. policy_engine_mode "any" on the app means EITHER
    # binding grants access.
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
