terraform {
  # Floor admits the CI image (hashicorp/terraform:1.16) and the workstation
  # toolchain, consistent with terraform/cloudflare.
  required_version = ">= 1.15, < 2.0"

  # GitLab-managed Terraform state (HTTP backend). Configure via TF_HTTP_* env
  # vars pointing at a tailscale-specific state name (see README).
  backend "http" {
  }

  required_providers {
    tailscale = {
      source = "tailscale/tailscale"
      # Pre-1.0 provider: a minor bump can break, so the pin floats only on the
      # patch and is bumped deliberately. The committed .terraform.lock.hcl pins
      # the exact version and hashes.
      version = "~> 0.29.0"
    }
  }
}

provider "tailscale" {
  # OAuth client credentials from the "Tailscale OAuth" 1Password item, injected
  # as TF_VAR_* by the operator. The client needs acl and dns write granted in
  # the admin console; provider scopes are a subset of the granted ones (README).
  oauth_client_id     = var.tailscale_oauth_client_id
  oauth_client_secret = var.tailscale_oauth_client_secret
  scopes              = ["acl", "dns"]
}
