terraform {
  required_version = ">= 1.5, < 2.0"

  # State stored in GitLab-managed Terraform state (HTTP backend)
  # All configuration via TF_HTTP_* environment variables (see Taskfile / CI)
  backend "http" {
  }

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
