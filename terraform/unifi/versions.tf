terraform {
  # Floor matches the CI image (hashicorp/terraform:1.15) and the local
  # toolchain, consistent with terraform/cloudflare, terraform/tailscale and
  # terraform/authentik.
  required_version = ">= 1.15, < 2.0"

  # GitLab-managed Terraform state (HTTP backend). Configure via TF_HTTP_* env
  # vars pointing at a unifi-specific state name (see README).
  backend "http" {
  }

  required_providers {
    unifi = {
      source = "ubiquiti-community/unifi"
      # Pre-1.0 provider: a minor bump carries schema moves, so the pin floats
      # only on the patch and is bumped deliberately. The committed
      # .terraform.lock.hcl pins the exact version and hashes.
      version = "~> 0.55.0"
    }
  }
}

provider "unifi" {
  # API key from the "UniFi Controller" 1Password item, injected as
  # TF_VAR_unifi_api_key by the Taskfile / CI. It belongs to a Limited Admin with
  # Local Access Only; the provider cannot authenticate a 2FA account.
  api_url = var.unifi_api_url
  api_key = var.unifi_api_key

  # Self-signed controller certificate on a LAN address — see the variable's
  # description for why this defaults on and when to turn it off.
  allow_insecure = var.unifi_allow_insecure
}
