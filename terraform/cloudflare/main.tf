# Cloudflare static resource configuration
# DNS records are managed by external-dns in the K3s cluster

data "cloudflare_zone" "external" {
  name = var.external_domain
}

# Zone-wide TLS/perf/caching settings (HSTS + Always Use HTTPS enforced)
resource "cloudflare_zone_settings_override" "external" {
  zone_id = data.cloudflare_zone.external.id

  settings {
    # SSL/TLS settings
    ssl                      = "strict" # Full (strict) SSL mode - requires valid cert on origin
    always_use_https         = "on"
    min_tls_version          = "1.2"
    automatic_https_rewrites = "on" # Rewrite HTTP links to HTTPS
    tls_1_3                  = "on"

    # Performance settings
    # Note: http2, polish, mirage, webp are read-only via API
    http3       = "on"
    zero_rtt    = "off"
    early_hints = "off" # Early Hints disabled (can enable for performance)
    brotli      = "on"
    # Auto Minify was retired by Cloudflare in 2024; no minify setting exists

    # Caching settings
    cache_level       = "aggressive" # Cache all static content (default for proxied zones)
    browser_cache_ttl = 14400        # 4 hours

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
