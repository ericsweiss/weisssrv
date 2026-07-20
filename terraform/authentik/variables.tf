variable "authentik_url" {
  description = "Base URL of the authentik API (internal split-horizon name)"
  type        = string
  default     = "https://auth.esweiss.com"

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+$", var.authentik_url))
    error_message = "authentik_url must be a bare https:// origin with no path or trailing slash."
  }
}

variable "authentik_token" {
  description = "authentik API token (1Password item 'Authentik Terraform Token', field 'credential')"
  type        = string
  sensitive   = true
}

# --- OAuth2 client secrets -------------------------------------------------
# One sensitive variable per OAuth2 provider, injected by the Taskfile / CI via
# `op run` from the SAME 1Password items the applications themselves consume
# (docs/15-credential-rotation.md "Required 1Password Items"), so Terraform and
# the app can never disagree about a secret. Never committed, never defaulted.

variable "oauth2_client_secret_mealie" {
  description = "Mealie OIDC client secret (1Password item 'Mealie SSO', field 'oidc-client-secret')"
  type        = string
  sensitive   = true
}

variable "oauth2_client_secret_bar_assistant" {
  description = "Bar Assistant OIDC client secret (1Password item 'Bar Assistant SSO', field 'authentik-client-secret')"
  type        = string
  sensitive   = true
}

variable "oauth2_client_secret_home_assistant" {
  description = "Home Assistant OIDC client secret (1Password item 'Home Assistant SSO', field 'authentik-client-secret')"
  type        = string
  sensitive   = true
}

variable "oauth2_client_secret_grafana" {
  description = "Grafana OIDC client secret (1Password item 'Grafana SSO', field 'oidc-client-secret')"
  type        = string
  sensitive   = true
}

variable "oauth2_client_secret_nextcloud" {
  description = "Nextcloud OIDC client secret (1Password item 'Nextcloud SSO', field 'client-secret')"
  type        = string
  sensitive   = true
}

variable "oauth2_client_secret_immich" {
  description = "Immich OIDC client secret (1Password item 'Immich SSO', field 'client-secret')"
  type        = string
  sensitive   = true
}
