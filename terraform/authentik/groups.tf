# Groups + memberships. Membership is modelled here on the group's `users`
# list (the goauthentik provider models it group-side; authentik_user.groups
# is the same relation from the other end — managing both would fight). The
# module resolves each USERNAME to its pk through a data source; users
# themselves are NOT managed.
#
# MEMBERSHIP IS EXHAUSTIVE. The provider treats `users` as the authoritative
# full list, so adding a second household member to homarr-users / mealie-users /
# home-assistant-users in the Admin console is drift: authentik-drift-plan
# reports it and the next supervised apply DELETES them from the group, removing
# their access. The diff is a list of pks, not names, so it is easy to approve by
# accident. Add people here instead — one more username in the relevant `users`
# list.
#
# Deliberately unmanaged: "authentik Read-only" (auto-generated alongside its
# managed role by authentik's RBAC bootstrap).
#
# Every group carries the module's prevent_destroy: a renamed key plans as
# destroy+create, which drops the memberships and every binding referencing it
# (README § Guardrails).

locals {
  # The single human operator. authentik's bootstrap admin (akadmin) is only in
  # the superuser group below.
  operator_users = ["eric"]

  # App-access groups, all with the single human operator as member. The
  # `*-users` groups also gate their applications via the one-binding-per-app
  # policy bindings in policy_bindings.tf. Map key = group name.
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
    "media-admins",         # gates the seven Downloads apps; carries NZBGet injection (below)
    "dns-admins",           # gates both AdGuard dashboards; carries AdGuard injection (below)
    "nextcloud-users",      # Nextcloud OIDC + `cloud` binding (docs/35)
    "traefik-admins",       # Traefik dashboard forward-auth + `traefik` binding
    "vpn-admins",           # wg-easy admin UI + `vpn` binding (docs/38)
  ]

  groups = merge(
    {
      for name in local.member_groups :
      name => { users = local.operator_users }
    },
    {
      # authentik's built-in superuser group. Managed (unlike "authentik
      # Read-only") because its membership is user-curated state: eric was added
      # alongside the bootstrap akadmin account. Key differs from the name
      # because the name carries a space.
      "authentik-admins" = {
        name         = "authentik Admins"
        is_superuser = true
        users        = ["akadmin", "eric"]
      }
    },
  )

  # Basic-auth injection attributes, kept out of local.groups so that map stays
  # non-sensitive. The attribute NAMES are what the proxy providers'
  # basic_auth_*_attribute fields reference (providers_proxy.tf); group
  # attributes merge into every member's user attributes, so membership alone
  # grants the credential injection. The values are op-run-injected from the SAME
  # 1Password items the apps' real credentials live in (variables.tf) — never
  # literals in authentik config.
  group_secret_attributes = {
    # media-admins gates the seven Downloads apps (policy_bindings.tf) and
    # injects the NZBGet ControlUsername/ControlPassword pair for the nzbget
    # provider.
    "media-admins" = {
      nzbget_user     = var.basic_auth_nzbget_username
      nzbget_password = var.basic_auth_nzbget_password
    }

    # dns-admins gates the two AdGuard SSO dashboards (policy_bindings.tf) and
    # injects the AdGuard admin credentials for both adguard providers.
    "dns-admins" = {
      adguard_user     = var.basic_auth_adguard_username
      adguard_password = var.basic_auth_adguard_password
    }
  }
}
