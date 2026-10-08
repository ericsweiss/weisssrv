# CRITICAL: OAuth2/OIDC providers. client_id values are public and pinned
# literally; secrets are injected from the 1Password items the apps consume.
# grant_types stay authorization_code + refresh_token only, overridden per
# provider if one client needs more. Every allowed_redirect_uris entry is
# matching_mode "strict": a "regex" URI with an unescaped dot matches any
# character and accepts look-alike domains. README § Guardrails.

locals {
  oauth2_grant_types = [
    "authorization_code",
    "refresh_token",
  ]

  # Server-side ordering of the default scope mappings on every OAuth2 provider,
  # pinned here because the list is provider state. Entries are managed ids;
  # `custom:<key>` names an entry of local.custom_scope_mappings below.
  oauth2_scope_mappings = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
  ]

  # The one user-authored property mapping. authentik's built-in email scope
  # hardcodes `email_verified: False` and Mealie 401s without a true claim, so
  # Mealie alone uses this replacement. Every account here is admin-created.
  custom_scope_mappings = {
    email_verified = {
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
  }

  # Shared provider posture, pinned here so a library default change cannot
  # rewrite live token or session behaviour. Per-provider entries override what
  # they must. scope_mappings = null means use local.oauth2_scope_mappings.
  oauth2_provider_defaults = {
    client_type                = "confidential"
    scope_mappings             = null
    sub_mode                   = "hashed_user_id"
    issuer_mode                = "per_provider"
    include_claims_in_id_token = true
    logout_method              = "backchannel"
    access_code_validity       = "minutes=1"
    access_token_validity      = "minutes=5"
    refresh_token_validity     = "days=30"
    refresh_token_threshold    = "hours=1"
  }

  oauth2_provider_data = {
    mealie = {
      name      = "Mealie"
      client_id = "CxIYMtz9Snb893TgDLy2h8Gbqv3wIsROZMqvfpJi"

      # Built-in email scope swapped for the asserted-verified replacement,
      # same server-side ordering.
      scope_mappings = [
        "goauthentik.io/providers/oauth2/scope-openid",
        "custom:email_verified",
        "goauthentik.io/providers/oauth2/scope-profile",
      ]

      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://food.ericsweiss.com/login"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://food.esweiss.com/login"
        },
      ]
    }

    bar_assistant = {
      name      = "Bar Assistant"
      client_id = "4wu1B32z1PhTMxf5TwFtWyHYsIcXitqTQFolSmMk"

      # Bar Assistant matches accounts by email (the only provider not on the
      # default hashed_user_id).
      sub_mode = "user_email"

      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://bar.ericsweiss.com/oauth/callback"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://bar.esweiss.com/oauth/callback"
        },
      ]
    }

    home_assistant = {
      name      = "Home Assistant"
      client_id = "t5vjALCIkc4VyPO6elG6PhazkDjjvw1dq8afI9ko"

      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://home.ericsweiss.com/auth/openid/callback"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://home.esweiss.com/auth/openid/callback"
        },
      ]
    }

    grafana = {
      name      = "Grafana"
      client_id = "FrzKcIhJaOhwbpI1zT5vdXjguxZcftApgnOpobFC"

      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://grafana.esweiss.com/login/generic_oauth"
        },
      ]
    }

    nextcloud = {
      name      = "Nextcloud"
      client_id = "fAOxMfZd8LSSlolT78GNcIR3xb1YFK581IT1QMEv"

      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://cloud.ericsweiss.com/apps/user_oidc/code"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://cloud.esweiss.com/apps/user_oidc/code"
        },
      ]
    }

    # Hermes dashboard OIDC provider, bound to the `agent` tile (issuer path
    # /application/o/agent/). It is the sole auth layer; there is no
    # forward-auth perimeter in front of it (docs/37).
    hermes_dashboard = {
      name      = "Hermes Dashboard"
      client_id = "hermes-dashboard"

      # Both hostnames: the dashboard reconstructs its redirect_uri per request
      # from Traefik's X-Forwarded-Host and -Proto.
      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://agent.ericsweiss.com/auth/callback"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://agent.esweiss.com/auth/callback"
        },
      ]
    }

    # Homarr dashboard OIDC provider, bound to the `dashboard` tile that
    # AUTH_OIDC_ISSUER points at. Homarr syncs the `groups` claim to same-named
    # groups, so `homarr-admins` gates the app and grants Homarr admin (docs/41).
    homarr = {
      name      = "Homarr"
      client_id = "homarr"

      # Both hostnames: NextAuth reconstructs the redirect_uri per request from
      # Traefik's forwarded Host (AUTH_TRUST_HOST=true). Callback path is
      # /api/auth/callback/oidc.
      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://dashboard.ericsweiss.com/api/auth/callback/oidc"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://dashboard.esweiss.com/api/auth/callback/oidc"
        },
      ]
    }

    immich = {
      # Live object name is lowercase "immich" (matches the application name).
      name      = "immich"
      client_id = "40iNWBaamlR89P2eeUgdK6kLjO2OD9wNX4IVLuD2"

      redirect_uris = [
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://photos.ericsweiss.com/auth/login"
        },
        {
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "https://photos.esweiss.com/auth/login"
        },
        {
          # Immich mobile app callback (docs/36 + reference_sso_authentik).
          matching_mode     = "strict"
          redirect_uri_type = "authorization"
          url               = "app.immich:///oauth-callback"
        },
      ]
    }
  }

  oauth2_providers = {
    for key, provider in local.oauth2_provider_data :
    key => merge(local.oauth2_provider_defaults, provider)
  }

  # Per-provider client secrets, injected from 1Password (variables.tf). Kept
  # out of local.oauth2_providers so that map stays non-sensitive and usable as
  # the module's for_each source.
  oauth2_client_secrets = {
    mealie           = var.oauth2_client_secret_mealie
    bar_assistant    = var.oauth2_client_secret_bar_assistant
    home_assistant   = var.oauth2_client_secret_home_assistant
    grafana          = var.oauth2_client_secret_grafana
    nextcloud        = var.oauth2_client_secret_nextcloud
    hermes_dashboard = var.oauth2_client_secret_hermes_dashboard
    homarr           = var.oauth2_client_secret_homarr
    immich           = var.oauth2_client_secret_immich
  }
}
