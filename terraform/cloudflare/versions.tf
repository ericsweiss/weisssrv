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
      # Patch-floating pin: minor bumps are deliberate (v4 minors have shipped
      # schema/deprecation changes). v5 is a breaking rewrite — migrate explicitly.
      version = "~> 4.52"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
