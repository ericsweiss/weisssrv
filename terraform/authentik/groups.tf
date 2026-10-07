# Groups and memberships, modelled group-side on each group's `users` list.
# The module resolves each username to its pk; users are not managed here, and
# "authentik Read-only" is left unmanaged. Guardrails: README.

# CRITICAL: membership is exhaustive. The provider treats `users` as the full
# authoritative list, so a member added in the Admin console is drift and the
# next supervised apply deletes them from the group, removing their access. The
# diff shows pks rather than names, so it is easy to approve by accident. Add
# people here instead.

locals {
  # The single human operator. authentik's bootstrap admin (akadmin) is only in
  # the superuser group below.
  operator_users = ["eric"]

  # Family members with NON-ADMIN access. They join only the *-users tiers in
  # family_member_groups below — never an *-admins group — so app access is
  # granted without any admin role. Usernames are declared in users.tf.
  family_users = ["amy", "neil", "emma", "joseph"]

  # The five apps the family gets: their user-tier (non-admin) access groups.
  # Adding a group here widens family access; removing one revokes it on the
  # next supervised apply (membership is exhaustive — see the header note).
  family_member_groups = [
    "mealie-users",
    "bar-assistant-users",
    "immich-users",
    "nextcloud-users",
    "homarr-users",
  ]

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
    "status-admins",        # Uptime Kuma admin UI forward-auth + `status` binding (docs/45)
    "traefik-admins",       # Traefik dashboard forward-auth + `traefik` binding
    "vpn-admins",           # wg-easy admin UI + `vpn` binding (docs/38)
  ]

  groups = merge(
    {
      for name in local.member_groups :
      name => {
        users = contains(local.family_member_groups, name) ? concat(local.operator_users, local.family_users) : local.operator_users
      }
    },
    {
      # authentik's built-in superuser group, managed because its membership is
      # user-curated state. The key differs from the name, which has a space.
      "authentik-admins" = {
        name         = "authentik Admins"
        is_superuser = true
        users        = ["akadmin", "eric"]
      }
    },
  )

  # Basic-auth injection attributes, kept out of local.groups so that map stays
  # non-sensitive. The names are referenced by providers_proxy.tf, and the
  # values are op-run-injected from 1Password (variables.tf).
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
