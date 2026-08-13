# SAML provider for GitLab (docs/27). GitLab's side of the trust (IdP cert
# fingerprint) lives in the "GitLab SSO" 1Password item and gitlab.rb — only
# the authentik side is managed here.
#
# The NameID policy ("default_name_id_policy") has no schema field in the
# provider. The live value is the server default (…nameid-format:persistent), so
# nothing drifts — but a UI change to it is invisible to Terraform.

locals {
  # Server-side ordering of the default SAML mappings on the GitLab provider.
  # Pinned here rather than inherited from the module default: the list is
  # provider state, and a library default change must not reorder it.
  saml_property_mappings = [
    "goauthentik.io/providers/saml/name",
    "goauthentik.io/providers/saml/email",
    "goauthentik.io/providers/saml/username",
    "goauthentik.io/providers/saml/uid",
    "goauthentik.io/providers/saml/groups",
    "goauthentik.io/providers/saml/upn",
    "goauthentik.io/providers/saml/ms-windowsaccountname",
  ]

  saml_providers = {
    gitlab = {
      name = "GitLab"

      acs_url         = "https://git.ericsweiss.com/users/auth/saml/callback"
      audience        = "https://git.ericsweiss.com"
      issuer_override = "https://git.ericsweiss.com"
      sp_binding      = "post"
      sls_url         = ""
      sls_binding     = "redirect"
      logout_method   = "frontchannel_iframe"

      assertion_valid_not_before      = "minutes=-5"
      assertion_valid_not_on_or_after = "minutes=5"
      session_valid_not_on_or_after   = "minutes=86400"

      digest_algorithm    = "http://www.w3.org/2001/04/xmlenc#sha256"
      signature_algorithm = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"

      sign_assertion       = true
      sign_response        = false
      sign_logout_request  = false
      sign_logout_response = false

      default_relay_state = ""
    }
  }
}
