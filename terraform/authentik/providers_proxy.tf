# Forward-auth (single application) proxy providers, served by the embedded
# outpost (outpost.tf). They share one shape — only name + external_host (and
# the basic-auth block, below) vary. They are deliberately EXPLICIT resources
# (not for_each over a map): the applications reference these providers, and
# `terraform import` validates the whole configuration on every invocation —
# indexing into a partially imported for_each map fails with "Invalid index",
# while whole-resource references simply evaluate as unknown. Explicit
# resources keep one-at-a-time import/state surgery possible. Adding a
# forward-auth app = copy one of these blocks + one entry in
# local.applications + the outpost provider list (see README).
#
# Shared shape notes:
# - property_mappings is deliberately NOT set: authentik auto-assigns the five
#   default scope mappings to every proxy provider (the live lists are exactly
#   those defaults), and the goauthentik provider only manages this field when
#   it is explicitly configured (its Read skips the field otherwise) —
#   configuring it here would leave a permanent phantom "+ property_mappings"
#   diff on the imported state. See README "Provider quirks".
# - Basic-auth INJECTION (basic_auth_enabled true + the two *_attribute names)
#   is on for providers whose upstream keeps its own credential check: the
#   outpost reads the named attributes from the user (group attributes merge
#   into user attributes — groups.tf stores them on the app's access group)
#   and sends them as the Authorization header; the dedicated
#   authentik-auth-basic Traefik middleware forwards it upstream. Currently:
#   nzbget (nzbget_user/nzbget_password on media-admins) and the two AdGuard
#   providers (adguard_user/adguard_password on dns-admins). All other
#   providers keep injection disabled with both attribute fields empty. On
#   Sonarr the live object still carries stale values in the two attribute
#   fields (a leaked literal credential) — the plan shows exactly that one
#   clear-to-empty diff until the first supervised apply flushes it, which is
#   approved. See README "The Sonarr exception".

resource "authentik_provider_proxy" "sonarr" {
  name          = "Sonarr"
  external_host = "https://tv.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "radarr" {
  name          = "Radarr"
  external_host = "https://movies.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "lidarr" {
  name          = "Lidarr"
  external_host = "https://music.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "qbittorrent" {
  name          = "qBittorrent"
  external_host = "https://qbittorrent.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "nzbget" {
  name          = "NZBGet"
  external_host = "https://nzbget.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  # Basic-auth injection: NZBGet validates HTTP Basic against its own
  # ControlUsername/ControlPassword (nzbget.conf) and has no External auth
  # mode like the *arrs — injecting the credentials kills the double-login.
  # The attribute VALUES live on the media-admins group (groups.tf, from the
  # 'NZBGet' 1Password item); these fields name the attributes, they never
  # hold literals (see README "The Sonarr exception" for the anti-pattern).
  # The nzbget IngressRoute must use the authentik-auth-basic middleware
  # (kubernetes/apps/download-clients/ingress-routes/nzbget) or the injected
  # Authorization header is stripped.
  basic_auth_enabled            = true
  basic_auth_username_attribute = "nzbget_user"
  basic_auth_password_attribute = "nzbget_password"

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "prowlarr" {
  name          = "Prowlarr"
  external_host = "https://prowlarr.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "pulsarr" {
  name          = "Pulsarr"
  external_host = "https://pulsarr.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "wireguard_easy" {
  name          = "WireGuard Easy"
  external_host = "https://vpn.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

# AdGuard Home SSO dashboards (Terraform-authored, docs/08)
# One provider per hostname (forward_single matches exactly one external
# host): adguard.esweiss.com -> dns-01, adguard-02.esweiss.com -> dns-02.
# Both inject the AdGuard admin credentials (adguard_user/adguard_password
# attributes on the dns-admins group, from the 'AdGuard Home' 1Password item)
# — AdGuard has no external-auth mode, so injection is what makes the SSO
# hostnames log straight in. The raw dns-01/dns-02.esweiss.com routes and the
# direct IPs stay untouched as the cluster-outage break-glass path.

resource "authentik_provider_proxy" "adguard_01" {
  name          = "AdGuard Home dns-01"
  external_host = "https://adguard.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = true
  basic_auth_username_attribute = "adguard_user"
  basic_auth_password_attribute = "adguard_password"

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}

resource "authentik_provider_proxy" "adguard_02" {
  name          = "AdGuard Home dns-02"
  external_host = "https://adguard-02.esweiss.com"

  mode                  = "forward_single"
  intercept_header_auth = true

  authorization_flow = data.authentik_flow.provider_authorization.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  internal_host                = ""
  internal_host_ssl_validation = true
  skip_path_regex              = ""
  cookie_domain                = ""

  basic_auth_enabled            = true
  basic_auth_username_attribute = "adguard_user"
  basic_auth_password_attribute = "adguard_password"

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"
}
