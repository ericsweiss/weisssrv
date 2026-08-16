# Service CNAMEs (auth, bar, food, plex, home, …) are NOT here — external-dns
# owns those from the k3s IngressRoutes. This file holds the records external-dns
# cannot express: the apex, the DDNS-tracked A records, CAA, SPF/DMARC, and the
# nested wildcards. Contract, token scopes and removal procedure: README.md.
#
# These are INPUTS to the library `cloudflare-zone` module (main.tf), not
# resources. Each map key is the record's state address, and the two flags below
# select which of the module's four lifecycle classes the record lands in.
#
# PREVENT_DESTROY POLICY (`protected = true` on every record here)
# This module auto-applies on main (`terraform apply -auto-approve`, with no
# pre-merge plan in CI — see README.md), so deleting or renaming a key would take
# a record out of DNS unreviewed. `protected` routes the record to a module
# resource carrying `lifecycle { prevent_destroy = true }`, making removal a
# deliberate two-step change: clear the flag, then drop the entry. In-place
# updates and `moved` blocks are unaffected.
#
# DDNS OWNERSHIP (`content_managed_externally = true`)
# The cloudflare-ddns CronJob owns the live IP; the placeholder below only seeds
# record creation (the module ignores drift on `content`). The CronJob PUTs a
# full body but preserves the record's existing `proxied`/`ttl`, so those stay
# Terraform-owned.
locals {
  # RFC 5737 TEST-NET-1 — deliberately unroutable. A fresh apply resolves these
  # four records to this address until the DDNS CronJob's next */5 run; seeding
  # from the real WAN IP instead would rot in git and eventually publish a lease
  # that belongs to a stranger.
  ddns_placeholder_ip = "192.0.2.1"

  # CAA — issuance is restricted to Let's Encrypt (cert-manager + acme.sh) plus
  # the Cloudflare Universal SSL partner CAs, so edge-cert renewal isn't blocked.
  # Cloudflare auto-injects CAA records for its other partner CAs outside this
  # config; they are intentionally absent here.
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
  # Cloudflare Universal SSL covers first-level wildcards only, and external-dns
  # annotations cannot express wildcards. The ide.git pair is the Web IDE
  # extension host (CVE-2026-5816 isolation; docs/27 § Web IDE extension host).
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

      # SPF ~all / DMARC p=none: monitoring-first and deliberately parked, not a
      # pending TODO. The `rua` target is a consumer Gmail on another org domain,
      # so aggregate reports will not accumulate evidence for tightening — treat
      # -all / p=reject as its own weighed change.
      spf = {
        name      = "@"
        type      = "TXT"
        content   = "v=spf1 ~all"
        ttl       = 1
        comment   = "SPF - softfail (monitoring); tighten to -all after DMARC reports"
        protected = true
      }

      dmarc = {
        name      = "_dmarc"
        type      = "TXT"
        content   = "v=DMARC1; p=none; rua=mailto:ericsweiss1@gmail.com"
        ttl       = 1
        comment   = "DMARC - monitoring policy (p=none); rua best-effort (cross-domain to consumer Gmail)"
        protected = true
      }

      # GitLab web UI + SSH on one hostname: DNS-only so SSH works alongside
      # HTTPS via Traefik. Origin IP is exposed here and on `direct` by design
      # (see below). Force a DDNS refresh after a fresh apply with
      # `kubectl -n cloudflare-ddns create job --from=cronjob/cloudflare-ddns …`.
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
      # nested wildcards and the Container Registry (direct TLS termination).
      # Exposure is intentional and gated by the Proxmox firewall (sg-gitlab,
      # sg-k3s-ingress-pub), a small open-port set (443, 2222, 5050) and
      # per-service authentication.
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

      # wg-easy endpoint (kubernetes/apps/wg-easy). DNS-only: WireGuard is UDP
      # and cannot be proxied, so this resolves to the origin, where the router
      # forwards WAN :51820/udp -> MetalLB VIP .99. An exposed endpoint is safe
      # by design — WireGuard drops any packet without a valid peer key
      # (docs/38 threat model).
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

      # Immich: CNAME to `direct` so it bypasses the Cloudflare proxy, whose
      # 100 MB request-body cap mobile video uploads routinely exceed. This is
      # why the Immich IngressRoute carries no external-dns annotation — the
      # record lives here.
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

# Two zone records are deliberately dashboard-managed, not codified: the null MX
# (0 .) that disables inbound mail, and the google-site-verification apex TXT.
# Both are set-once and world-readable; `terraform plan` never touches them.
