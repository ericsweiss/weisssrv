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
      # Patch-floating pin (~> 4.52.0 = >= 4.52.0, < 4.53.0): patches float, but a
      # minor bump must be a deliberate edit here (v4 minors have shipped
      # schema/deprecation changes). v5 is a breaking rewrite — migrate explicitly.
      #
      # KNOWN MIGRATION DEBT (v4 -> v5): the v5 provider removed/renamed the
      # resources this config relies on:
      #   - cloudflare_zone_settings_override (main.tf) -> per-setting
      #     cloudflare_zone_setting resources
      #   - cloudflare_record (dns.tf, all records) -> cloudflare_dns_record
      #     (different argument schema; `data {}` blocks for CAA become a
      #     typed `data` object)
      # Migrating means rewriting every resource above AND `terraform state mv`
      # for each, so the ~> 4.52.0 pin defers it deliberately. Do NOT bump to v5
      # incidentally — schedule it as its own change.
      version = "~> 4.52.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
