# DNS Records managed by Terraform
# Note: Service CNAME records (auth, bar, food, plex, home) are managed by external-dns in k3s

# Root domain A record - IP managed by DDNS, config managed by Terraform
# name = var.external_domain (FQDN apex form). The CAA records below use the
# "@" apex shorthand; both resolve to the zone apex in the v4 provider. The
# FQDN form is kept here because it doubles as inline documentation of which
# zone this record lives in.
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
    # NOTE: the cloudflare-ddns CronJob PUTs a full body including `proxied` on
    # every update (kubernetes/.../cloudflare-ddns/cronjob.yaml), so the literal
    # proxied value there MUST stay in sync with the value above (root=true) or
    # Terraform and DDNS will fight. DDNS preserves the existing ttl, so ttl is
    # not a divergence risk.
    ignore_changes = [content]
  }
}

# CAA records - restrict TLS cert issuance to Let's Encrypt only.
# Both `letsencrypt.org` (cert-manager via Cloudflare DNS-01 in cluster)
# and the same authority used by acme.sh on dns-01 cover everything we issue.
resource "cloudflare_record" "caa_issue" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "issue"
    value = "letsencrypt.org"
  }
  comment = "Restrict cert issuance to Let's Encrypt"
}

resource "cloudflare_record" "caa_issuewild" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "issuewild"
    value = "letsencrypt.org"
  }
  comment = "Restrict wildcard cert issuance to Let's Encrypt"
}

# Cloudflare Universal SSL edge certs may be issued by a partner CA (Google Trust
# Services / SSL.com), not just Let's Encrypt. Without these the CAA records above
# would block edge-cert renewal. Add issue + issuewild for each partner CA.
resource "cloudflare_record" "caa_issue_pki_goog" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "issue"
    value = "pki.goog"
  }
  comment = "Allow Cloudflare Universal SSL partner CA (Google Trust Services)"
}

resource "cloudflare_record" "caa_issuewild_pki_goog" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "issuewild"
    value = "pki.goog"
  }
  comment = "Allow Cloudflare Universal SSL partner CA wildcard (Google Trust Services)"
}

resource "cloudflare_record" "caa_issue_ssl_com" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "issue"
    value = "ssl.com"
  }
  comment = "Allow Cloudflare Universal SSL partner CA (SSL.com)"
}

resource "cloudflare_record" "caa_issuewild_ssl_com" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "issuewild"
    value = "ssl.com"
  }
  comment = "Allow Cloudflare Universal SSL partner CA wildcard (SSL.com)"
}

resource "cloudflare_record" "caa_iodef" {
  zone_id = data.cloudflare_zone.external.id
  name    = "@"
  type    = "CAA"
  ttl     = 1
  data {
    flags = 0
    tag   = "iodef"
    # Intentional: this address is published in the public DNS CAA record
    # so CAs can report issuance-policy violations. PII exposure is by
    # design — RFC 8659 §4.1.3 requires an operator-reachable channel,
    # and no role-specific inbox (security@, certmaster@) currently exists.
    value = "mailto:ericsweiss1@gmail.com"
  }
  comment = "CAA violation reports go here"
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
    # cloudflare-ddns co-owns `proxied` via its full-body PUT (git=false); keep
    # the literal in the CronJob in sync with proxied below.
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
  ttl     = 1
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
  ttl     = 1
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
  ttl     = 60              # short TTL since DDNS updates this record
  comment = "Direct access (no proxy) - IP updated by DDNS"

  lifecycle {
    # Allow DDNS to update the IP without Terraform reverting it.
    # cloudflare-ddns co-owns `proxied` via its full-body PUT (direct=false);
    # keep the literal in the CronJob in sync with proxied below.
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
  ttl     = 1
  comment = "GitLab Pages wildcard - DNS only, TLS via Traefik"
}

# =============================================================================
# GitLab Web IDE Extension Host
# =============================================================================
# Per-extension SOP isolation: each VS Code extension iframe is loaded from
# <ext-id>.ide.git.ericsweiss.com so the browser SOP keeps extension JS away
# from the GitLab session cookie. CVE-2026-5816 mitigation; see
# docs/27-gitlab-deployment.md "Web IDE extension host" section.

# GitLab Web IDE - ide.git.ericsweiss.com (apex)
# Nested subdomain - not covered by Universal SSL, use direct access
resource "cloudflare_record" "ide_git" {
  zone_id = data.cloudflare_zone.external.id
  name    = "ide.git"
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false
  ttl     = 1
  comment = "GitLab Web IDE extension host apex - DNS only, TLS via Traefik"
}

# GitLab Web IDE wildcard - *.ide.git.ericsweiss.com
# Note: Cloudflare Universal SSL only covers first-level wildcards (*.ericsweiss.com).
# Nested wildcards like *.ide.git require Advanced Certificate Manager ($10/mo).
# Using DNS-only mode via direct.ericsweiss.com so Traefik handles TLS with Let's Encrypt cert.
resource "cloudflare_record" "ide_git_wildcard" {
  zone_id = data.cloudflare_zone.external.id
  name    = "*.ide.git"
  type    = "CNAME"
  content = "direct.${var.external_domain}"
  proxied = false
  ttl     = 1
  comment = "GitLab Web IDE wildcard - DNS only, TLS via Traefik"
}
