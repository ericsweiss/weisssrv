# terraform/b2 — Backblaze B2 offsite-backup bucket as code

Codifies the settings of the **existing** Backblaze B2 bucket
`weisssrv-backup` (bucket id `4ef45c874b3188409cf10a11`) that holds the estate's
nightly offsite restic repository (docs/42 "Offsite Backup"; day-2 runbook in
docs/12 "Backup and Recovery"). Mirrors the
`terraform/authentik` / `terraform/tailscale` pattern: GitLab HTTP state backend
+ 1Password-injected credentials, its own state name, a read-only CI
`b2-drift-plan` job, and a **supervised** apply.

The bucket is **ADOPTED, never created**: the native `import {}` block
(`imports.tf`) binds the managed resource to the live bucket by its
`bucket_id`, and `lifecycle { prevent_destroy = true }` (`main.tf`) is a hard
backstop against ever destroying the only offsite copy of the backups.

## ⚠️ Apply is a supervised step

`terraform apply` here reconciles the live bucket's versioning / lifecycle /
server-side-encryption settings. A bad lifecycle rule could start **expiring
backup data**, so — like `terraform/tailscale` — the `terraform:b2-apply`
Taskfile task refuses `-auto-approve`: review the plan and type `yes`. CI runs
only the read-only `b2-drift-plan` job (`.gitlab-ci.yml`):
`terraform plan -detailed-exitcode`, `allow_failure: true`, on the schedule and
on b2-module MRs — an out-of-band Backblaze-console edit surfaces as drift
instead of being silently reverted later.

## What is managed

| Kind | Resource | Import ID |
|---|---|---|
| B2 bucket (`weisssrv-backup`) | `b2_bucket.weisssrv_backup` | bucket_id `4ef45c874b3188409cf10a11` |

Managed settings on the bucket:

- **`bucket_type = "allPrivate"`** — no public downloads.
- **`default_server_side_encryption` (SSE-B2 / AES256)** — a redundant
  server-side layer under restic's primary client-side encryption (the repo
  password is the `rclone_crypt_password` field of the `B2 Archive Backup`
  1Password item).
- **`lifecycle_rules` (`days_from_hiding_to_deleting = 30`)** — rclone's b2
  backend deletes-by-hiding, so restic forget/prune leaves HIDDEN versions;
  this expires them 30 days after they are hidden. `days_from_uploading_to_hiding`
  is intentionally unset so a live version is never auto-hidden.

## What is deliberately UNMANAGED (and why)

- **Bucket contents / files** — Terraform manages bucket *settings* only; the
  restic repo objects are written by the `restic_offsite` Ansible role on
  pve-nas-01 (docs/42; day-2 runbook docs/12 "Backup and Recovery").
- **Application keys** — the B2 app keys live in the `B2 Archive Backup`
  1Password item (docs/15), rotated out-of-band. The bucket runs on a
  **capability-restricted** key (`listBuckets,listFiles,readFiles,writeFiles,
  readBucketEncryption` — no `deleteFiles`): rclone deletes-by-hiding and the
  lifecycle rule below expires the hidden versions, so hide-only prune needs
  only `writeFiles`. Because it needs no elevated capability, the restricted key
  ships **at merge** (an at-merge Step 0, docs/42) rather than as a post-merge
  swap — mint it with
  `b2 key create --bucket weisssrv-backup weisssrv-restic-offsite listBuckets,listFiles,readFiles,writeFiles,readBucketEncryption`
  and store the returned id/key in the `B2 Archive Backup` item before the
  storage deploy.

## Credentials

Both the Taskfile tasks and the CI `b2-drift-plan` job read the B2 application
key from the `B2 Archive Backup` 1Password item (the same item the
`restic_offsite` role consumes):

- `TF_VAR_b2_application_key_id` ← `op://Homelab/B2 Archive Backup/b2_key_id`
- `TF_VAR_b2_application_key` ← `op://Homelab/B2 Archive Backup/b2_application_key`

State backend auth (`TF_HTTP_*`) points at the b2-specific state name
`terraform/state/b2` so it does not collide with the sibling modules.

## Usage

```bash
task terraform:b2-init      # init against the GitLab state backend
task terraform:b2-plan      # read-only diff vs the live bucket
task terraform:b2-apply     # SUPERVISED — review the plan, type yes (no -auto-approve)
task terraform:b2-import    # one-time / DR: adopt the existing bucket into state
```

The first `terraform:b2-plan`/`apply` (or `b2-import`) adopts the bucket via the
`import {}` block; subsequent plans are no-ops except the versioning / lifecycle
/ SSE reconciliation. `terraform fmt` / `terraform validate` (the multi-module
CI jobs and `task terraform:fmt` / `terraform:validate-local`) pick this module
up automatically — they glob every directory under `terraform/` with a
`versions.tf`.
