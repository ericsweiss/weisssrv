# Application access-policy group bindings. Every application carries exactly
# ONE group binding (order 0, enabled) — with policy_engine_mode "any",
# membership of the bound group is required to pass authorization. This is the
# ENFORCEMENT structure for per-app access: the sole human user is a member of
# every bound group (groups.tf), so effective access today is unchanged;
# future users are scoped per-app purely by group membership.
#
# All bindings here are Terraform-CREATED (nothing to import): before this
# file no application had a binding, i.e. every app was open to all
# authenticated users.

locals {
  # App slug -> the group gating it. The seven Downloads (media-client) apps
  # share media-admins; every other app is gated by its own per-app group.
  application_group_bindings = {
    # --- Home ---
    bar    = authentik_group.app["bar-assistant-users"].id
    food   = authentik_group.app["mealie-users"].id
    home   = authentik_group.app["home-assistant-users"].id
    photos = authentik_group.app["immich-users"].id

    # --- Software ---
    agent   = authentik_group.app["hermes-users"].id
    cloud   = authentik_group.app["nextcloud-users"].id
    git     = authentik_group.app["gitlab-users"].id
    grafana = authentik_group.app["grafana-users"].id
    vpn     = authentik_group.app["vpn-admins"].id

    # --- Downloads ---
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

# agent-sso: same gate as the `agent` perimeter — hermes-users.
resource "authentik_policy_binding" "agent_sso" {
  target = authentik_application.agent_sso.uuid
  group  = authentik_group.app["hermes-users"].id
  order  = 0
}

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
