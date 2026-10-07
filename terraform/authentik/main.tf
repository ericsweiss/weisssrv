# Authentik SSO state as code. The shape comes from the weisssrv-lib
# `authentik-sso` module at a hand-bumped ?ref= that scripts/test_site_configs.py
# holds equal to WEISSSRV_LIB_REF; this site's objects are in the sibling files.
module "sso" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/authentik-sso?ref=v0.17.1"

  # Flow slugs, signing key, grant types and mappings are passed explicitly so a
  # library default change cannot repoint provider identity on a ref bump.
  authorization_flow_slug = "default-provider-authorization-implicit-consent"
  invalidation_flow_slug  = "default-provider-invalidation-flow"
  signing_key_name        = "authentik Self-signed Certificate"

  oauth2_grant_types     = local.oauth2_grant_types
  oauth2_scope_mappings  = local.oauth2_scope_mappings
  saml_property_mappings = local.saml_property_mappings
  custom_scope_mappings  = local.custom_scope_mappings

  oauth2_providers      = local.oauth2_providers
  oauth2_client_secrets = local.oauth2_client_secrets
  proxy_providers       = local.proxy_providers
  saml_providers        = local.saml_providers

  groups                  = local.groups
  group_secret_attributes = local.group_secret_attributes
  users                   = local.users

  applications    = local.applications
  policy_bindings = local.policy_bindings

  embedded_outpost = local.embedded_outpost
}
