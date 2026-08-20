# Managed user accounts — identity as code, credentials NEVER.
#
# One entry per account this repo owns; `task authentik:add-user` scaffolds an
# entry here (and reminds you to add the username to the right group in
# groups.tf — membership lives THERE, on the group, not here). Passwords and
# MFA are set by the person themselves via an authentik enrollment/recovery
# link an admin sends after the supervised apply (docs/40 § Managed users).
#
# PRE-EXISTING accounts join via a declarative `import {}` block in
# imports.tf (id = the user pk) landing in the SAME apply as the entry here —
# declaring one without the import fails the apply on a username collision.
# `akadmin` stays deliberately unmanaged: it is the break-glass account and
# lives outside IaC on purpose. Service accounts (outpost, etc.) are
# authentik-managed and never belong here.
#
# The module puts `prevent_destroy` on every user: rename a key with a
# `moved {}` block, never delete+recreate — a destroy takes the account's
# sessions and consent grants with it.
locals {
  users = {
    # Adopted 2026-08-19 (import pk 7): values mirror the live object
    # verbatim, so the import lands with a zero-change plan.
    "eric" = {
      name  = "Eric Weiss"
      email = "ericsweiss1@gmail.com"
    }
  }
}
