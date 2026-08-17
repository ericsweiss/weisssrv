# Tailnet policy (ACLs, SSH rules, route auto-approvers) plus the esweiss.com
# Split-DNS entry, as code. autoApprovers is what makes subnet-route failover
# across the six Proxmox hosts real, and the ssh rules are the remote-access
# authorization posture.
#
# APPLY IS SUPERVISED — see README.md. Review the plan against the live tailnet
# ACL; a bad ACL severs tailnet/SSH access.
#
# The resource shape and its guardrails (`reset_acl_on_destroy = false`,
# `prevent_destroy` on the ACL) come from the weisssrv-lib `tailscale-acl` module
# at a pinned ref; `policy.hujson` and the Split-DNS map (split_dns.tf) are this
# site's data. The ref is NOT covered by scripts/check-lib-pins.py — bump it by
# hand together with variables.WEISSSRV_LIB_REF.
#
# `file()` is called HERE, in the root module: `path.module` inside the module
# would resolve to the module's own directory.
module "tailnet" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/tailscale-acl?ref=v0.9.5"

  acl_policy = file("${path.module}/policy.hujson")
  split_dns  = local.split_dns
}
