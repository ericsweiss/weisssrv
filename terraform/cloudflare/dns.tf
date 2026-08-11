# Service CNAMEs (auth, bar, food, plex, home, …) are NOT here — external-dns
# owns those from the k3s IngressRoutes. This file holds the records external-dns
# cannot express: the apex, the DDNS-tracked A records, CAA, SPF/DMARC, and the
# nested wildcards. Contract, token scopes and removal procedure: README.md.
#
# PREVENT_DESTROY POLICY (every resource below carries it)
# This module auto-applies on main (`terraform apply -auto-approve`, and the MR
# widget shows only change COUNTS), so deleting or renaming a resource block or
# a for_each key would take a record out of DNS unreviewed. prevent_destroy makes
# removal a deliberate two-step change: drop the lifecycle block, then the
# resource. In-place updates and `moved` blocks are unaffected.
#
# DDNS OWNERSHIP (every record with ignore_changes = [content])
# The cloudflare-ddns CronJob owns the live IP; the placeholder below only seeds
# record creation. The CronJob PUTs a full body but preserves the record's
# existing `proxied`/`ttl`, so those stay Terraform-owned.
locals {
  ddns_placeholder_ip = "104.156.98.15"
}

# Apex A record. name is the FQDN apex form; the CAA/SPF records below use the
# "@" shorthand (DMARC lives at _dmarc) — both resolve to the apex in the v4
# provider.
resource "cloudflare_record" "root" {
  zone_id = data.cloudflare_zone.external.id
  name    = var.external_domain
  type    = "A"
  content = local.ddns_placeholder_ip
  proxied = true # Cloudflare proxy (orange cloud) enabled
  ttl     = 1    # 1 = "Auto"; required for proxied
  comment = "Managed by Terraform - IP updated by cloudflare-ddns CronJob in k3s"

  lifecycle {
    ignore_changes  = [content] # DDNS-owned; see the header notes
    prevent_destroy = true
  }
}

# CAA — issuance is restricted to Let's Encrypt (cert-manager + acme.sh) plus the
# Cloudflare Universal SSL partner CAs, so edge-cert renewal isn't blocked.
# Cloudflare auto-injects CAA records for its other partner CAs outside this
# config; they are intentionally absent here.
locals {
  caa_records = {
    issue_letsencrypt     = { tag = "issue", value = "letsencrypt.org", comment = "Restrict cert issuance to Let's Encrypt" }
    issuewild_letsencrypt = { tag = "issuewild", value = "letsencrypt.org", comment = "Restrict wildcard cert issuance to Let's Encrypt" }
    issue_pki_goog        = { tag = "issue", value = "pki.goog", comment = "Allow Cloudflare Universal SSL partner CA (Google Trust Services)" }
    issuewild_pki_goog    = { tag = "issuewild", value = "pki.goog", comment = "Allow Cloudflare Universal SSL partner CA wildcard (Google Trust Services)" }
    issue_ssl_com         = { tag = "issue", value = "ssl.com", comment = "Allow Cloudflare Universal SSL partner CA (SSL.com)" }
    issuewild_ssl_com     = { tag = "issuewild", value = "ssl.com", comment = "Allow Cloudflare Universal SSL partner CA wildcard (SSL.com)" }

    # iodef is published in public DNS by design: RFC 8659 §4.1.3 wants an
    # operator-reachable channel and no role inbox exists.
    iodef = { tag = "iodef", value = "mailto:ericsweiss1@gmail.com", comment = "CAA violation reports go here" }
  }
}

resource "cloudflare_record" "caa" {
  for_each = local.caa_records

  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1

  data {
    flags = 0
    tag   = each.value.tag
    value = each.value.value
  }

  comment = each.value.comment

  lifecycle {
    # A for_each KEY rename plans as destroy+create — dropping a CA from the
    # allow-list mid-apply would fail in-flight cert issuance.
    prevent_destroy = true
  }
}

# Anti-spoofing, monitoring-first: SPF softfail (~all) and DMARC p=none so any
# legitimate sender is flagged rather than dropped. Tighten to -all / p=reject
# once confident none exists. The rua target is a consumer Gmail address on
# another org domain, so aggregate reports likely aren't delivered — the value
# here is the published policy.
resource "cloudflare_record" "spf" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "TXT"
  content = "v=spf1 ~all"
  ttl     = 1
  comment = "SPF - softfail (monitoring); tighten to -all after DMARC reports"

  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_record" "dmarc" {
  zone_id = data.cloudflare_zone.external.id
  name    = "_dmarc"
  type    = "TXT"
  content = "v=DMARC1; p=none; rua=mailto:ericsweiss1@gmail.com"
  ttl     = 1
  comment = "DMARC - monitoring policy (p=none); rua best-effort (cross-domain to consumer Gmail)"

  lifecycle {
    prevent_destroy = true
  }
}

