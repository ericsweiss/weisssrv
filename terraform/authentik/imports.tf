# Import blocks binding the ADOPTED resources to their live API objects
# (applications by slug, providers by numeric pk, groups by uuid). Idempotent:
# once a resource is in state the block is a no-op, so this file is the permanent
# state<->API identity map.
#
# Addresses are MODULE-QUALIFIED since the move onto the library `authentik-sso`
# module (main.tf): the resources are keyed instances of the module's resources,
# and the key is the map key in the sibling site-data files. moved.tf holds the
# old->new address map for state that predates the move; this file is for state
# that has none.
#
# SCOPE — the 44 objects that existed in the Admin UI at adoption time only.
# Objects this module has AUTHORED since have no import block (authentik assigns
# their pks/uuids at create time), which is why a state-loss rebuild is not a
# one-command re-import — see README "Import methodology / disaster recovery".

# Applications (id = slug). Static list, NOT local.applications: a newly
# authored app has no live object to import, and a for_each over the map would
# fail its plan. import.sh reads this list.
locals {
  imported_application_slugs = toset([
    "agent",
    "bar",
    "cloud",
    "food",
    "git",
    "grafana",
    "home",
    "movies",
    "music",
    "nzbget",
    "photos",
    "prowlarr",
    "pulsarr",
    "qbittorrent",
    "tv",
    "vpn",
  ])
}

import {
  for_each = local.imported_application_slugs
  to       = module.sso.authentik_application.this[each.value]
  id       = each.value
}

# Proxy providers (id = provider pk)

import {
  to = module.sso.authentik_provider_proxy.this["sonarr"]
  id = "4"
}

import {
  to = module.sso.authentik_provider_proxy.this["radarr"]
  id = "5"
}

import {
  to = module.sso.authentik_provider_proxy.this["lidarr"]
  id = "6"
}

import {
  to = module.sso.authentik_provider_proxy.this["qbittorrent"]
  id = "7"
}

import {
  to = module.sso.authentik_provider_proxy.this["nzbget"]
  id = "8"
}

import {
  to = module.sso.authentik_provider_proxy.this["prowlarr"]
  id = "9"
}

import {
  to = module.sso.authentik_provider_proxy.this["pulsarr"]
  id = "10"
}

import {
  to = module.sso.authentik_provider_proxy.this["wireguard_easy"]
  id = "17"
}

# OAuth2 providers (id = provider pk)

import {
  to = module.sso.authentik_provider_oauth2.this["mealie"]
  id = "1"
}

import {
  to = module.sso.authentik_provider_oauth2.this["bar_assistant"]
  id = "2"
}

import {
  to = module.sso.authentik_provider_oauth2.this["home_assistant"]
  id = "3"
}

import {
  to = module.sso.authentik_provider_oauth2.this["grafana"]
  id = "13"
}

import {
  to = module.sso.authentik_provider_oauth2.this["nextcloud"]
  id = "14"
}

import {
  to = module.sso.authentik_provider_oauth2.this["immich"]
  id = "15"
}

# SAML provider (id = provider pk)

import {
  to = module.sso.authentik_provider_saml.this["gitlab"]
  id = "12"
}

# Groups (id = group uuid)

import {
  for_each = {
    "admin"           = "53d5caec-677c-4f61-bded-d4f6abfdc987"
    "gitlab-admins"   = "c1813efe-df8a-4d1b-95fe-2fba678a7beb"
    "gitlab-users"    = "44e845b4-49fa-4178-b3f4-0064b322b783"
    "grafana-admins"  = "2aa4637f-2a59-4b7a-857e-bbeefaaa75d8"
    "grafana-users"   = "888ac6b4-b6f5-4e2f-90a6-d53847905206"
    "hermes-users"    = "e6d6c372-93c3-4be8-8601-d40bce26127e"
    "immich-users"    = "c07e7bbc-294f-49ab-9cb5-8009387cf0c9"
    "mealie-admins"   = "7bbab05a-add9-4bc4-8803-043082b9629d"
    "mealie-users"    = "63d3f306-c7dd-4b4d-a801-3cc408304c4d"
    "nextcloud-users" = "afcfb27c-be96-4860-8613-fb81f4cce06b"
    "vpn-admins"      = "25c11794-4b0d-4344-8ddf-bd9272c04a6d"
  }
  to = module.sso.authentik_group.this[each.key]
  id = each.value
}

import {
  to = module.sso.authentik_group.this["authentik-admins"]
  id = "b3dbbcbb-2207-41fb-bf2b-dacd7aeec37c"
}

# Embedded outpost (id = outpost uuid). The import is what makes a provider-list
# change an in-place append instead of an (impossible) create of authentik's own
# outpost. The [0] is the module's `count` on embedded_outpost != null.

import {
  to = module.sso.authentik_outpost.embedded[0]
  id = "436f896e-05cd-4cf7-b9db-7141ad70927b"
}
