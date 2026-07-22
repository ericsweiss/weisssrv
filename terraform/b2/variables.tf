# B2 application key credentials, injected by the Taskfile / CI via `op run` /
# `op read` from the SAME "B2 Archive Backup" 1Password item the restic_offsite
# Ansible role consumes (docs/15-credential-rotation.md "Required 1Password
# Items"), so Terraform and the backup role can never disagree about the
# account. Never committed, never defaulted.

variable "b2_application_key_id" {
  description = "B2 application key ID (1Password item 'B2 Archive Backup', field 'b2_key_id')"
  type        = string
  sensitive   = true
}

variable "b2_application_key" {
  description = "B2 application key (1Password item 'B2 Archive Backup', field 'b2_application_key')"
  type        = string
  sensitive   = true
}
