output "zone_id" {
  description = "Cloudflare zone ID for the external domain"
  value       = module.zone.zone_id
}

output "external_domain" {
  description = "External domain name"
  value       = var.external_domain
}

output "zone_status" {
  description = "Zone status"
  value       = module.zone.zone_status
}

output "name_servers" {
  description = "Cloudflare nameservers for the zone"
  value       = module.zone.name_servers
}
