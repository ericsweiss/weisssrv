terraform {
  # Floor matches the CI image (hashicorp/terraform:1.15) and the local
  # toolchain, consistent with terraform/cloudflare and terraform/tailscale.
  required_version = ">= 1.15, < 2.0"

  # State stored in GitLab-managed Terraform state (HTTP backend), same pattern
  # as the sibling modules. Configure via TF_HTTP_* env vars pointing at an
  # authentik-specific state name (see README) so it does not collide with the
  # cloudflare/tailscale state.
  backend "http" {
  }

  required_providers {
    authentik = {
      source = "goauthentik/authentik"
      # EXACT pin: the provider is released in lockstep with the authentik
      # server (2026.5.0 targets server 2026.5.x — our deployment runs
      # 2026.5.5). A provider bump must ride the server upgrade, never float
      # ahead of it: a newer provider can carry schema for API fields the
      # older server does not serve. Bump this alongside authentik_version in
      # group_vars/all.yml. The committed .terraform.lock.hcl pins the exact
      # hashes for reproducible init.
      version = "2026.5.0"
    }
  }
}

provider "authentik" {
  # API token from the "Authentik Terraform Token" 1Password item; injected as
  # TF_VAR_authentik_token by the Taskfile / CI (op run). The user behind the
  # token needs admin API access (it manages applications/providers/groups).
  url   = var.authentik_url
  token = var.authentik_token
}
