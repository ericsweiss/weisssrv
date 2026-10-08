# State addresses that predate the move onto the library `tailscale-acl`
# module. Re-applying is a no-op once state carries the new addresses; removing
# a block BEFORE that apply plans destroy+create, past `prevent_destroy`.

moved {
  from = tailscale_acl.policy
  to   = module.tailnet.tailscale_acl.this
}

moved {
  from = tailscale_dns_split_nameservers.esweiss
  to   = module.tailnet.tailscale_dns_split_nameservers.this["esweiss.com"]
}

# data.tailscale_device.ts_dns needs no block: data sources are re-read on every
# plan, so its move into the module is invisible to state.
