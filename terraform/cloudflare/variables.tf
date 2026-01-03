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
}
