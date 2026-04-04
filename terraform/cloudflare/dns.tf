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

# =============================================================================
# GitLab DNS Records
# These are managed by Terraform (not external-dns) because:
# - Subdomains like registry.git require explicit management
# - Wildcard domains (*.pages.git) aren't supported by external-dns annotations
# =============================================================================

# GitLab Web UI + SSH - git.ericsweiss.com
# DNS-only mode allows both HTTPS (via Traefik) and SSH access on the same hostname
# Note: Origin IP is already exposed via direct.ericsweiss.com, so no additional security impact
# CUTOVER NOTE: On first apply, DNS may briefly resolve to the placeholder IP until
# the DDNS CronJob updates it. Apply during low-traffic window and run
# `task k3s:deploy-ddns` immediately after to trigger an update.
resource "cloudflare_record" "git" {
  zone_id = data.cloudflare_zone.external.id
  name    = "git"
  type    = "A"
  content = "104.156.98.15" # Initial/placeholder value - updated by DDNS
  proxied = false           # DNS-only to allow SSH traffic
  comment = "GitLab Web + SSH - DNS only, TLS via Traefik, IP updated by DDNS"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it
    ignore_changes = [content]
  }
}

# GitLab Container Registry - registry.git.ericsweiss.com
# Nested subdomain - not covered by Universal SSL, use direct access
resource "cloudflare_record" "registry_git" {
  zone_id = data.cloudflare_zone.external.id
  name    = "registry.git"
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false
  comment = "GitLab Container Registry - DNS only, TLS via Traefik"
}

# GitLab Pages - pages.git.ericsweiss.com (apex for pages)
# Nested subdomain - not covered by Universal SSL, use direct access
resource "cloudflare_record" "pages_git" {
  zone_id = data.cloudflare_zone.external.id
  name    = "pages.git"
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false
  comment = "GitLab Pages apex - DNS only, TLS via Traefik"
}

# Direct A record (DNS-only) for services that can't use Cloudflare proxy
# Used by GitLab Pages wildcard and Container Registry which require nested wildcard certificates
#
# SECURITY NOTE: This record intentionally exposes the origin IP (DNS-only mode).
# This is required for:
# - GitLab Pages wildcard TLS (Cloudflare can't proxy nested wildcards)
# - Container Registry access (requires direct TLS termination)
#
# Note: GitLab SSH now uses git.ericsweiss.com (also DNS-only) for a unified URL.
#
# Protection is provided by:
# - Proxmox firewall restricts access (sg-gitlab, sg-k3s-workers security groups)
# - Only specific ports are open (443, 2222, 5050)
# - Services require authentication (GitLab, Container Registry)
resource "cloudflare_record" "direct" {
  zone_id = data.cloudflare_zone.external.id
  name    = "direct"
  type    = "A"
  content = "104.156.98.15" # Initial/placeholder value - updated by DDNS
  proxied = false           # DNS-only mode (grey cloud) - intentionally exposes origin IP
  comment = "Direct access (no proxy) - IP updated by DDNS"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it
    ignore_changes = [content]
  }
}

# GitLab Pages wildcard - *.pages.git.ericsweiss.com
# Note: Cloudflare Universal SSL only covers first-level wildcards (*.ericsweiss.com).
# Nested wildcards like *.pages.git require Advanced Certificate Manager ($10/mo).
# Using DNS-only mode via direct.ericsweiss.com so Traefik handles TLS with Let's Encrypt cert.
resource "cloudflare_record" "pages_git_wildcard" {
  zone_id = data.cloudflare_zone.external.id
  name    = "*.pages.git"
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false
  comment = "GitLab Pages wildcard - DNS only, TLS via Traefik"
}
