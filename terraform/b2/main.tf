# The single offsite-backup bucket. ADOPTED (never created) from the existing
# B2 bucket via the native import block in imports.tf — the bucket already holds
# the only offsite copy of the estate, so this module reconciles its versioning
# / lifecycle / SSE settings without ever recreating it (bucket_name is
# ForceNew, and prevent_destroy below is a hard backstop).
resource "b2_bucket" "weisssrv_backup" {
  bucket_name = "weisssrv-backup"
  bucket_type = "allPrivate"

  # Keep client-side restic encryption (repo password = the rclone_crypt_password
  # field of the "B2 Archive Backup" item) as the PRIMARY at-rest layer; SSE-B2
  # is a redundant belt-and-suspenders server-side layer. AES256 is the only
  # algorithm B2 supports.
  default_server_side_encryption {
    mode      = "SSE-B2"
    algorithm = "AES256"
  }

  # B2 versioning is always on (it keeps every version). rclone's b2 backend
  # deletes-by-hiding (hard_delete is left unset/false in the restic_offsite
  # role's rclone.conf), so restic forget/prune leaves HIDDEN versions behind;
  # this lifecycle rule expires those hidden versions 30 days after they are
  # hidden. days_from_uploading_to_hiding is intentionally UNSET so a live
  # (current) version is never auto-hidden — only restic's own prune hides.
  lifecycle_rules {
    file_name_prefix             = ""
    days_from_hiding_to_deleting = 30
  }

  lifecycle {
    # The bucket holds the only offsite copy — never let an apply (a bad
    # rename/type edit) destroy it. Removal is a deliberate two-step: drop this
    # block in its own commit, then destroy.
    prevent_destroy = true
  }
}
