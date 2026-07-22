# Groups + memberships. Membership is modelled here on the group's `users`
# list (the goauthentik provider models it group-side; authentik_user.groups
# is the same relation from the other end — managing both would fight).
# Users themselves are NOT managed — see data.tf.
#
# Deliberately unmanaged: "authentik Read-only" (auto-generated alongside its
# managed role by authentik's RBAC bootstrap).

locals {
  # App-access groups, all with the single human operator as member. The
  # `*-users` groups also gate their applications via the one-binding-per-app
  # policy bindings in policy_bindings.tf. (media-admins / dns-admins carry
  # basic-auth injection attributes too, so they are explicit resources below,
  # not list entries.)
  # Grouped by consumer:
  #   admin                          — wg-easy admin UI (docs/38)
  #   bar-assistant-users            — `bar` application binding
  #   gitlab-admins / gitlab-users   — GitLab SAML group mapping (docs/27);
  #                                    gitlab-users also binds the `git` app
  #   grafana-admins / grafana-users — Grafana OIDC role mapping (docs/31);
  #                                    grafana-users also binds the `grafana` app
  #   hermes-users                   — Hermes dashboard OIDC (docs/37)
  #                                    + the `agent` app binding
  #   home-assistant-users           — `home` application binding
  #   homarr-admins / homarr-users   — two-tier `dashboard` app gate (either
  #                                    binding grants access); homarr-admins is
  #                                    also the Homarr admin group synced from
  #                                    the OIDC `groups` claim, homarr-users
  #                                    land as regular users (docs/41)
  #   immich-users                   — Immich OIDC (docs/36) + `photos` binding
  #   mealie-admins / mealie-users   — Mealie OIDC (docs/22-23);
  #                                    mealie-users also binds the `food` app
  #   nextcloud-users                — Nextcloud OIDC (docs/35) + `cloud` binding
  #   vpn-admins                     — wg-easy admin UI (docs/38) + `vpn` binding
  member_groups = [
    "admin",
    "bar-assistant-users",
    "gitlab-admins",
    "gitlab-users",
    "grafana-admins",
    "grafana-users",
    "hermes-users",
    "home-assistant-users",
    "homarr-admins",
    "homarr-users",
    "immich-users",
    "mealie-admins",
    "mealie-users",
    "nextcloud-users",
    "vpn-admins",
  ]
}

resource "authentik_group" "app" {
  for_each = toset(local.member_groups)

  name  = each.value
  users = [data.authentik_user.eric.pk]
}

# Role groups that ALSO carry basic-auth injection attributes (explicit
# resources, not member_groups entries, because of the attributes map). The
# attribute names are what the proxy providers' basic_auth_*_attribute fields
# reference (providers_proxy.tf); group attributes merge into every member's
# user attributes, so membership alone grants the credential injection. The
# values are op-run-injected from the SAME 1Password items the apps' real
# credentials live in (variables.tf) — never literals in authentik config.

# media-admins gates the seven Downloads apps (policy_bindings.tf) and injects
# the NZBGet ControlUsername/ControlPassword pair for the nzbget provider.
resource "authentik_group" "media_admins" {
  name  = "media-admins"
  users = [data.authentik_user.eric.pk]
  attributes = jsonencode({
    nzbget_user     = var.basic_auth_nzbget_username
    nzbget_password = var.basic_auth_nzbget_password
  })
}

# dns-admins gates the two AdGuard SSO dashboards (policy_bindings.tf) and
# injects the AdGuard admin credentials for both adguard providers.
resource "authentik_group" "dns_admins" {
  name  = "dns-admins"
  users = [data.authentik_user.eric.pk]
  attributes = jsonencode({
    adguard_user     = var.basic_auth_adguard_username
    adguard_password = var.basic_auth_adguard_password
  })
}

# authentik's built-in superuser group. Managed (unlike "authentik Read-only")
# because its membership is user-curated state: eric was added alongside the
# bootstrap akadmin account.
resource "authentik_group" "authentik_admins" {
  name         = "authentik Admins"
  is_superuser = true
  users = [
    data.authentik_user.akadmin.pk,
    data.authentik_user.eric.pk,
  ]
}
