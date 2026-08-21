terraform {
  # Floor matches the CI image (hashicorp/terraform:1.15) and the local
  # toolchain, consistent with terraform/cloudflare, terraform/tailscale and
  # terraform/authentik.
  required_version = ">= 1.15, < 2.0"

  # State stored in GitLab-managed Terraform state (HTTP backend), same pattern
  # as the sibling roots. Configure via TF_HTTP_* env vars pointing at a
  # unifi-specific state name (see README) so it does not collide with the
  # cloudflare/tailscale/authentik state.
  backend "http" {
  }

  required_providers {
    unifi = {
      source = "ubiquiti-community/unifi"
      # Patch-floating pin (~> 0.55.0 = >=0.55.0, <0.56.0): a pre-1.0 provider
      # and a ground-up rewrite of the abandoned paultyng/unifi one, so a minor
      # bump carries schema moves (0.52 -> 0.55 made firewall-policy `index`
      # read-only and endpoint match lists Computed). Make it a deliberate edit.
      # The committed .terraform.lock.hcl pins the exact version + hashes.
      version = "~> 0.55.0"
    }
  }
}

provider "unifi" {
  # API key from the "UniFi Controller" 1Password item, injected as
  # TF_VAR_unifi_api_key by the Taskfile / CI (op run). It belongs to a Limited
  # Admin with Local Access Only — the provider cannot authenticate an account
  # with 2FA, which every ui.com SSO account here has.
  #
  # `site` is left at the provider default ("default"), which is this console's
  # only site.
  api_url = var.unifi_api_url
  api_key = var.unifi_api_key

  # The console serves its own self-signed certificate on the LAN address, and
  # the api_url is that address — there is no name to issue a real cert for.
  allow_insecure = true
}
