# Split-DNS: esweiss.com queries from Tailscale clients go to the in-cluster
# CoreDNS resolver (the "ts-dns" operator device), which CNAMEs Traefik-fronted
# names to traefik-tailnet (mesh path) and forwards the rest to AdGuard.
# See kubernetes/apps/tailnet-dns and docs/05-tailscale.md.
locals {
  split_dns = {
    # Resolved by the module from the device's deterministic hostname (the
    # Service's tailscale.com/hostname annotation), so the 100.x is derived at
    # plan time and a device rebuild self-heals.
    #
    # FAILURE MODE: Tailscale appends a numeric suffix ("ts-dns-1") when the
    # hostname is still held by a device that has not aged out — the common
    # outcome when the Service is recreated before the old node key expires. The
    # lookup then errors after its 60s wait and `terraform plan` fails outright,
    # which looks exactly like ACL drift in the allow_failure `tailscale-drift-
    # plan` job. Recovery: delete the stale `ts-dns` device in the Admin console
    # (or `tailscale logout` it) so the rebuilt Service reclaims the bare
    # hostname, then re-plan.
    "esweiss.com" = { device_hostname = "ts-dns" }
  }
}

# NOTE — removing a key from `local.split_dns` breaks `*.esweiss.com` resolution
# for every tailnet client (the mesh path in docs/05-tailscale.md). Treat this
# map as break-glass. How that removal presents depends on the module ref pinned
# in main.tf; scripts/test_site_configs.py holds that pin equal to
# WEISSSRV_LIB_REF, and this note moves with it. At the pinned release the module's
# `tailscale_dns_split_nameservers` carries `prevent_destroy`, so the removal
# is a hard plan error rather than a destroy; the deliberate path is
# `terraform state rm 'module.tailnet.tailscale_dns_split_nameservers.this["esweiss.com"]'`
# first (the live mapping survives that), then dropping the key. The module
# README at the pinned ref is the contract.
