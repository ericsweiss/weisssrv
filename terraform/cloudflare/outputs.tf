output "zone_id" {
  description = "Cloudflare zone ID for ericsweiss.com"
  value       = data.cloudflare_zone.external.id
}

output "external_domain" {
  description = "External domain name"
  value       = var.external_domain
}

output "zone_status" {
  description = "Zone status"
  value       = data.cloudflare_zone.external.status
}

output "name_servers" {
  description = "Cloudflare nameservers for the zone"
  value       = data.cloudflare_zone.external.name_servers
}
