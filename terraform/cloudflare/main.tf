# Zone-level Cloudflare configuration. The zone shape comes from the weisssrv-lib
# `cloudflare-zone` module at a hand-bumped ref; dns.tf holds this site's records, and
# external-dns owns the service CNAMEs. Keep the ref equal to WEISSSRV_LIB_REF.
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=v0.17.1"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain

  # Both passed explicitly, never inherited: a library default change must not move
  # this zone's TLS posture on a ref bump. Managing them needs the Terraform token's
  # Zone Settings:Edit scope, which the ESO-shared DNS token does not carry.
  manage_zone_settings = true
  zone_settings = {
    ssl                      = "strict" # Full (strict): requires a valid cert on origin
    always_use_https         = "on"
    min_tls_version          = "1.2"
    automatic_https_rewrites = "on"
    tls_1_3                  = "on"

    # http2, polish, mirage and webp are read-only over the API; Auto Minify is not a
    # settable zone setting.
    http3       = "on"
    zero_rtt    = "off"
    early_hints = "off"
    brotli      = "on"

    cache_level       = "aggressive"
    browser_cache_ttl = 14400 # 4 hours

    development_mode = "off"

    hsts = {
      enabled            = true
      max_age            = 31536000 # 1 year
      include_subdomains = true
      nosniff            = true
      # preload intentionally off — submission to the browser HSTS preload list
      # is a hard-to-reverse commitment we don't want for this domain.
      preload = false
    }
  }

  records = local.dns_records
}
