# OAuth2/OIDC providers. client_id values are public identifiers (they appear
# in every authorize redirect) and are pinned literally; client secrets are
# injected per provider from the SAME 1Password items the applications consume
# (see variables.tf), so Terraform and the app can never disagree.
#
# Two rules hold for every provider here:
# - grant_types: authorization_code + refresh_token ONLY. If one client truly
#   needs more, override it on that provider, never widen the shared list.
# - every allowed_redirect_uris entry is matching_mode "strict". A "regex" mode
#   full-matches, and an unescaped `.` matches any character — a plain URL as
#   the pattern also accepts look-alike registrable domains.
#
# Every provider carries prevent_destroy — a rename plans as destroy+create and
# breaks that app's login (README § Guardrails).

locals {
  oauth2_grant_types = [
    "authorization_code",
    "refresh_token",
  ]

  # Server-side ordering of the default scope mappings on every OAuth2 provider.
  oauth2_property_mappings = [
    data.authentik_property_mapping_provider_scope.openid.id,
    data.authentik_property_mapping_provider_scope.email.id,
    data.authentik_property_mapping_provider_scope.profile.id,
  ]

  # Mealie only: same ordering, but the built-in email scope is swapped for the
  # replacement below. See that resource for why.
  oauth2_property_mappings_email_verified = [
    data.authentik_property_mapping_provider_scope.openid.id,
    authentik_property_mapping_provider_scope.email_verified.id,
    data.authentik_property_mapping_provider_scope.profile.id,
  ]
}

# The ONE user-authored property mapping. authentik's built-in email scope
# hardcodes `email_verified: False` and is a managed blueprint object (restored
# on upgrade), while Mealie 401s any login without a true claim — so Mealie gets
# this replacement and every other provider keeps the built-in. Asserting true is
# sound here: no self-registration exists, every account is admin-created.
resource "authentik_property_mapping_provider_scope" "email_verified" {
  name        = "OIDC email (asserted verified)"
  scope_name  = "email"
  description = "Email address, with email_verified asserted true. Mealie refuses to authenticate without it; authentik's built-in email scope hardcodes false."

  expression = <<-EOT
    return {
        "email": request.user.email,
        "email_verified": True,
    }
  EOT
}

resource "authentik_provider_oauth2" "mealie" {
  name      = "Mealie"
  client_id = "CxIYMtz9Snb893TgDLy2h8Gbqv3wIsROZMqvfpJi"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_mealie
  grant_types   = local.oauth2_grant_types

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings_email_verified

  allowed_redirect_uris = [
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://food.ericsweiss.com/login",
    },
    {
      matching_mode     = "strict",
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

  lifecycle {
    prevent_destroy = true
  }
}

resource "authentik_provider_oauth2" "bar_assistant" {
  name      = "Bar Assistant"
  client_id = "4wu1B32z1PhTMxf5TwFtWyHYsIcXitqTQFolSmMk"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_bar_assistant
  grant_types   = local.oauth2_grant_types

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://bar.ericsweiss.com/oauth/callback",
    },
    {
      matching_mode     = "strict",
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

  lifecycle {
    prevent_destroy = true
  }
}

resource "authentik_provider_oauth2" "home_assistant" {
  name      = "Home Assistant"
  client_id = "t5vjALCIkc4VyPO6elG6PhazkDjjvw1dq8afI9ko"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_home_assistant
  grant_types   = local.oauth2_grant_types

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://home.ericsweiss.com/auth/openid/callback",
    },
    {
      matching_mode     = "strict",
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

  lifecycle {
    prevent_destroy = true
  }
}

resource "authentik_provider_oauth2" "grafana" {
  name      = "Grafana"
  client_id = "FrzKcIhJaOhwbpI1zT5vdXjguxZcftApgnOpobFC"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_grafana
  grant_types   = local.oauth2_grant_types

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

  lifecycle {
    prevent_destroy = true
  }
}

resource "authentik_provider_oauth2" "nextcloud" {
  name      = "Nextcloud"
  client_id = "fAOxMfZd8LSSlolT78GNcIR3xb1YFK581IT1QMEv"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_nextcloud
  grant_types   = local.oauth2_grant_types

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

  lifecycle {
    prevent_destroy = true
  }
}

# Hermes dashboard OIDC provider (docs/37 §SSO). Terraform-authored, hence a
# human-readable client_id. Bound to the `agent` tile (issuer path
# /application/o/agent/) — the sole auth layer; there is no forward-auth
# perimeter in front of it.
resource "authentik_provider_oauth2" "hermes_dashboard" {
  name      = "Hermes Dashboard"
  client_id = "hermes-dashboard"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_hermes_dashboard
  grant_types   = local.oauth2_grant_types

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    # Both hostnames, like the other dual-host OIDC apps (immich/nextcloud):
    # the dashboard reconstructs its redirect_uri per-request from Traefik's
    # X-Forwarded-Host/-Proto (kubernetes/apps/hermes/deployment.yaml).
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://agent.ericsweiss.com/auth/callback",
    },
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://agent.esweiss.com/auth/callback",
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

  lifecycle {
    prevent_destroy = true
  }
}

# Homarr dashboard OIDC provider (docs/41). Bound to the `dashboard` tile
# (issuer path /application/o/dashboard/, which homarr's AUTH_OIDC_ISSUER
# points at). Homarr syncs the `groups` claim to same-named Homarr groups, so
# `homarr-admins` both gates the app and grants Homarr admin.
resource "authentik_provider_oauth2" "homarr" {
  name      = "Homarr"
  client_id = "homarr"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_homarr
  grant_types   = local.oauth2_grant_types

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id
  property_mappings  = local.oauth2_property_mappings

  allowed_redirect_uris = [
    # Both hostnames, like the other dual-host OIDC apps (immich/nextcloud/
    # hermes): NextAuth reconstructs the redirect_uri per-request from Traefik's
    # forwarded Host (homarr AUTH_TRUST_HOST=true). Callback path is
    # /api/auth/callback/oidc.
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://dashboard.ericsweiss.com/api/auth/callback/oidc",
    },
    {
      matching_mode     = "strict",
      redirect_uri_type = "authorization",
      url               = "https://dashboard.esweiss.com/api/auth/callback/oidc",
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

  lifecycle {
    prevent_destroy = true
  }
}

resource "authentik_provider_oauth2" "immich" {
  # Live object name is lowercase "immich" (matches the application name).
  name      = "immich"
  client_id = "40iNWBaamlR89P2eeUgdK6kLjO2OD9wNX4IVLuD2"

  client_type   = "confidential"
  client_secret = var.oauth2_client_secret_immich
  grant_types   = local.oauth2_grant_types

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

  lifecycle {
    prevent_destroy = true
  }
}
