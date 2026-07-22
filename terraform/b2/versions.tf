terraform {
  # Floor matches the CI image (hashicorp/terraform:1.15) and the local
  # toolchain, consistent with terraform/cloudflare / tailscale / authentik.
  # Native `import {}` blocks (imports.tf) require >= 1.5, so 1.15 is fine.
  required_version = ">= 1.15, < 2.0"

  # State stored in GitLab-managed Terraform state (HTTP backend), same pattern
  # as the sibling modules. Configure via TF_HTTP_* env vars pointing at a
  # b2-specific state name (see README) so it does not collide with the
  # cloudflare/tailscale/authentik state.
  backend "http" {
  }

  required_providers {
    b2 = {
      source = "Backblaze/b2"
      # Patch-floating pin (~> 0.13.0 = >= 0.13.0, < 0.14.0): the provider is
      # pre-1.0, so a minor bump can carry breaking changes — make it a
      # deliberate edit (matches the cloudflare/tailscale pins). The committed
      # .terraform.lock.hcl pins the exact version + hashes for reproducible
      # init.
      version = "~> 0.13.0"
    }
  }
}

provider "b2" {
  # B2 application key from the "B2 Archive Backup" 1Password item; injected as
  # TF_VAR_b2_application_key_id / TF_VAR_b2_application_key by the Taskfile / CI
  # (op run / op read). The key needs bucket-management capability (create/read
  # bucket settings) to reconcile versioning + lifecycle + SSE on the bucket.
  application_key_id = var.b2_application_key_id
  application_key    = var.b2_application_key
}
