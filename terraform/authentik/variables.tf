variable "authentik_url" {
  description = "Base URL of the authentik API (internal split-horizon name)"
  type        = string
  default     = "https://auth.esweiss.com"

  validation {
    # A port is allowed: a DR bootstrap or a port-forwarded API endpoint
    # (https://127.0.0.1:8443) is exactly what `terraform import` needs when the
    # split-horizon name is not resolvable yet.
    condition     = can(regex("^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", var.authentik_url))
    error_message = "authentik_url must be a bare https:// host with an optional port, and no path or trailing slash."
  }
}

variable "authentik_token" {
  description = "authentik API token (1Password item 'Authentik Terraform Token', field 'credential')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.authentik_token) > 0
    error_message = "authentik_token must be non-empty; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

# OAuth2 client secrets
# One sensitive variable per OAuth2 provider, injected by the Taskfile / CI via
# `op run` from the SAME 1Password items the applications themselves consume
# (docs/15-credential-rotation.md "Required 1Password Items"), so Terraform and
# the app can never disagree about a secret. Never committed, never defaulted.
#
# WHY EACH ONE VALIDATES A LENGTH FLOOR
# A renamed 1Password field makes `op run` supply an empty string, and because
# these are `sensitive`, the plan renders the change as
# `~ client_secret = (sensitive value) -> (sensitive value)` — an operator
# reviewing it line by line cannot see that the new value is empty. Applying that
# desynchronises authentik from the app and locks users out. The floor is well
# below every value in use (the shortest is 40 characters).

variable "oauth2_client_secret_mealie" {
  description = "Mealie OIDC client secret (1Password item 'Mealie SSO', field 'oidc-client-secret')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_mealie) >= 16
    error_message = "oauth2_client_secret_mealie looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_bar_assistant" {
  description = "Bar Assistant OIDC client secret (1Password item 'Bar Assistant SSO', field 'authentik-client-secret')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_bar_assistant) >= 16
    error_message = "oauth2_client_secret_bar_assistant looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_home_assistant" {
  description = "Home Assistant OIDC client secret (1Password item 'Home Assistant SSO', field 'authentik-client-secret')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_home_assistant) >= 16
    error_message = "oauth2_client_secret_home_assistant looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_grafana" {
  description = "Grafana OIDC client secret (1Password item 'Grafana SSO', field 'oidc-client-secret')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_grafana) >= 16
    error_message = "oauth2_client_secret_grafana looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_nextcloud" {
  description = "Nextcloud OIDC client secret (1Password item 'Nextcloud SSO', field 'client-secret')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_nextcloud) >= 16
    error_message = "oauth2_client_secret_nextcloud looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_immich" {
  description = "Immich OIDC client secret (1Password item 'Immich SSO', field 'client-secret')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_immich) >= 16
    error_message = "oauth2_client_secret_immich looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_hermes_dashboard" {
  description = "Hermes dashboard OIDC client secret (1Password item 'Hermes Secrets', field 'hermes-dashboard-oidc-client-secret' — the same field the hermes-secrets ExternalSecret syncs into the cluster)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_hermes_dashboard) >= 16
    error_message = "oauth2_client_secret_hermes_dashboard looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "oauth2_client_secret_homarr" {
  description = "Homarr OIDC client secret (1Password item 'Homarr SSO', field 'client-secret' — the same field the homarr-secrets ExternalSecret syncs into the cluster)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.oauth2_client_secret_homarr) >= 16
    error_message = "oauth2_client_secret_homarr looks empty or truncated; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

# Basic-auth injection credentials
# Per-app upstream credentials stored as GROUP attributes (groups.tf) and
# injected by proxy providers with basic_auth_enabled (providers_proxy.tf):
# the embedded outpost sends them as the Authorization header, which the
# dedicated authentik-auth-basic Traefik middleware forwards upstream — so
# SSO members never see the app's own login. Values come from the SAME
# 1Password items the apps' real credentials live in, so authentik can never
# inject a stale pair. Usernames are not secret per se, but the pairs travel
# together and are marked sensitive as a unit.
#
# Non-empty only (no length floor): usernames are legitimately short, and an
# empty value here silently disables credential injection, so NZBGet/AdGuard fall
# back to their own login prompt behind the SSO gate.

variable "basic_auth_nzbget_username" {
  description = "NZBGet ControlUsername (1Password item 'NZBGet', field 'username' — must match nzbget.conf)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.basic_auth_nzbget_username) > 0
    error_message = "basic_auth_nzbget_username must be non-empty; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "basic_auth_nzbget_password" {
  description = "NZBGet ControlPassword (1Password item 'NZBGet', field 'password' — must match nzbget.conf)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.basic_auth_nzbget_password) > 0
    error_message = "basic_auth_nzbget_password must be non-empty; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "basic_auth_adguard_username" {
  description = "AdGuard Home admin username (1Password item 'AdGuard Home', field 'username')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.basic_auth_adguard_username) > 0
    error_message = "basic_auth_adguard_username must be non-empty; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}

variable "basic_auth_adguard_password" {
  description = "AdGuard Home admin password (1Password item 'AdGuard Home', field 'password')"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.basic_auth_adguard_password) > 0
    error_message = "basic_auth_adguard_password must be non-empty; check the op:// reference in the Taskfile and the authentik-drift-plan job."
  }
}
