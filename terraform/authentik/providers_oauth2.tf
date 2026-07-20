# OAuth2/OIDC providers. client_id values are public identifiers (they appear
# in every authorize redirect) and are pinned literally; client secrets are
# injected per provider from the SAME 1Password items the applications consume
# (see variables.tf), so Terraform and the app can never disagree.
#
# grant_types is order-sensitive server-side; the two orderings below are the
# exact lists the API stores (older providers were created with a different
# UI ordering than the 2026.x-created ones).

locals {
  # Mealie / Bar Assistant / Home Assistant / Grafana (created pre-2026)
  oauth2_grant_types_legacy = [
    "authorization_code",
    "hybrid",
    "implicit",
    "client_credentials",
    "password",
    "urn:ietf:params:oauth:grant-type:device_code",
    "refresh_token",
  ]
  # Nextcloud / Immich / Hermes Dashboard (created on 2026.x)
  oauth2_grant_types_current = [
    "authorization_code",
    "implicit",
    "hybrid",
    "refresh_token",
    "client_credentials",
    "password",
    "urn:ietf:params:oauth:grant-type:device_code",
  ]

  # Server-side ordering of the default scope mappings on every OAuth2 provider.
  oauth2_property_mappings = [
    data.authentik_property_mapping_provider_scope.openid.id,
    data.authentik_property_mapping_provider_scope.email.id,
    data.authentik_property_mapping_provider_scope.profile.id,
  ]
}

resource "authentik_provider_oauth2" "mealie" {
  name      = "Mealie"
  client_id = "CxIYMtz9Snb893TgDLy2h8Gbqv3wIsROZMqvfpJi"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_mealie
  grant_types   = local.oauth2_grant_types_legacy

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "regex",
      redirect_uri_type = "authorization",
      url               = "https://food.ericsweiss.com/login",
    },
    {
      matching_mode     = "regex",
      redirect_uri_type = "authorization",
      url               = "https://food.esweiss.com/login",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  sub_mode                   = "hashed_user_id"
  issuer_mode                = "per_provider"
  logout_method              = "backchannel"
}

resource "authentik_provider_oauth2" "bar_assistant" {
  name      = "Bar Assistant"
  client_id = "4wu1B32z1PhTMxf5TwFtWyHYsIcXitqTQFolSmMk"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_bar_assistant
  grant_types   = local.oauth2_grant_types_legacy

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "regex",
      redirect_uri_type = "authorization",
      url               = "https://bar.ericsweiss.com/oauth/callback",
    },
    {
      matching_mode     = "regex",
      redirect_uri_type = "authorization",
      url               = "https://bar.esweiss.com/oauth/callback",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  # Bar Assistant matches accounts by email (the only provider not on the
  # default hashed_user_id).
  sub_mode      = "user_email"
  issuer_mode   = "per_provider"
  logout_method = "backchannel"
}

resource "authentik_provider_oauth2" "home_assistant" {
  name      = "Home Assistant"
  client_id = "t5vjALCIkc4VyPO6elG6PhazkDjjvw1dq8afI9ko"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_home_assistant
  grant_types   = local.oauth2_grant_types_legacy

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "regex",
      redirect_uri_type = "authorization",
      url               = "https://home.ericsweiss.com/auth/openid/callback",
    },
    {
      matching_mode     = "regex",
      redirect_uri_type = "authorization",
      url               = "https://home.esweiss.com/auth/openid/callback",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  sub_mode                   = "hashed_user_id"
  issuer_mode                = "per_provider"
  logout_method              = "backchannel"
}

resource "authentik_provider_oauth2" "grafana" {
  name      = "Grafana"
  client_id = "FrzKcIhJaOhwbpI1zT5vdXjguxZcftApgnOpobFC"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_grafana
  grant_types   = local.oauth2_grant_types_legacy

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://grafana.esweiss.com/login/generic_oauth",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  sub_mode                   = "hashed_user_id"
  issuer_mode                = "per_provider"
  logout_method              = "backchannel"
}

resource "authentik_provider_oauth2" "nextcloud" {
  name      = "Nextcloud"
  client_id = "fAOxMfZd8LSSlolT78GNcIR3xb1YFK581IT1QMEv"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_nextcloud
  grant_types   = local.oauth2_grant_types_current

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://cloud.ericsweiss.com/apps/user_oidc/code",
    },
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://cloud.esweiss.com/apps/user_oidc/code",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  sub_mode                   = "hashed_user_id"
  issuer_mode                = "per_provider"
  logout_method              = "backchannel"
}

# Hermes dashboard `self_hosted` OIDC provider (docs/37 §SSO) — the first
# provider AUTHORED in Terraform rather than imported, so the client_id is a
# chosen human-readable literal instead of an API-generated 40-char one
# (client_ids are public identifiers either way). Its application is
# authentik_application.agent_sso (slug agent-sso -> issuer path
# /application/o/agent-sso/); the `agent` application keeps the forward-auth
# perimeter (authentik_provider_proxy.hermes).
resource "authentik_provider_oauth2" "hermes_dashboard" {
  name      = "Hermes Dashboard"
  client_id = "hermes-dashboard"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_hermes_dashboard
  grant_types   = local.oauth2_grant_types_current

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      # The dashboard reconstructs its redirect_uri from
      # HERMES_DASHBOARD_PUBLIC_URL (pinned to the external host in
      # kubernetes/apps/hermes/deployment.yaml), so exactly one strict URI
      # is ever presented.
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://agent.ericsweiss.com/auth/callback",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  sub_mode                   = "hashed_user_id"
  issuer_mode                = "per_provider"
  logout_method              = "backchannel"
}

resource "authentik_provider_oauth2" "immich" {
  # Live object name is lowercase "immich" (matches the application name).
  name      = "immich"
  client_id = "40iNWBaamlR89P2eeUgdK6kLjO2OD9wNX4IVLuD2"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_immich
  grant_types   = local.oauth2_grant_types_current

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://photos.ericsweiss.com/auth/login",
    },
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://photos.esweiss.com/auth/login",
    },
    {
      # Immich mobile app callback (docs/36 + reference_sso_authentik).
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "app.immich:///oauth-callback",
    },
  ]

  access_code_validity       = "minutes=1"
  access_token_validity      = "minutes=5"
  refresh_token_validity     = "days=30"
  refresh_token_threshold    = "hours=1"
  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  sub_mode                   = "hashed_user_id"
  issuer_mode                = "per_provider"
  logout_method              = "backchannel"
}
