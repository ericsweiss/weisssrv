# Cloudflare static resource configuration
# DNS records are managed by external-dns in the K3s cluster

data "cloudflare_zone" "external" {
  name = var.external_domain
}

# Zone settings configured to match current Cloudflare configuration
# with HSTS and Always Use HTTPS enabled for improved security
resource "cloudflare_zone_settings_override" "external" {
  zone_id = data.cloudflare_zone.external.id

  settings {
    # SSL/TLS settings
    ssl                      = "full" # Full SSL mode (Cloudflare to origin with self-signed cert allowed)
    always_use_https         = "on"   # Force HTTP → HTTPS redirects
    min_tls_version          = "1.2"  # Minimum TLS version
    automatic_https_rewrites = "on"   # Rewrite HTTP links to HTTPS
    tls_1_3                  = "on"   # Enable TLS 1.3

    # Performance settings
    # Note: http2, polish, mirage, webp are read-only via API
    http3       = "on"  # HTTP/3 (QUIC) enabled
    zero_rtt    = "off" # 0-RTT Connection Resumption disabled
    early_hints = "off" # Early Hints disabled (can enable for performance)
    brotli      = "on"  # Brotli compression
    # Minify is managed via separate Cloudflare Page Rules or dashboard

    # Caching settings
    cache_level       = "aggressive" # Current setting per Terraform state
    browser_cache_ttl = 14400        # 4 hours

    # Development mode (off for production)
    development_mode = "off"

    # Security headers (HSTS)
    # Enforces HTTPS for 1 year and applies to all subdomains
    security_header {
      enabled            = true
      max_age            = 31536000 # 1 year
      include_subdomains = true     # Apply to *.ericsweiss.com
      nosniff            = true     # Prevent MIME sniffing
    }
  }
}

# Page Rules can be added here as needed
# Example:
# resource "cloudflare_page_rule" "cache_everything" {
#   zone_id = data.cloudflare_zone.external.id
#   target  = "*.${var.external_domain}/*"
#   priority = 1
#
#   actions {
#     cache_level = "cache_everything"
#   }
# }
