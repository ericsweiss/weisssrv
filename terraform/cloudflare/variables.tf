variable "cloudflare_api_token" {
  description = "Cloudflare API token with appropriate permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID"
  type        = string
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
