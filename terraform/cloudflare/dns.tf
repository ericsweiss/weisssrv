# CRITICAL: record inventory (inputs) for the library `cloudflare-zone` module in
# main.tf. Every record sets `protected = true`, so the module gives it
# `prevent_destroy`: deleting or renaming a map key needs a state re-address first
# or the auto-apply on main fails. `content_managed_externally = true` hands the
# live IP to the cloudflare-ddns CronJob. Record ownership, the removal procedure
# and token scopes: README.md.
locals {
  # RFC 5737 TEST-NET-1, unroutable: the four DDNS records resolve here until the
  # CronJob's next */5 run (README.md).
  ddns_placeholder_ip = "192.0.2.1"

  # Issuance restricted to Let's Encrypt (cert-manager + acme.sh) plus the
  # Cloudflare Universal SSL partner CAs. Cloudflare auto-injects CAA for its
  # other partner CAs outside this config.
  caa_records = {
    caa_issue_letsencrypt     = { tag = "issue", value = "letsencrypt.org", comment = "Restrict cert issuance to Let's Encrypt" }
    caa_issuewild_letsencrypt = { tag = "issuewild", value = "letsencrypt.org", comment = "Restrict wildcard cert issuance to Let's Encrypt" }
    caa_issue_pki_goog        = { tag = "issue", value = "pki.goog", comment = "Allow Cloudflare Universal SSL partner CA (Google Trust Services)" }
    caa_issuewild_pki_goog    = { tag = "issuewild", value = "pki.goog", comment = "Allow Cloudflare Universal SSL partner CA wildcard (Google Trust Services)" }
    caa_issue_ssl_com         = { tag = "issue", value = "ssl.com", comment = "Allow Cloudflare Universal SSL partner CA (SSL.com)" }
    caa_issuewild_ssl_com     = { tag = "issuewild", value = "ssl.com", comment = "Allow Cloudflare Universal SSL partner CA wildcard (SSL.com)" }

    # iodef is published in public DNS by design: RFC 8659 §4.1.3 wants an
    # operator-reachable channel and no role inbox exists.
    caa_iodef = { tag = "iodef", value = "mailto:ericsweiss1@gmail.com", comment = "CAA violation reports go here" }
  }

  # Nested subdomains/wildcards pointing at `direct` (DNS-only, TLS via Traefik):
  # Universal SSL covers first-level wildcards only and external-dns annotations
  # cannot express wildcards. The ide.git pair is the Web IDE extension host (docs/27).
  gitlab_direct_cnames = {
    "registry.git" = "GitLab Container Registry - DNS only, TLS via Traefik"
    "pages.git"    = "GitLab Pages apex - DNS only, TLS via Traefik"
    "*.pages.git"  = "GitLab Pages wildcard - DNS only, TLS via Traefik"
    "ide.git"      = "GitLab Web IDE extension host apex - DNS only, TLS via Traefik"
    "*.ide.git"    = "GitLab Web IDE wildcard - DNS only, TLS via Traefik"
  }

  dns_records = merge(
    {
      # Apex A record. `name` is the FQDN apex form; the CAA/SPF records use the
      # "@" shorthand (DMARC lives at _dmarc) — both resolve to the apex in the
      # v4 provider.
      root = {
        name                       = var.external_domain
        type                       = "A"
        content                    = local.ddns_placeholder_ip
        proxied                    = true # Cloudflare proxy (orange cloud) enabled
        ttl                        = 1    # 1 = "Auto"; required for proxied
        comment                    = "Managed by Terraform - IP updated by cloudflare-ddns CronJob in k3s"
        protected                  = true
        content_managed_externally = true
      }

      # SPF ~all / DMARC p=none: monitoring-first; -all and p=reject are their own
      # weighed change. The off-zone rua needs <domain>._report._dmarc.gmail.com
      # TXT "v=DMARC1" (RFC 7489), not ours to publish, so no reports arrive.
      spf = {
        name      = "@"
        type      = "TXT"
        content   = "v=spf1 ~all"
        ttl       = 1
        comment   = "SPF - softfail; -all is a deliberate separate change (see dns.tf)"
        protected = true
      }

      dmarc = {
        name      = "_dmarc"
        type      = "TXT"
        content   = "v=DMARC1; p=none; rua=mailto:ericsweiss1@gmail.com"
        ttl       = 1
        comment   = "DMARC - monitoring policy (p=none); off-zone rua unauthorized (RFC 7489), no reports"
        protected = true
      }

      # GitLab web UI + SSH on one hostname: DNS-only so SSH works alongside
      # HTTPS via Traefik. Origin IP is exposed here and on `direct`; see
      # README.md § Who owns which record.
      git = {
        name                       = "git"
        type                       = "A"
        content                    = local.ddns_placeholder_ip
        proxied                    = false # DNS-only to allow SSH traffic
        ttl                        = 60    # short TTL since DDNS updates this record
        comment                    = "GitLab Web + SSH - DNS only, TLS via Traefik, IP updated by DDNS"
        protected                  = true
        content_managed_externally = true
      }

      # Origin-IP record for what the Cloudflare proxy cannot front: GitLab Pages
      # nested wildcards and the Container Registry. Exposure is gated by the WAN
      # forwards in terraform/unifi, the Proxmox guest firewall and per-service auth.
      direct = {
        name                       = "direct"
        type                       = "A"
        content                    = local.ddns_placeholder_ip
        proxied                    = false # DNS-only (grey cloud) - intentionally exposes origin IP
        ttl                        = 60    # short TTL since DDNS updates this record
        comment                    = "Direct access (no proxy) - IP updated by DDNS"
        protected                  = true
        content_managed_externally = true
      }

      # wg-easy endpoint (kubernetes/apps/wg-easy). DNS-only: WireGuard is UDP and
      # cannot be proxied; the router forwards WAN :51820/udp to MetalLB VIP .99.
      # WireGuard drops any packet without a valid peer key (docs/38).
      vpn = {
        name                       = "vpn"
        type                       = "A"
        content                    = local.ddns_placeholder_ip
        proxied                    = false # DNS-only — WireGuard/UDP can't be proxied
        ttl                        = 60    # short TTL since DDNS updates this record
        comment                    = "wg-easy WireGuard VPN endpoint - DNS only (UDP), IP updated by DDNS"
        protected                  = true
        content_managed_externally = true
      }

      # Immich: CNAME to `direct`, bypassing the Cloudflare proxy whose 100 MB
      # request-body cap mobile video uploads exceed. The Immich IngressRoute
      # therefore carries no external-dns annotation.
      photos = {
        name      = "photos"
        type      = "CNAME"
        content   = "direct.${var.external_domain}"
        proxied   = false # DNS-only: mobile uploads exceed the 100 MB proxied body cap
        ttl       = 1
        comment   = "Immich - DNS only (proxy bypass for large uploads), TLS via Traefik"
        protected = true
      }
    },
    {
      for key, caa in local.caa_records : key => {
        name        = "@"
        type        = "CAA"
        ttl         = 1
        record_data = { flags = 0, tag = caa.tag, value = caa.value }
        comment     = caa.comment
        # A key rename plans as destroy+create — dropping a CA from the
        # allow-list mid-apply would fail in-flight cert issuance.
        protected = true
      }
    },
    {
      for name, comment in local.gitlab_direct_cnames : name => {
        name    = name
        type    = "CNAME"
        content = "direct.${var.external_domain}"
        proxied = false
        ttl     = 1
        comment = comment
        # A key rename destroys the record: registry/pages/ide all resolve
        # through it.
        protected = true
      }
    },
  )
}

# Two zone records are dashboard-managed, not codified: the null MX
# (0 .) that disables inbound mail, and the google-site-verification apex TXT.
# Both are set-once and world-readable; `terraform plan` never touches them.