# Two zone records are deliberately dashboard-managed, not codified: the null MX
# (0 .) that disables inbound mail, and the google-site-verification apex TXT.
# Both are set-once and world-readable; `terraform plan` never touches them.

# GitLab web UI + SSH on one hostname: DNS-only so SSH works alongside HTTPS via
# Traefik. Origin IP is exposed here and on `direct` by design (see below).
# A fresh apply resolves to the placeholder until the DDNS CronJob's next */5 run;
# force one with `kubectl -n cloudflare-ddns create job --from=cronjob/cloudflare-ddns …`.
resource "cloudflare_record" "git" {
  zone_id = data.cloudflare_zone.external.id
  name    = "git"
  type    = "A"
  content = local.ddns_placeholder_ip
  proxied = false # DNS-only to allow SSH traffic
  ttl     = 60    # short TTL since DDNS updates this record
  comment = "GitLab Web + SSH - DNS only, TLS via Traefik, IP updated by DDNS"

  lifecycle {
    ignore_changes  = [content] # DDNS-owned; see the header notes
    prevent_destroy = true
  }
}

# Origin-IP record for what the Cloudflare proxy cannot front: GitLab Pages
# nested wildcards and the Container Registry (direct TLS termination). Exposure
# is intentional and gated by the Proxmox firewall (sg-gitlab, sg-k3s-ingress-pub),
# a small open-port set (443, 2222, 5050) and per-service authentication.
resource "cloudflare_record" "direct" {
  zone_id = data.cloudflare_zone.external.id
  name    = "direct"
  type    = "A"
  content = local.ddns_placeholder_ip
  proxied = false # DNS-only (grey cloud) - intentionally exposes origin IP
  ttl     = 60    # short TTL since DDNS updates this record
  comment = "Direct access (no proxy) - IP updated by DDNS"

  lifecycle {
    ignore_changes  = [content] # DDNS-owned; see the header notes
    prevent_destroy = true
  }
}

# wg-easy endpoint (kubernetes/apps/wg-easy). DNS-only: WireGuard is UDP and
# cannot be proxied, so this resolves to the origin, where the router forwards
# WAN :51820/udp -> MetalLB VIP .99. An exposed endpoint is safe by design —
# WireGuard drops any packet without a valid peer key (docs/38 threat model).
resource "cloudflare_record" "vpn" {
  zone_id = data.cloudflare_zone.external.id
  name    = "vpn"
  type    = "A"
  content = local.ddns_placeholder_ip
  proxied = false # DNS-only — WireGuard/UDP can't be proxied
  ttl     = 60    # short TTL since DDNS updates this record
  comment = "wg-easy WireGuard VPN endpoint - DNS only (UDP), IP updated by DDNS"

  lifecycle {
    ignore_changes  = [content] # DDNS-owned; see the header notes
    prevent_destroy = true
  }
}

# Immich: CNAME to `direct` so it bypasses the Cloudflare proxy, whose 100 MB
# request-body cap mobile video uploads routinely exceed. This is why the Immich
# IngressRoute carries no external-dns annotation — the record lives here.
resource "cloudflare_record" "photos" {
  zone_id = data.cloudflare_zone.external.id
  name    = "photos"
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false # DNS-only: mobile uploads exceed the 100 MB proxied body cap
  ttl     = 1
  comment = "Immich - DNS only (proxy bypass for large uploads), TLS via Traefik"

  lifecycle {
    prevent_destroy = true
  }
}

# Nested subdomains/wildcards pointing at `direct` (DNS-only, TLS via Traefik):
# Cloudflare Universal SSL covers first-level wildcards only, and external-dns
# annotations cannot express wildcards. The ide.git pair is the Web IDE extension
# host (CVE-2026-5816 isolation; docs/27 § Web IDE extension host).
locals {
  gitlab_direct_cnames = {
    "registry.git" = "GitLab Container Registry - DNS only, TLS via Traefik"
    "pages.git"    = "GitLab Pages apex - DNS only, TLS via Traefik"
    "*.pages.git"  = "GitLab Pages wildcard - DNS only, TLS via Traefik"
    "ide.git"      = "GitLab Web IDE extension host apex - DNS only, TLS via Traefik"
    "*.ide.git"    = "GitLab Web IDE wildcard - DNS only, TLS via Traefik"
  }
}

resource "cloudflare_record" "gitlab_direct" {
  for_each = local.gitlab_direct_cnames

  zone_id = data.cloudflare_zone.external.id
  name    = each.key
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false
  ttl     = 1
  comment = each.value

  lifecycle {
    # A key rename destroys the record: registry/pages/ide all resolve through it.
    prevent_destroy = true
  }
}
