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
#   the pattern also accepts look-alike registrable domains. (The module rejects
#   a regex URI with an unescaped dot; strict is what this site uses anyway.)
#
# Every provider carries the module's prevent_destroy — a renamed map key plans
# as destroy+create and breaks that app's login (README § Guardrails).

locals {
  oauth2_grant_types = [
    "authorization_code",
    "refresh_token",
  ]

  # Server-side ordering of the default scope mappings on every OAuth2 provider.
  # Pinned here rather than inherited from the module default: the list IS
  # provider state, and a library default change must not reorder it on a ref
  # bump. Entries are managed ids; `custom:<key>` names an entry of
  # local.custom_scope_mappings below.
  oauth2_scope_mappings = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
  ]

  # The ONE user-authored property mapping. authentik's built-in email scope
  # hardcodes `email_verified: False` and is a managed blueprint object (restored
  # on upgrade), while Mealie 401s any login without a true claim — so Mealie gets
  # this replacement (see its scope_mappings below) and every other provider keeps
  # the built-in. Asserting true is sound here: no self-registration exists, every
  # account is admin-created.
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
  # rewrite live token/session behaviour on a ref bump. Per-provider entries
  # override what they must (Bar Assistant's sub_mode; Mealie's scope list).
  # scope_mappings = null means "use local.oauth2_scope_mappings".
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

    # Hermes dashboard OIDC provider (docs/37 §SSO). Terraform-authored, hence a
    # human-readable client_id. Bound to the `agent` tile (issuer path
    # /application/o/agent/) — the sole auth layer; there is no forward-auth
    # perimeter in front of it.
    hermes_dashboard = {
      name      = "Hermes Dashboard"
      client_id = "hermes-dashboard"

      # Both hostnames, like the other dual-host OIDC apps (immich/nextcloud):
      # the dashboard reconstructs its redirect_uri per-request from Traefik's
      # X-Forwarded-Host/-Proto (kubernetes/apps/hermes/deployment.yaml).
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

    # Homarr dashboard OIDC provider (docs/41). Bound to the `dashboard` tile
    # (issuer path /application/o/dashboard/, which homarr's AUTH_OIDC_ISSUER
    # points at). Homarr syncs the `groups` claim to same-named Homarr groups, so
    # `homarr-admins` both gates the app and grants Homarr admin.
    homarr = {
      name      = "Homarr"
      client_id = "homarr"

      # Both hostnames, like the other dual-host OIDC apps (immich/nextcloud/
      # hermes): NextAuth reconstructs the redirect_uri per-request from Traefik's
      # forwarded Host (homarr AUTH_TRUST_HOST=true). Callback path is
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
