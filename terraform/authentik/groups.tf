# Groups + memberships. Membership is modelled here on the group's `users`
# list (the goauthentik provider models it group-side; authentik_user.groups
# is the same relation from the other end — managing both would fight).
# Users themselves are NOT managed — see data.tf.
#
# Deliberately unmanaged: "authentik Read-only" (auto-generated alongside its
# managed role by authentik's RBAC bootstrap).
#
# Every group carries prevent_destroy: a rename plans as destroy+create, which
# drops the memberships and every binding referencing it (README § Guardrails).

locals {
  # App-access groups, all with the single human operator as member. The
  # `*-users` groups also gate their applications via the one-binding-per-app
  # policy bindings in policy_bindings.tf. (media-admins / dns-admins carry
  # basic-auth injection attributes too, so they are explicit resources below,
  # not list entries.)
  member_groups = [
    "admin",                # legacy imported group; no binding in this module
    "bar-assistant-users",  # `bar` application binding
    "gitlab-admins",        # GitLab SAML group mapping (docs/27)
    "gitlab-users",         # GitLab SAML mapping + `git` app binding
    "grafana-admins",       # Grafana OIDC role mapping (docs/31)
    "grafana-users",        # Grafana OIDC mapping + `grafana` app binding
    "hermes-users",         # Hermes dashboard OIDC + `agent` binding (docs/37)
    "home-assistant-users", # `home` application binding
    "homarr-admins",        # Homarr admin group synced from the OIDC groups claim (docs/41)
    "homarr-users",         # second tier of the `dashboard` gate; either binding grants access
    "immich-users",         # Immich OIDC + `photos` binding (docs/36)
    "mealie-admins",        # Mealie OIDC admin mapping (docs/22-23)
    "mealie-users",         # Mealie OIDC + `food` app binding
    "nextcloud-users",      # Nextcloud OIDC + `cloud` binding (docs/35)
    "vpn-admins",           # wg-easy admin UI + `vpn` binding (docs/38)
  ]
}

resource "authentik_group" "app" {
  for_each = toset(local.member_groups)

  name  = each.value
  users = [data.authentik_user.eric.pk]

  lifecycle {
    prevent_destroy = true
  }
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

  lifecycle {
    prevent_destroy = true
  }
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

  lifecycle {
    prevent_destroy = true
  }
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

  lifecycle {
    prevent_destroy = true
  }
}
