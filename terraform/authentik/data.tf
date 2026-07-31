# Stock authentik objects referenced by the managed resources. These are all
# created and owned by authentik itself (default flows, the self-signed
# signing keypair, the `managed`-flagged default property mappings, built-in
# users) — Terraform reads them by stable identifier and NEVER manages them.
# See README.md "Managed vs unmanaged".

# Flows
# Every provider uses the default implicit-consent authorization flow and the
# default provider-invalidation flow.

data "authentik_flow" "provider_authorization" {
  slug = "default-provider-authorization-implicit-consent"
}

data "authentik_flow" "provider_invalidation" {
  slug = "default-provider-invalidation-flow"
}

# Signing keypair
# The install-generated self-signed keypair signs OIDC tokens (all six OAuth2
# providers) and the GitLab SAML assertions.

data "authentik_certificate_key_pair" "self_signed" {
  name = "authentik Self-signed Certificate"
}

# OAuth2 scope mappings
# Looked up individually (not via managed_list) so each provider's
# property_mappings list can reproduce the exact server-side ordering.
# (Proxy providers carry no lookups here: authentik auto-assigns their five
# default scope mappings and the resource deliberately leaves the field
# unconfigured — see providers_proxy.tf.)

data "authentik_property_mapping_provider_scope" "openid" {
  managed = "goauthentik.io/providers/oauth2/scope-openid"
}

data "authentik_property_mapping_provider_scope" "email" {
  managed = "goauthentik.io/providers/oauth2/scope-email"
}

data "authentik_property_mapping_provider_scope" "profile" {
  managed = "goauthentik.io/providers/oauth2/scope-profile"
}

# SAML property mappings (GitLab provider)

data "authentik_property_mapping_provider_saml" "name" {
  managed = "goauthentik.io/providers/saml/name"
}

data "authentik_property_mapping_provider_saml" "email" {
  managed = "goauthentik.io/providers/saml/email"
}

data "authentik_property_mapping_provider_saml" "username" {
  managed = "goauthentik.io/providers/saml/username"
}

data "authentik_property_mapping_provider_saml" "uid" {
  managed = "goauthentik.io/providers/saml/uid"
}

data "authentik_property_mapping_provider_saml" "groups" {
  managed = "goauthentik.io/providers/saml/groups"
}

data "authentik_property_mapping_provider_saml" "upn" {
  managed = "goauthentik.io/providers/saml/upn"
}

data "authentik_property_mapping_provider_saml" "windows_account_name" {
  managed = "goauthentik.io/providers/saml/ms-windowsaccountname"
}

# Users
# Users are deliberately NOT managed (akadmin is authentik's bootstrap admin,
# eric is the human operator whose password/MFA live outside Terraform).
# Group membership is modelled on the authentik_group resources' `users`
# lists, referencing these lookups.

data "authentik_user" "eric" {
  username = "eric"
}

data "authentik_user" "akadmin" {
  username = "akadmin"
}
