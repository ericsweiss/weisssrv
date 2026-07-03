# Cloudflare static resource configuration
# DNS records are managed by external-dns in the K3s cluster

# account_id scopes the lookup — Cloudflare zone names are not unique across
# accounts, so a like-named zone visible to the token would make a name-only
# lookup ambiguous.
data "cloudflare_zone" "external" {
  account_id = var.cloudflare_account_id
  name       = var.external_domain
}

# Zone-wide TLS/perf/caching settings (HSTS + Always Use HTTPS enforced)
resource "cloudflare_zone_settings_override" "external" {
  zone_id = data.cloudflare_zone.external.id

  settings {
    ssl                      = "strict" # Full (strict): requires a valid cert on origin
    always_use_https         = "on"
    min_tls_version          = "1.2"
    automatic_https_rewrites = "on"
    tls_1_3                  = "on"

    # http2, polish, mirage, webp are read-only via API; Auto Minify was retired
    # by Cloudflare in 2024 (no minify setting exists).
    http3       = "on"
    zero_rtt    = "off"
    early_hints = "off"
    brotli      = "on"

    cache_level       = "aggressive"
    browser_cache_ttl = 14400 # 4 hours

    development_mode = "off"

    security_header { # HSTS
      enabled            = true
      max_age            = 31536000 # 1 year
      include_subdomains = true
      nosniff            = true
      # preload intentionally omitted — submission to the browser HSTS preload
      # list is a hard-to-reverse commitment we don't want for this domain.
    }
  }
}
