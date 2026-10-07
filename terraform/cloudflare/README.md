# terraform/cloudflare — external DNS + zone settings

Manages the `ericsweiss.com` Cloudflare zone: the records external-dns cannot
express, and the zone-wide TLS/caching settings. Same pattern as the
`terraform/tailscale` / `terraform/authentik` siblings (GitLab HTTP state
backend + 1Password-injected credentials) with one crucial difference:

> **This is the only Terraform module that auto-applies to production.** On a
> merge to `main` touching `terraform/cloudflare/**`, the `deploy-terraform` CI
> job runs `terraform apply -auto-approve` against the saved plan. There is **no
> pre-merge plan in CI**: while `OP_SERVICE_ACCOUNT_TOKEN` is masked+protected
> the `terraform-plan` job's `merge_request_event` rule cannot match, so the job
> is inert and the MR widget shows nothing (see its rule comment in
> `.gitlab-ci.yml`, which is the authority). The mandatory pre-merge review is a
> local `task terraform:cloudflare-plan`.

## Shape from the library, records from here

`main.tf` is a thin caller of the weisssrv-lib **`cloudflare-zone`** module at a
pinned `?ref=`: the module owns the zone-settings resource and the four
per-record lifecycle classes, `dns.tf` owns this site's record inventory
(`local.dns_records`). This layer is the module's only live consumer, so a
behavioural module change shows up in a real `terraform plan` here instead of
only in the cluster template's render check.

Two consequences:

- **The ref is bumped by hand.** `scripts/check-lib-pins.py` gates the
  `include:` list and `ansible/requirements.yml`; it does not read Terraform
  module sources. Bump `?ref=` in this `main.tf` and in every other root's
  `main.tf` under `terraform/` in the same MR as `variables.WEISSSRV_LIB_REF`
  — `scripts/test_site_configs.py` fails if any of them disagrees.
- **`terraform init` clones `weisssrv-lib` over HTTPS.** In CI that is already
  covered: `.gitlab-ci.yml`'s global `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0` pair
  rewrites credential-less `https://$CI_SERVER_HOST/` URLs to the job token (the
  same mechanism `ansible-galaxy` uses for the collection), so `terraform-plan`,
  `deploy-terraform` and `terraform-validate` authenticate without extra config.
  Locally the operator's own git credentials cover it.

`zone_settings` is passed explicitly rather than left to the module defaults, so
a library default change cannot move this zone's TLS posture on a ref bump.

## Who owns which record

| Owner | Records | Why |
|---|---|---|
| **Terraform** (`dns.tf`) | apex `A`, `git`, `direct`, `vpn` (A); `photos` + the `registry/pages/ide` nested CNAMEs; CAA set; SPF + DMARC TXT | external-dns cannot express wildcards, nested subdomains, CAA/TXT, or the DDNS-tracked A records |
| **external-dns** (in-cluster) | service CNAMEs derived from IngressRoutes (`auth`, `bar`, `food`, `plex`, `home`, …) | annotation-driven, follows the route |
| **cloudflare-ddns CronJob** (`kubernetes/infrastructure/configs/cloudflare-ddns`) | the *content* (IP) of the four A records above | the home WAN IP is dynamic; each record sets `content_managed_externally`, so the module gives it `ignore_changes = [content]` and Terraform never reverts it. `proxied`/`ttl` stay Terraform-owned — the CronJob preserves the live values on update |

Two zone records are deliberately dashboard-managed and never planned: the null
`MX` (disables inbound mail) and the `google-site-verification` apex TXT.

`main.tf` additionally pins the zone settings — `ssl = strict`, Always Use
HTTPS, `min_tls_version 1.2`, HSTS (1 year, includeSubDomains, no preload),
HTTP/3, Brotli, cache level.

The DDNS placeholder in `dns.tf` is `192.0.2.1` (RFC 5737 TEST-NET-1), not a real
address: a fresh apply is *expected* to resolve those four records to something
unroutable until the CronJob's next `*/5` run. Seeding from the live WAN IP would
rot in git and eventually publish a lease that belongs to someone else.

## prevent_destroy policy

Every record sets `protected = true`, which routes it to a module resource
carrying `lifecycle { prevent_destroy = true }` — because an auto-applying module
plus a counts-only MR widget means a deleted or renamed map key would drop a
record from public DNS with no confirmation step.

`prevent_destroy` refuses any plan that destroys a protected record, and a flag
change re-addresses the record (`cloudflare_record.protected["<key>"]` →
`cloudflare_record.this["<key>"]`), which Terraform plans as a destroy. So
clearing the flag on its own does not work — it is the blocked step. Two paths:

- **Un-protecting a record you are keeping.** In one MR, set `protected = false`
  AND add a root `moved { from = module.zone.cloudflare_record.protected["<key>"]
  to = module.zone.cloudflare_record.this["<key>"] }`. `moved` re-addresses state
  instead of destroying, so the apply is clean.
- **Deleting a record.** Do the un-protect MR above first, then delete the map
  entry in a second MR — the record is now an ordinary `cloudflare_record.this`
  and the destroy applies. Both MRs auto-apply on merge, so read each plan
  locally first.

`prevent_destroy` does not block in-place updates, and `moved` blocks (state
renames — `moved.tf` holds the ones from the pre-module layout) are unaffected.

The `deploy-terraform` job adds a second guard outside Terraform: it counts the
deletes in the saved plan and refuses the apply if there are any, protected or
not, and a replacement counts as a delete. The delete-a-record MR above is what
trips it. When the deletion is the reviewed intent, either apply from your
workstation with `task terraform:cloudflare-apply`, or re-run the job from the
pipeline with the variable `ALLOW_DNS_DELETE=1`.

`moved.tf` is the permanent map of the pre-module state addresses: the blocks
stay, and removing one rides a supervised plan and apply.

The zone-settings override is protected the same way at the pinned ref, so
flipping `manage_zone_settings = false` — which would revert `ssl`/HSTS/
`min_tls_version` zone-wide — plans a destroy that `prevent_destroy` refuses.
The deliberate path is
`task terraform:cloudflare-state -- rm 'module.zone.cloudflare_zone_settings_override.this[0]'`
first, then flipping the variable. Removing the whole `module "zone"` block
likewise fails on the records' `prevent_destroy`.

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
task terraform:cloudflare-init     # terraform init (GitLab state backend)
task terraform:cloudflare-plan     # review the diff vs the live zone
task terraform:cloudflare-apply    # normally unnecessary — CI applies on merge to main
```

The unprefixed `task terraform:init/plan/apply` remain as aliases for these
three; every other root is addressed by its own prefix (`terraform:tailscale-*`,
`terraform:authentik-*`, `terraform:unifi-*`).
