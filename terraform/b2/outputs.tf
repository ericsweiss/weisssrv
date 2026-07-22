output "bucket_id" {
  description = "B2 bucket ID for weisssrv-backup (the restic offsite repo bucket)"
  value       = b2_bucket.weisssrv_backup.bucket_id
}

output "bucket_name" {
  description = "B2 bucket name"
  value       = b2_bucket.weisssrv_backup.bucket_name
}
