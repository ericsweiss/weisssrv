# CRITICAL: apply is supervised (README.md). Review the plan against the live
# tailnet ACL, because a bad one severs tailnet SSH. The resource shape and its
# guardrails come from the weisssrv-lib `tailscale-acl` module; `policy.hujson`
# and the Split-DNS map (split_dns.tf) are this site's data. The `?ref=` below is
# not covered by scripts/check-lib-pins.py: bump it by hand alongside
# variables.WEISSSRV_LIB_REF. `file()` is called here, in the root module,
# because `path.module` inside the module resolves to the module's directory.
module "tailnet" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/tailscale-acl?ref=v0.18.0"

  acl_policy = file("${path.module}/policy.hujson")
  split_dns  = local.split_dns
}
