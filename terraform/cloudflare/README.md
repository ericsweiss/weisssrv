# terraform/cloudflare — external DNS + zone settings

Manages the `ericsweiss.com` Cloudflare zone: the records external-dns cannot
express, and the zone-wide TLS/caching settings. Same pattern as the
`terraform/tailscale` / `terraform/authentik` siblings (GitLab HTTP state
backend + 1Password-injected credentials) with one crucial difference:

> **This is the only Terraform module that auto-applies to production.** On a
> merge to `main` touching `terraform/cloudflare/**`, the `deploy-terraform` CI
> job runs `terraform apply -auto-approve` against the saved plan. The MR widget
> shows change **counts**, not which records — so review the plan output in the
> `terraform-plan` job before merging.

## Who owns which record

| Owner | Records | Why |
|---|---|---|
| **Terraform** (`dns.tf`) | apex `A`, `git`, `direct`, `vpn` (A); `photos` + the `registry/pages/ide` nested CNAMEs; CAA set; SPF + DMARC TXT | external-dns cannot express wildcards, nested subdomains, CAA/TXT, or the DDNS-tracked A records |
| **external-dns** (in-cluster) | service CNAMEs derived from IngressRoutes (`auth`, `bar`, `food`, `plex`, `home`, …) | annotation-driven, follows the route |
| **cloudflare-ddns CronJob** (`kubernetes/apps/cloudflare-ddns`) | the *content* (IP) of the four A records above | the home WAN IP is dynamic; each record carries `ignore_changes = [content]` so Terraform never reverts it. `proxied`/`ttl` stay Terraform-owned — the CronJob preserves the live values on update |

Two zone records are deliberately dashboard-managed and never planned: the null
`MX` (disables inbound mail) and the `google-site-verification` apex TXT.

`main.tf` additionally pins the zone settings — `ssl = strict`, Always Use
HTTPS, `min_tls_version 1.2`, HSTS (1 year, includeSubDomains, no preload),
HTTP/3, Brotli, cache level.

## prevent_destroy policy

Every resource in this module carries `lifecycle { prevent_destroy = true }`,
because an auto-applying module plus a counts-only MR widget means a deleted or
renamed resource block (or a `for_each` key) would drop a record from public DNS
with no confirmation step. Removing one is a deliberate **two-step** change:

1. delete the `lifecycle` block, commit/merge;
2. delete the resource, commit/merge.

`prevent_destroy` does not block in-place updates, and `moved` blocks (state
renames) are unaffected.

## Credentials and state backend

Both come from 1Password via `op run` (Taskfile locally, `op read` in CI):

| Variable | 1Password reference |
|---|---|
| `TF_VAR_cloudflare_api_token` | `op://Homelab/Cloudflare Terraform Token/credential` |
| `TF_VAR_cloudflare_account_id` | `op://Homelab/Cloudflare Terraform Token/username` |
| `TF_HTTP_PASSWORD` (state) | `op://Homelab/GitLab Terraform State Token/credential` |

The Terraform token needs **Zone:Read + DNS:Edit + Zone Settings:Edit**. It is
deliberately a *different* item from `Cloudflare DNS Token` (the one ESO hands to
in-cluster consumers and acme.sh), which holds DNS:Edit + Zone:Read only — so a
compromised pod cannot downgrade zone-wide TLS posture. Rotation:
`docs/15-credential-rotation.md`.

```
.../terraform/state/cloudflare   (+ /lock)
TF_HTTP_LOCK_METHOD=POST         # GitLab state backend locks via POST
TF_HTTP_UNLOCK_METHOD=DELETE     # and unlocks via DELETE (else apply → 405)
```

## Taskfile wrappers

```bash
task terraform:init     # terraform init (GitLab state backend)
task terraform:plan     # review the diff vs the live zone
task terraform:apply    # normally unnecessary — CI applies on merge to main
```
