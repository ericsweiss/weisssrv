# Zone-level Cloudflare configuration. Records are split between external-dns
# (service CNAMEs, from the k3s IngressRoutes) and dns.tf (apex, DDNS-tracked A
# records, CAA, SPF/DMARC, nested wildcards).
#
# The zone SHAPE — the settings block and the four per-record destroy/drift
# protection classes — comes from the weisssrv-lib `cloudflare-zone` module at a
# pinned ref; the record inventory in dns.tf is this site's data. This layer is
# that module's only live consumer, so a behavioural change to it surfaces in a
# real `terraform plan` here rather than only in the cluster template's render
# check. (terraform/authentik is the same arrangement over `authentik-sso`.)
#
# The ref is NOT covered by scripts/check-lib-pins.py (that script gates the
# `include:` list and ansible/requirements.yml only) — bump it by hand together
# with variables.WEISSSRV_LIB_REF.
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=v0.13.2"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain

  # Passed explicitly rather than inherited from the module defaults: a library
  # default change must never silently move this zone's TLS posture on a ref
  # bump. Managing these settings at all is why the Terraform token carries Zone
  # Settings:Edit — a scope deliberately absent from the "Cloudflare DNS Token"
  # item shared with the in-cluster ESO consumers (see variables.tf).
  zone_settings = {
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
