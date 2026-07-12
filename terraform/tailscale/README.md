# terraform/tailscale — tailnet policy as code

Manages the Tailscale **ACL policy** (access rules, Tailscale SSH rules, and
subnet-route **auto-approvers**) for the homelab tailnet, mirroring the
`terraform/cloudflare` pattern (HTTP state backend + 1Password-injected
credentials).

Primary motivation: the six Proxmox hosts advertise `192.168.0.0/24` and enable
Tailscale SSH, but route activation requires approval and SSH is governed by
tailnet ACLs — previously unversioned. `policy.hujson` codifies that and adds
`autoApprovers` so any owner-advertised `192.168.0.0/24` route auto-approves,
making subnet-router **failover real** instead of a single-host SPOF.

## ⚠️ Apply is a supervised step

`terraform apply` here **overwrites the live tailnet ACL**. A wrong policy can
sever tailnet connectivity and Tailscale SSH (including the path this repo's
remote admin relies on). Do **not** wire `apply` into CI — a read-only drift
`plan` in CI is fine (and wanted, so Admin-console hot-fixes surface instead
of being silently reverted at the next apply), but `apply` stays supervised.
Review `policy.hujson` against the current Admin console ACL first.

## One-time setup

1. **OAuth client** — Admin console → Settings → OAuth clients → generate a
   client with the **`acl`** scope (write). Store id + secret in a 1Password
   item `Tailscale OAuth` (fields `client id`, `credential`).
2. **Verify the owner identity** in `policy.hujson` (`autoApprovers.routes`)
   matches your tailnet owner (currently `ericsweiss1@gmail.com`).
3. **State backend** — use a tailscale-specific GitLab state name so it does not
   collide with the cloudflare state, e.g.:
   ```
   export TF_HTTP_ADDRESS="https://git.ericsweiss.com/api/v4/projects/1/terraform/state/tailscale"
   export TF_HTTP_LOCK_ADDRESS="$TF_HTTP_ADDRESS/lock"
   export TF_HTTP_UNLOCK_ADDRESS="$TF_HTTP_ADDRESS/lock"
   export TF_HTTP_LOCK_METHOD=POST      # GitLab state backend locks via POST
   export TF_HTTP_UNLOCK_METHOD=DELETE  # and unlocks via DELETE (else apply → 405)
   # plus TF_HTTP_USERNAME / TF_HTTP_PASSWORD as in the cloudflare tasks
   ```

## Supervised apply

```bash
cd terraform/tailscale
export TF_VAR_tailscale_oauth_client_id=$(op read "op://Homelab/Tailscale OAuth/client id")
export TF_VAR_tailscale_oauth_client_secret=$(op read "op://Homelab/Tailscale OAuth/credential")
terraform init
terraform plan          # review carefully — confirm the diff vs the live ACL
# First apply adopts the ACL. If the resource reports the ACL already has
# content, either import it (terraform import tailscale_acl.policy acl) or set
# overwrite_existing_content=true on the resource for the first apply only.
terraform apply
```

After apply, confirm remote access still works (SSH to a Proxmox host over the
tailnet) and that `tailscale status` shows the `192.168.0.0/24` route approved
on all six hosts.

## Tightening (follow-up)

The initial `acls` block preserves full member access (non-breaking). Once
validated, scope it to tags/groups and tag the Proxmox subnet routers
(`tag:subnet-router`) — see `docs/05-tailscale.md`. Adding `terraform:tailscale-*`
Taskfile wrappers (op-run + TF_HTTP_*) is a small follow-up.
