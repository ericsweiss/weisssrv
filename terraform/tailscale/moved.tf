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
# state. The module selects the device's IPv4 address explicitly and
# preconditions on there being exactly one, so the nameserver cannot silently
# become a v6 value (README § Split-DNS).
