# Native import block (Terraform >= 1.5; the repo is on 1.15) binding the
# managed bucket resource to the live B2 bucket by its bucket_id. Idempotent:
# once the bucket is in state the block is a no-op, so this file is the
# permanent record of the state<->API identity map and the disaster-recovery
# re-import path (fresh state: `terraform plan`/`apply` re-adopts the bucket;
# expected result is "1 to import, 0 to add/destroy" plus only the
# lifecycle/versioning/SSE reconciliation as in-place changes).
#
# The bucket is ADOPTED, never created (see main.tf). An import.sh fallback
# (legacy `terraform import`) mirrors terraform/authentik for DR toolchains
# that predate native import blocks.
import {
  to = b2_bucket.weisssrv_backup
  # Existing bucket id for "weisssrv-backup" — do NOT create a new bucket.
  id = "4ef45c874b3188409cf10a11"
}
