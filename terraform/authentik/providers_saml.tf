# SAML provider for GitLab (docs/27). GitLab's side of the trust (IdP cert
# fingerprint) lives in the "GitLab SSO" 1Password item and gitlab.rb — only
# the authentik side is managed here.
#
# The NameID policy ("default_name_id_policy") has no schema field in the
# provider. The live value is the server default (…nameid-format:persistent), so
# nothing drifts — but a UI change to it is invisible to Terraform.

locals {
  # Server-side ordering of the default SAML mappings on the GitLab provider.
  saml_property_mappings = [
    data.authentik_property_mapping_provider_saml.name.id,
    data.authentik_property_mapping_provider_saml.email.id,
    data.authentik_property_mapping_provider_saml.username.id,
    data.authentik_property_mapping_provider_saml.uid.id,
    data.authentik_property_mapping_provider_saml.groups.id,
    data.authentik_property_mapping_provider_saml.upn.id,
    data.authentik_property_mapping_provider_saml.windows_account_name.id,
  ]
}

resource "authentik_provider_saml" "gitlab" {
  name = "GitLab"

  acs_url         = "https://git.ericsweiss.com/users/auth/saml/callback"
  audience        = "https://git.ericsweiss.com"
  issuer_override = "https://git.ericsweiss.com"
  sp_binding      = "post"
  sls_url         = ""
  sls_binding     = "redirect"
  logout_method   = "frontchannel_iframe"

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.saml_property_mappings

  assertion_valid_not_before      = "minutes=-5"
  assertion_valid_not_on_or_after = "minutes=5"
  session_valid_not_on_or_after   = "minutes=86400"

  digest_algorithm    = "http://www.w3.org/2001/04/xmlenc#sha256"
  signature_algorithm = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
  signing_kp          = data.authentik_certificate_key_pair.self_signed.id

  sign_assertion       = true
  sign_response        = false
  sign_logout_request  = false
  sign_logout_response = false

  default_relay_state = ""

  lifecycle {
    prevent_destroy = true
  }
}
