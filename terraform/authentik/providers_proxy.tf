# Forward-auth (single application) proxy providers, served by the embedded
# outpost. All nine share the same shape — only name + external_host vary.
# They are deliberately EXPLICIT resources (not for_each over a map): the
# applications reference these providers, and `terraform import` validates
# the whole configuration on every invocation — indexing into a partially
# imported for_each map fails with "Invalid index", while whole-resource
# references simply evaluate as unknown. Explicit resources keep one-at-a-time
# import/state surgery possible. Adding a forward-auth app = copy one of
# these blocks + one entry in local.applications (see README).
#
# Shared shape notes:
# - property_mappings is deliberately NOT set: authentik auto-assigns the five
#   default scope mappings to every proxy provider (the live lists are exactly
#   those defaults), and the goauthentik provider only manages this field when
#   it is explicitly configured (its Read skips the field otherwise) —
#   configuring it here would leave a permanent phantom "+ property_mappings"
#   diff on the imported state. See README "Provider quirks".
# - Basic-auth injection is disabled on every provider (basic_auth_enabled
#   false, both attributes empty). On Sonarr the live object still carries
#   stale values in the two attribute fields (a leaked literal credential) —
#   the plan shows exactly that one clear-to-empty diff until the first
#   supervised apply flushes it, which is approved. See README "The Sonarr
#   exception".

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

  basic_auth_enabled            = false
  basic_auth_username_attribute = ""
  basic_auth_password_attribute = ""

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

resource "authentik_provider_proxy" "hermes" {
  name          = "Hermes"
  external_host = "https://agent.ericsweiss.com"

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
