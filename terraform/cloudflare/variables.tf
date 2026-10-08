variable "cloudflare_api_token" {
  # Needs Zone Settings:Edit on top of Zone:Read + DNS:Edit for main.tf's
  # cloudflare_zone_settings_override. Separate from the "Cloudflare DNS Token"
  # item so in-cluster consumers cannot change zone-wide TLS posture.
  description = "Cloudflare API token with Zone:Read + DNS:Edit + Zone Settings:Edit (1Password item 'Cloudflare Terraform Token', field 'credential')"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  # Sourced by the Taskfile and CI from the *username* field of the "Cloudflare
  # Terraform Token" item, sharing that item with the API token. A wrong value
  # surfaces as zone-not-found, so the validation below fails fast on the shape.
  description = "Cloudflare account ID (stored in the 'username' field of the 'Cloudflare Terraform Token' 1Password item)"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.cloudflare_account_id))
    error_message = "cloudflare_account_id must be a 32-character hex string (the Cloudflare account ID)."
  }
}

variable "external_domain" {
  description = "External domain managed in Cloudflare"
  type        = string
  default     = "ericsweiss.com"

  validation {
    condition     = can(regex("^[a-z0-9-]+(\\.[a-z0-9-]+)*\\.[a-z]{2,}$", var.external_domain))
    error_message = "external_domain must be a bare FQDN (no scheme, trailing dot, or slash), e.g. ericsweiss.com."
  }
}
