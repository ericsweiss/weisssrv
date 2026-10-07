terraform {
  # Floor matches the CI image (hashicorp/terraform:1.15) and the local
  # toolchain — state written by 1.15 is unreadable by older binaries.
  required_version = ">= 1.15, < 2.0"

  # State stored in GitLab-managed Terraform state (HTTP backend)
  # All configuration via TF_HTTP_* environment variables (see Taskfile / CI)
  backend "http" {
  }

  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
      # Patches float; a minor bump is a deliberate edit (v4 minors ship schema
      # changes). v5 is a breaking rewrite of every resource here; scope and
      # sequence are in docs/16-next-steps.md § Terraform and gates.
      version = "~> 4.52.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
