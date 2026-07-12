# DNS Records managed by Terraform
# Note: Service CNAME records (auth, bar, food, plex, home) are managed by external-dns in k3s

# Root domain A record - IP managed by DDNS, config managed by Terraform
# name = var.external_domain (FQDN apex form). The CAA records and the apex SPF
# TXT below use the "@" apex shorthand (the DMARC TXT lives at _dmarc); both the
# FQDN and "@" forms resolve to the zone apex in the v4 provider. The FQDN form
# is kept here because it doubles as inline documentation of which zone this
# record lives in.
resource "cloudflare_record" "root" {
  zone_id = data.cloudflare_zone.external.id
  name    = var.external_domain # ericsweiss.com
  type    = "A"
  content = "104.156.98.15" # Initial/placeholder value - updated by DDNS
  proxied = true            # Cloudflare proxy (orange cloud) enabled
  ttl     = 1               # 1 = "Auto" (Cloudflare-managed); required for proxied
  comment = "Managed by Terraform - IP updated by cloudflare-ddns CronJob in k3s"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it.
    # Only `content` (the IP) is DDNS-owned; `proxied`/`ttl` stay Terraform-owned.
    # The cloudflare-ddns CronJob PUTs a full body but preserves the record's
    # existing `proxied` and `ttl` on update (kubernetes/.../cloudflare-ddns/
    # cronjob.yaml); its per-record literal only seeds record creation
    # (Cloudflare defaults a fresh record to proxied=false), so there's no
    # divergence risk with the values above.
    ignore_changes = [content]
  }
}

# CAA records — restrict TLS cert issuance to the CAs we actually use:
# Let's Encrypt (cert-manager DNS-01 in-cluster + acme.sh on dns-01) plus the
# Cloudflare Universal SSL partner CAs (Google Trust Services, SSL.com) so edge-
# cert renewal isn't blocked. Cloudflare ALSO auto-injects CAA records for its
# other Universal SSL partner CAs (currently digicert.com, comodoca.com) that it
# manages outside this config — they are intentionally not represented here. The
# iodef entry publishes a violation-reporting address per RFC 8659.
locals {
  caa_records = {
    issue_letsencrypt     = { tag = "issue", value = "letsencrypt.org", comment = "Restrict cert issuance to Let's Encrypt" }
    issuewild_letsencrypt = { tag = "issuewild", value = "letsencrypt.org", comment = "Restrict wildcard cert issuance to Let's Encrypt" }
    issue_pki_goog        = { tag = "issue", value = "pki.goog", comment = "Allow Cloudflare Universal SSL partner CA (Google Trust Services)" }
    issuewild_pki_goog    = { tag = "issuewild", value = "pki.goog", comment = "Allow Cloudflare Universal SSL partner CA wildcard (Google Trust Services)" }
    issue_ssl_com         = { tag = "issue", value = "ssl.com", comment = "Allow Cloudflare Universal SSL partner CA (SSL.com)" }
    issuewild_ssl_com     = { tag = "issuewild", value = "ssl.com", comment = "Allow Cloudflare Universal SSL partner CA wildcard (SSL.com)" }

    # iodef: published in public DNS so CAs can report issuance-policy
    # violations. PII exposure is by design — RFC 8659 §4.1.3 requires an
    # operator-reachable channel, and no role inbox (security@, certmaster@)
    # currently exists.
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
}

# Migrate the previously hand-written per-record CAA blocks into the for_each
# above so apply moves state in place instead of destroy/recreate.
moved {
  from = cloudflare_record.caa_issue
  to   = cloudflare_record.caa["issue_letsencrypt"]
}
moved {
  from = cloudflare_record.caa_issuewild
  to   = cloudflare_record.caa["issuewild_letsencrypt"]
}
moved {
  from = cloudflare_record.caa_issue_pki_goog
  to   = cloudflare_record.caa["issue_pki_goog"]
}
moved {
  from = cloudflare_record.caa_issuewild_pki_goog
  to   = cloudflare_record.caa["issuewild_pki_goog"]
}
moved {
  from = cloudflare_record.caa_issue_ssl_com
  to   = cloudflare_record.caa["issue_ssl_com"]
}
moved {
  from = cloudflare_record.caa_issuewild_ssl_com
  to   = cloudflare_record.caa["issuewild_ssl_com"]
}
moved {
  from = cloudflare_record.caa_iodef
  to   = cloudflare_record.caa["iodef"]
}

# Anti-spoofing records, deployed in monitoring-first mode. SPF starts as
# softfail (~all) and DMARC as p=none so legitimate senders (if any mail is ever
# sent as @ericsweiss.com, e.g. via the Gmail relay) are flagged, not dropped.
# Note: the rua target is a consumer Gmail address on a different org domain, so
# DMARC aggregate reports likely WON'T be delivered (cross-domain rua needs an
# authorization record that gmail.com doesn't publish for consumer accounts) —
# the value here is the published policy itself. Tighten SPF to -all and DMARC to
# p=reject once confident no legitimate senders exist.
resource "cloudflare_record" "spf" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "TXT"
  content = "v=spf1 ~all"
  ttl     = 1
  comment = "SPF - softfail (monitoring); tighten to -all after DMARC reports"
}

