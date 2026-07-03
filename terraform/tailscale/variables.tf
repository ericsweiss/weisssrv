variable "tailscale_oauth_client_id" {
  description = "Tailscale OAuth client ID (scope: acl). 1Password item 'Tailscale Terraform OAuth'."
  type        = string
  sensitive   = true
}

variable "tailscale_oauth_client_secret" {
  description = "Tailscale OAuth client secret (scope: acl). 1Password item 'Tailscale Terraform OAuth'."
  type        = string
  sensitive   = true
}
