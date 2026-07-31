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

# OAuth2 client secrets
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

variable "oauth2_client_secret_hermes_dashboard" {
  description = "Hermes dashboard OIDC client secret (1Password item 'Hermes Secrets', field 'hermes-dashboard-oidc-client-secret' — the same field the hermes-secrets ExternalSecret syncs into the cluster)"
  type        = string
  sensitive   = true
}

variable "oauth2_client_secret_homarr" {
  description = "Homarr OIDC client secret (1Password item 'Homarr SSO', field 'client-secret' — the same field the homarr-secrets ExternalSecret syncs into the cluster)"
  type        = string
  sensitive   = true
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

variable "basic_auth_nzbget_username" {
  description = "NZBGet ControlUsername (1Password item 'NZBGet', field 'username' — must match nzbget.conf)"
  type        = string
  sensitive   = true
}

variable "basic_auth_nzbget_password" {
  description = "NZBGet ControlPassword (1Password item 'NZBGet', field 'password' — must match nzbget.conf)"
  type        = string
  sensitive   = true
}

variable "basic_auth_adguard_username" {
  description = "AdGuard Home admin username (1Password item 'AdGuard Home', field 'username')"
  type        = string
  sensitive   = true
}

variable "basic_auth_adguard_password" {
  description = "AdGuard Home admin password (1Password item 'AdGuard Home', field 'password')"
  type        = string
  sensitive   = true
}
