# State-address migration for the move onto the library `tailscale-acl` module
# (main.tf). Both resources kept their configuration; only their addresses
# changed. Without these blocks Terraform would plan a destroy+create of the
# tailnet ACL and of the esweiss.com Split-DNS entry — the two changes this
# module spends its README warning about.
#
# `moved` is not affected by `prevent_destroy`, and re-applying is a no-op once
# state carries the new addresses.

moved {
  from = tailscale_acl.policy
  to   = module.tailnet.tailscale_acl.this
}

moved {
  from = tailscale_dns_split_nameservers.esweiss
  to   = module.tailnet.tailscale_dns_split_nameservers.this["esweiss.com"]
}

# data.tailscale_device.ts_dns needs no `moved`: data sources are re-read on
# every plan, so its move into the module (as
# module.tailnet.data.tailscale_device.split_dns["esweiss.com"]) is invisible to
# state.
#
# ONE SAFEGUARD IS LOST IN THE MOVE, deliberately noted rather than assumed
# away: the pre-module lookup filtered the address list to the IPv4 entry
# (`one([for a in ...addresses : a if !strcontains(a, ":")])`) precisely so an
# ordering change in the provider or the API could not repoint tailnet
# `esweiss.com` resolution. The library module indexes `addresses[0]` instead.
# That resolves to the same 100.x address today, but as a property of the
# provider's ordering, not of this configuration. Until the filter is restored
# upstream, the supervised apply must CONFIRM THE PLANNED NAMESERVER IS THE
# 100.x ADDRESS (README § Split-DNS) — a v6 value there breaks `*.esweiss.com`
# for every tailnet client.
