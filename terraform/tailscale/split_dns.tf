# Split-DNS: route esweiss.com queries from Tailscale clients to the in-cluster
# CoreDNS resolver (the "ts-dns" operator device), REPLACING the console entry
# that pointed at AdGuard .150/.160. The resolver returns a CNAME to the
# traefik-tailnet device for Traefik-fronted names (mesh path) and forwards the
# rest to AdGuard. See kubernetes/apps/tailnet-dns and docs/05-tailscale.md.
#
# This was gated behind `enable_split_dns` (default false) so the ACL could be
# applied on its own first, back when the ts-dns device did not exist yet. That
# phased rollout is DONE: the device is registered, the entry is in state and
# live (`tailscale dns status` on a node shows `esweiss.com -> 100.87.106.104`).
# The gate is removed rather than defaulted to true because nothing ever set
# TF_VAR_enable_split_dns — not the Taskfile's tf_tailscale_env anchor, not the
# tailscale-drift-plan job — so every plan computed count = 0 and proposed
# DESTROYING the live entry: permanent false drift in the drift job, and a
# routine supervised ACL apply one confirmation away from deleting tailnet
# *.esweiss.com resolution.

# Read the ts-dns device's tailnet IP by its deterministic hostname (set via the
# Service's tailscale.com/hostname annotation). Derives the 100.x at apply time
# rather than hardcoding a runtime-assigned address; self-heals if the device is
# rebuilt. addresses[0] is the IPv4 (100.x) Tailscale address.
data "tailscale_device" "ts_dns" {
  hostname = "ts-dns"
  wait_for = "60s"
}

resource "tailscale_dns_split_nameservers" "esweiss" {
  domain      = "esweiss.com"
  nameservers = [data.tailscale_device.ts_dns.addresses[0]]

  # Same reasoning as the ACL in main.tf: dropping this entry silently breaks
  # *.esweiss.com resolution for every tailnet client (the mesh path in
  # docs/05-tailscale.md), so removing it has to be an explicit break-glass
  # edit rather than a side effect of a refactor.
  lifecycle {
    prevent_destroy = true
  }
}
