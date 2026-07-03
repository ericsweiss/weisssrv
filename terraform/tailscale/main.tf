# Tailnet policy (ACLs, SSH rules, route auto-approvers) as code.
#
# Why this exists: the six Proxmox hosts advertise 192.168.0.0/24 and enable
# Tailscale SSH, but a route is only ACTIVE once approved, and SSH access is
# governed by tailnet ACLs — none of which was versioned. Codifying it here
# (a) makes subnet-route HA real via autoApprovers (any owner-advertised
# 192.168.0.0/24 route auto-approves, so failover across the 6 hosts works) and
# (b) brings the remote-access/SSH authorization posture under GitOps review.
#
# APPLY IS SUPERVISED — see README.md. policy.hujson must be reviewed against the
# live tailnet ACL before the first apply (a bad ACL can sever tailnet/SSH access).
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
