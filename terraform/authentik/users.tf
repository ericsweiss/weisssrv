# Managed user accounts: usernames only. This repo mirrors to a public remote, so no
# personal data or credentials live here. Display names and emails come from
# var.user_identities; adding or adopting an account is docs/40 § Managed users.
locals {
  # Every name here needs a matching key in the "Authentik User Identities"
  # 1Password item, or the apply fails.
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
