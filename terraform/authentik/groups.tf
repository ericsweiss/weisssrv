# Groups + memberships. Membership is modelled here on the group's `users`
# list (the goauthentik provider models it group-side; authentik_user.groups
# is the same relation from the other end — managing both would fight).
# Users themselves are NOT managed — see data.tf.
#
# Deliberately unmanaged: "authentik Read-only" (auto-generated alongside its
# managed role by authentik's RBAC bootstrap).

locals {
  # App-access groups, all with the single human operator as member.
  # Grouped by consumer:
  #   admin                          — wg-easy admin UI (docs/38)
  #   gitlab-admins / gitlab-users   — GitLab SAML group mapping (docs/27)
  #   grafana-admins / grafana-users — Grafana OIDC role mapping (docs/31)
  #   hermes-users                   — Hermes forward-auth perimeter (docs/37)
  #   immich-users                   — Immich OIDC (docs/36)
  #   mealie-admins / mealie-users   — Mealie OIDC (docs/22-23)
  #   nextcloud-users                — Nextcloud OIDC (docs/35)
  #   vpn-admins                     — wg-easy admin UI (docs/38)
  member_groups = [
    "admin",
    "gitlab-admins",
    "gitlab-users",
    "grafana-admins",
    "grafana-users",
    "hermes-users",
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
