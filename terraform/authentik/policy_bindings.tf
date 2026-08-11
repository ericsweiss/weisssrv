# Application access-policy group bindings — the enforcement structure for
# per-app access. Every application carries exactly one group binding (order 0);
# with policy_engine_mode "any", membership of the bound group is required to
# pass authorization. Homarr is the one two-tier exception (below). All bindings
# are Terraform-created; none is imported.

locals {
  # App slug -> the group gating it. The seven Downloads (media-client) apps
  # share media-admins; every other app is gated by its own per-app group.
  application_group_bindings = {
    # Home
    bar    = authentik_group.app["bar-assistant-users"].id
    cloud  = authentik_group.app["nextcloud-users"].id
    food   = authentik_group.app["mealie-users"].id
    home   = authentik_group.app["home-assistant-users"].id
    photos = authentik_group.app["immich-users"].id

    # Software
    agent   = authentik_group.app["hermes-users"].id
    git     = authentik_group.app["gitlab-users"].id
    grafana = authentik_group.app["grafana-users"].id
    vpn     = authentik_group.app["vpn-admins"].id

    # Downloads
    movies      = authentik_group.media_admins.id
    music       = authentik_group.media_admins.id
    nzbget      = authentik_group.media_admins.id
    prowlarr    = authentik_group.media_admins.id
    pulsarr     = authentik_group.media_admins.id
    qbittorrent = authentik_group.media_admins.id
    tv          = authentik_group.media_admins.id
  }
}

resource "authentik_policy_binding" "app_group" {
  for_each = local.application_group_bindings

  target = authentik_application.app[each.key].uuid
  group  = each.value
  order  = 0
}

# Applications outside the local.applications map (see applications.tf) get
# explicit bindings.

# The two AdGuard SSO dashboards: dns-admins (which also carries the injected
# AdGuard credentials — groups.tf).
resource "authentik_policy_binding" "adguard_01" {
  target = authentik_application.adguard_01.uuid
  group  = authentik_group.dns_admins.id
  order  = 0
}

resource "authentik_policy_binding" "adguard_02" {
  target = authentik_application.adguard_02.uuid
  group  = authentik_group.dns_admins.id
  order  = 0
}

# Homarr dashboard: two access tiers. homarr-admins is also the group name
# Homarr matches from the OIDC `groups` claim to grant board-admin; homarr-users
# members pass the Authentik gate but land as regular (non-admin) Homarr users.
# policy_engine_mode "any" on the app means EITHER binding grants access.
resource "authentik_policy_binding" "homarr" {
  target = authentik_application.homarr.uuid
  group  = authentik_group.app["homarr-admins"].id
  order  = 0
}

resource "authentik_policy_binding" "homarr_users" {
  target = authentik_application.homarr.uuid
  group  = authentik_group.app["homarr-users"].id
  order  = 1
}
