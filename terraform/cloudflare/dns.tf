# DNS Records managed by Terraform
# Note: Service CNAME records (bar, food, plex, home) are managed by external-dns in k3s

# Root domain A record - IP managed by DDNS, config managed by Terraform
resource "cloudflare_record" "root" {
  zone_id = data.cloudflare_zone.external.id
  name    = var.external_domain # ericsweiss.com
  type    = "A"
  content = "104.156.98.15" # Initial/placeholder value - updated by DDNS
  proxied = true            # Cloudflare proxy (orange cloud) enabled
  comment = "Managed by Terraform - IP updated by cloudflare-ddns CronJob in k3s"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it
    ignore_changes = [content]
  }
}
