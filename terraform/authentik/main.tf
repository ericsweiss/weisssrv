# Authentik SSO state as code. The SHAPE — every resource, its
# `prevent_destroy` guard, the unbound-application precondition and the security
# defaults — comes from the weisssrv-lib `authentik-sso` module at a pinned ref;
# the object inventory (applications, providers, groups, bindings, the outpost
# list) is this site's data in the sibling files.
#
# The module grew what this site's shapes need in v0.7.0: `prevent_destroy` on every object, `custom_scope_mappings`
# (Mealie's asserted-verified email scope) and the unbound-application
# precondition. Every terraform root now pins the same release as
# WEISSSRV_LIB_REF — the pins are bumped by hand (check-lib-pins.py does not
# read module sources), and scripts/test_site_configs.py fails a missed one.
#
# Adoption was address-only: moved.tf maps all 78 pre-module resource instances
# onto their module addresses, so the first plan after this change is moves and
# nothing else (README § Adoption onto the library module).
#
# The flow slugs, signing key name, grant types and mapping lists are passed
# EXPLICITLY rather than inherited from the module defaults: they are identity
# for every provider here, and a library default change must never repoint them
# on a ref bump. Same reasoning as terraform/cloudflare's zone_settings.
module "sso" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/authentik-sso?ref=v0.13.1"

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
