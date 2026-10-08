terraform {
  # Floor admits the CI image (hashicorp/terraform:1.16) and the workstation
  # toolchain, consistent with terraform/cloudflare and terraform/tailscale.
  required_version = ">= 1.15, < 2.0"

  # GitLab-managed state over the HTTP backend. TF_HTTP_* env vars select the
  # authentik-specific state name (see README).
  backend "http" {
  }

  required_providers {
    authentik = {
      source = "goauthentik/authentik"
      # CRITICAL: exact pin, minor-locked to authentik_version in group_vars/all.yml.
      # A provider ahead of the server carries schema the API does not serve.
      version = "2026.8.0"
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
