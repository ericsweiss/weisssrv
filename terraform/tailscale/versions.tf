terraform {
  # Floor matches the CI image (hashicorp/terraform:1.15) and the local
  # toolchain, consistent with terraform/cloudflare.
  required_version = ">= 1.15, < 2.0"

  # State stored in GitLab-managed Terraform state (HTTP backend), same pattern
  # as terraform/cloudflare. Configure via TF_HTTP_* env vars pointing at a
  # tailscale-specific state name (see README) so it does not collide with the
  # cloudflare state.
  backend "http" {
  }

  required_providers {
    tailscale = {
      source = "tailscale/tailscale"
      # Patch-floating pin (~> 0.29.0 = >=0.29.0, <0.30.0): the provider is
      # pre-1.0, so a minor bump can carry breaking changes — make it a deliberate
      # edit (matches terraform/cloudflare). The committed .terraform.lock.hcl
      # pins the exact version + hashes for reproducible init.
      version = "~> 0.29.0"
    }
  }
}

provider "tailscale" {
  # OAuth client credentials (scope: acl write) from the "Tailscale Terraform
  # OAuth" 1Password item; injected as TF_VAR_* by the operator (see README).
  oauth_client_id     = var.tailscale_oauth_client_id
  oauth_client_secret = var.tailscale_oauth_client_secret
  scopes              = ["acl"]
}
