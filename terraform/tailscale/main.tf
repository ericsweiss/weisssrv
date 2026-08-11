# Tailnet policy (ACLs, SSH rules, route auto-approvers) as code. autoApprovers
# is what makes subnet-route failover across the six Proxmox hosts real, and the
# ssh rules are the remote-access authorization posture.
#
# APPLY IS SUPERVISED — see README.md. Review the plan against the live tailnet
# ACL; a bad ACL severs tailnet/SSH access.
resource "tailscale_acl" "policy" {
  acl = file("${path.module}/policy.hujson")

  # Do NOT reset to the default allow-all ACL on destroy. Once the policy is
  # tightened past the current allow-all baseline, an accidental destroy/resource
  # removal reverting the tailnet to allow-all would be a silent security
  # regression — preserve the last-applied policy instead. prevent_destroy makes
  # tearing the ACL down an explicit break-glass action (remove this first).
  reset_acl_on_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}
