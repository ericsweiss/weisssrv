variable "tailscale_oauth_client_id" {
  description = "Tailscale OAuth client ID (scopes: acl + dns, both write). 1Password item 'Tailscale OAuth', field 'client id'."
  type        = string
  sensitive   = true
}

variable "tailscale_oauth_client_secret" {
  description = "Tailscale OAuth client secret (scopes: acl + dns, both write). 1Password item 'Tailscale OAuth', field 'credential'."
  type        = string
  sensitive   = true
}
