# Split-DNS: route esweiss.com queries from Tailscale clients to the in-cluster
# CoreDNS resolver (the "ts-dns" operator device), REPLACING the console entry
# that pointed at AdGuard .150/.160. The resolver returns a CNAME to the
# traefik-tailnet device for Traefik-fronted names (mesh path) and forwards the
# rest to AdGuard. See kubernetes/apps/tailnet-dns and docs/05-tailscale.md.
#
# GATED so the ACL (policy.hujson) can be applied on its own FIRST. The ts-dns
# device does not exist until the Flux changes are merged and the operator has
# registered it, so the data source below cannot be read until then. Leave
# enable_split_dns=false (default) for the phase-A ACL apply; flip it on only for
# the final phase-B apply, once `kubectl -n tailnet-dns get svc ts-dns` shows a
# 100.x EXTERNAL-IP:
#
#   Phase A (ACL only):   task terraform:tailscale-apply
#   Phase B (Split-DNS):  terraform import 'tailscale_dns_split_nameservers.esweiss[0]' esweiss.com   # adopt the existing console entry
#                         TF_VAR_enable_split_dns=true task terraform:tailscale-apply

variable "enable_split_dns" {
  description = "Manage the esweiss.com Split-DNS nameserver (requires the ts-dns operator device to be registered). Keep false until phase B."
  type        = bool
  default     = false
}

# Read the ts-dns device's tailnet IP by its deterministic hostname (set via the
# Service's tailscale.com/hostname annotation). Derives the 100.x at apply time
# rather than hardcoding a runtime-assigned address; self-heals if the device is
# rebuilt. addresses[0] is the IPv4 (100.x) Tailscale address.
data "tailscale_device" "ts_dns" {
  count    = var.enable_split_dns ? 1 : 0
  hostname = "ts-dns"
  wait_for = "60s"
}

resource "tailscale_dns_split_nameservers" "esweiss" {
  count       = var.enable_split_dns ? 1 : 0
  domain      = "esweiss.com"
  nameservers = [data.tailscale_device.ts_dns[0].addresses[0]]
}
