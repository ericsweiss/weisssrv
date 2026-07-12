variable "cloudflare_api_token" {
  # Dedicated Terraform token — needs Zone Settings:Edit on top of Zone:Read +
  # DNS:Edit because main.tf manages cloudflare_zone_settings_override. Kept
  # separate from the "Cloudflare DNS Token" item (ESO in-cluster consumers +
  # acme_certs) so those hold DNS:Edit + Zone:Read only and a compromised pod
  # cannot downgrade zone-wide TLS posture.
  description = "Cloudflare API token with Zone:Read + DNS:Edit + Zone Settings:Edit (1Password item 'Cloudflare Terraform Token', field 'credential')"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  # Sourced (Taskfile + CI) from the *username* field of the "Cloudflare
  # Terraform Token" 1Password item — non-obvious because it shares an item
  # with the API token rather than living in a dedicated field. A wrong value
  # surfaces as a confusing zone-not-found error, so the validation block below
  # fails fast on an obviously-malformed ID (Cloudflare account IDs are 32 hex
  # chars).
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