resource "cloudflare_record" "dmarc" {
  zone_id = data.cloudflare_zone.external.id
  name    = "_dmarc"
  type    = "TXT"
  content = "v=DMARC1; p=none; rua=mailto:ericsweiss1@gmail.com"
  ttl     = 1
  comment = "DMARC - monitoring policy (p=none); rua best-effort (cross-domain to consumer Gmail)"
}

# Records present in the Cloudflare zone but intentionally managed outside this
# config (not service CNAMEs, so external-dns does not own them either):
#   - null MX (0 .)                            — disables inbound mail
#   - google-site-verification=... (apex TXT)  — Google Search Console ownership
# Both are set-once dashboard records that never change (and both values are
# world-readable DNS data, so secrecy is not a factor); codifying them here
# would require `terraform import` of the existing records just to manage two
# static one-offs — not worth the state overhead. `terraform plan` will not
# touch them.

# =============================================================================
# GitLab DNS Records
# These are managed by Terraform (not external-dns) because:
# - Subdomains like registry.git require explicit management
# - Wildcard domains (*.pages.git) aren't supported by external-dns annotations
# =============================================================================

# GitLab Web UI + SSH - git.ericsweiss.com
# DNS-only mode allows both HTTPS (via Traefik) and SSH access on the same hostname
# Note: Origin IP is already exposed via direct.ericsweiss.com, so no additional security impact
# Fresh applies briefly resolve to the placeholder IP until the DDNS CronJob's
# next */5 run; trigger one immediately with:
#   kubectl -n cloudflare-ddns create job --from=cronjob/cloudflare-ddns manual-$(date +%s)
resource "cloudflare_record" "git" {
  zone_id = data.cloudflare_zone.external.id
  name    = "git"
  type    = "A"
  content = "104.156.98.15" # Initial/placeholder value - updated by DDNS
  proxied = false           # DNS-only to allow SSH traffic
  ttl     = 60              # short TTL since DDNS updates this record
  comment = "GitLab Web + SSH - DNS only, TLS via Traefik, IP updated by DDNS"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it.
    # The cloudflare-ddns CronJob preserves the record's existing `proxied` on
    # update; its per-record literal only seeds record creation, so `proxied`
    # below stays Terraform-owned.
    ignore_changes = [content]
  }
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
# - Proxmox firewall restricts access (sg-gitlab on the GitLab VM, sg-k3s-ingress-pub on the Traefik ingress path)
# - Only specific ports are open (443, 2222, 5050)
# - Services require authentication (GitLab, Container Registry)
resource "cloudflare_record" "direct" {
  zone_id = data.cloudflare_zone.external.id
  name    = "direct"
  type    = "A"
  content = "104.156.98.15" # Initial/placeholder value - updated by DDNS
  proxied = false           # DNS-only mode (grey cloud) - intentionally exposes origin IP
  ttl     = 60              # short TTL since DDNS updates this record
  comment = "Direct access (no proxy) - IP updated by DDNS"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it.
    # The cloudflare-ddns CronJob preserves the record's existing `proxied` on
    # update; its per-record literal only seeds record creation, so `proxied`
    # below stays Terraform-owned.
    ignore_changes = [content]
  }
}

# Nested-subdomain records pointing at direct.${var.external_domain} (DNS-only,
# TLS via Traefik). Nested subdomains and nested wildcards aren't covered by
# Cloudflare Universal SSL (first-level wildcards only — nested would need
# Advanced Certificate Manager, $10/mo), and wildcards can't be expressed via
# external-dns annotations, so they're managed here.
#
# The ide.git / *.ide.git pair is the Web IDE extension host: each VS Code
# extension iframe loads from <ext-id>.ide.git.ericsweiss.com so the browser
# same-origin policy keeps extension JS away from the GitLab session cookie
# (CVE-2026-5816 mitigation; see docs/27-gitlab-deployment.md "Web IDE extension
# host").
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
}

# Migrate the previously hand-written per-record CNAME blocks into the for_each
# above so apply moves state in place instead of destroy/recreate.
moved {
  from = cloudflare_record.registry_git
  to   = cloudflare_record.gitlab_direct["registry.git"]
}
moved {
  from = cloudflare_record.pages_git
  to   = cloudflare_record.gitlab_direct["pages.git"]
}
moved {
  from = cloudflare_record.pages_git_wildcard
  to   = cloudflare_record.gitlab_direct["*.pages.git"]
}
moved {
  from = cloudflare_record.ide_git
  to   = cloudflare_record.gitlab_direct["ide.git"]
}
moved {
  from = cloudflare_record.ide_git_wildcard
  to   = cloudflare_record.gitlab_direct["*.ide.git"]
}
