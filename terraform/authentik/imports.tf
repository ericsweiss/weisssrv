# Import blocks binding every managed resource to its live API object
# (applications by slug, providers by numeric pk, groups by uuid). These are
# idempotent: once a resource is present in state the block is a no-op, so
# the file is kept as the permanent record of the state<->API identity map
# and as the disaster-recovery re-import path (fresh state: `terraform plan`
# re-imports everything; expected result is "N to import, 0 to change").
#
# The one-time state bootstrap was performed with `terraform import` (see
# README "Import methodology") — no apply was involved.

# --- Applications (id = slug) ----------------------------------------------

import {
  for_each = local.applications
  to       = authentik_application.app[each.key]
  id       = each.key
}

# --- Proxy providers (id = provider pk) -------------------------------------

import {
  to = authentik_provider_proxy.sonarr
  id = "4"
}

import {
  to = authentik_provider_proxy.radarr
  id = "5"
}

import {
  to = authentik_provider_proxy.lidarr
  id = "6"
}

import {
  to = authentik_provider_proxy.qbittorrent
  id = "7"
}

import {
  to = authentik_provider_proxy.nzbget
  id = "8"
}

import {
  to = authentik_provider_proxy.prowlarr
  id = "9"
}

import {
  to = authentik_provider_proxy.pulsarr
  id = "10"
}

import {
  to = authentik_provider_proxy.wireguard_easy
  id = "17"
}

# --- OAuth2 providers (id = provider pk) ------------------------------------

import {
  to = authentik_provider_oauth2.mealie
  id = "1"
}

import {
  to = authentik_provider_oauth2.bar_assistant
  id = "2"
}

import {
  to = authentik_provider_oauth2.home_assistant
  id = "3"
}

import {
  to = authentik_provider_oauth2.grafana
  id = "13"
}

import {
  to = authentik_provider_oauth2.nextcloud
  id = "14"
}

import {
  to = authentik_provider_oauth2.immich
  id = "15"
}

# --- SAML provider (id = provider pk) ---------------------------------------

import {
  to = authentik_provider_saml.gitlab
  id = "12"
}

# --- Groups (id = group uuid) -----------------------------------------------

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
  to = authentik_group.app[each.key]
  id = each.value
}

import {
  to = authentik_group.authentik_admins
  id = "b3dbbcbb-2207-41fb-bf2b-dacd7aeec37c"
}

# --- Embedded outpost (id = outpost uuid) ------------------------------------
# Adopted after the original 44-object import (see outpost.tf for why). The
# import is what makes the auth-passthrough plan an in-place provider-list
# append instead of an (impossible) create of authentik's own outpost.

import {
  to = authentik_outpost.embedded
  id = "436f896e-05cd-4cf7-b9db-7141ad70927b"
}
