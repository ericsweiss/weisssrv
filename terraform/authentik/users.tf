# Managed user accounts — usernames as code; personal data and credentials NEVER.
#
# This repo mirrors to a PUBLIC GitHub remote, so no personal data lives here:
# the map keys below are the usernames (login names) only, and each account's
# display NAME and EMAIL come from var.user_identities — the 1Password
# "Authentik User Identities" item (docs/15), op-run-injected as
# TF_VAR_user_identities at plan/apply time. Passwords and MFA are set by the
# person themselves via an authentik enrollment/recovery link an admin sends
# after the supervised apply (docs/40 § Managed users).
#
# Adding an account: `task authentik:add-user` appends a username here (and
# prints the 1Password JSON snippet to add + the groups.tf reminder — membership
# lives on the group in groups.tf, not here). A username with no matching
# user_identities entry fails the apply, because the module reads its name and
# email from that map.
#
# PRE-EXISTING accounts also need a declarative `import {}` block in imports.tf
# (id = the user pk) landing in the SAME apply — `eric` was adopted that way
# (pk 7). `akadmin` stays deliberately unmanaged (the break-glass account);
# service accounts (outpost, etc.) are authentik-managed and never belong here.
#
# The module puts `prevent_destroy` on every user: rename a username with a
# `moved {}` block, never delete+recreate — a destroy takes the account's
# sessions and consent grants with it.
locals {
  # Login names only. `eric` is pre-existing (import pk 7 in imports.tf); the
  # four family accounts are created fresh. Every name here needs a matching key
  # in the "Authentik User Identities" 1Password item, or the apply fails.
  managed_usernames = [
    "eric",
    "amy",
    "neil",
    "emma",
    "joseph",
  ]

  # Name + email resolved from the op-injected identities map; active/path take
  # the module defaults (true / "users").
  users = {
    for username in local.managed_usernames :
    username => {
      name  = var.user_identities[username].name
      email = var.user_identities[username].email
    }
  }
}
