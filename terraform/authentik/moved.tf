# State-address migration for the move onto the library `authentik-sso` module
# (main.tf). Every object kept its configuration field-for-field; only its
# address changed, from a root-module resource to a keyed instance of the
# module's equivalent resource.
#
# 78 blocks — one per RESOURCE INSTANCE that existed before the move, which is
# every address `terraform state list` reports for this state:
#
#   19  applications   (16 keyed + adguard_01 / adguard_02 / homarr)
#   10  proxy providers
#    8  OAuth2 providers
#    1  SAML provider
#    1  scope property mapping (email_verified)
#   18  groups         (15 keyed + media_admins / dns_admins / authentik_admins)
#   20  policy bindings (16 keyed + adguard_01 / adguard_02 / homarr / homarr_users)
#    1  embedded outpost
#
# An address with NO block here plans as destroy+create of a live SSO object, so
# the list is exhaustive by construction rather than by inspection: it was
# derived from the pre-move .tf files (each explicit resource plus every key of
# each `for_each` map) and cross-checked against imports.tf and import.sh, which
# name the same identities.
#
# `moved` is not affected by `prevent_destroy`, and re-applying is a no-op once
# state carries the new addresses. The data sources need no blocks: they are
# re-read on every plan, so their move into the module (the two flows, the
# signing keypair, the scope/SAML mappings and the two user lookups that used to
# live in data.tf) is invisible to state.

# --- Applications (19) -------------------------------------------------------
# The three that were explicit resources take their slug as the module key, so
# `homarr` becomes "dashboard" — the slug it always had.

moved {
  from = authentik_application.app["bar"]
  to   = module.sso.authentik_application.this["bar"]
}

moved {
  from = authentik_application.app["cloud"]
  to   = module.sso.authentik_application.this["cloud"]
}

moved {
  from = authentik_application.app["food"]
  to   = module.sso.authentik_application.this["food"]
}

moved {
  from = authentik_application.app["home"]
  to   = module.sso.authentik_application.this["home"]
}

moved {
  from = authentik_application.app["photos"]
  to   = module.sso.authentik_application.this["photos"]
}

moved {
  from = authentik_application.app["agent"]
  to   = module.sso.authentik_application.this["agent"]
}

moved {
  from = authentik_application.app["git"]
  to   = module.sso.authentik_application.this["git"]
}

moved {
  from = authentik_application.app["grafana"]
  to   = module.sso.authentik_application.this["grafana"]
}

moved {
  from = authentik_application.app["vpn"]
  to   = module.sso.authentik_application.this["vpn"]
}

moved {
  from = authentik_application.app["movies"]
  to   = module.sso.authentik_application.this["movies"]
}

moved {
  from = authentik_application.app["music"]
  to   = module.sso.authentik_application.this["music"]
}

moved {
  from = authentik_application.app["nzbget"]
  to   = module.sso.authentik_application.this["nzbget"]
}

moved {
  from = authentik_application.app["prowlarr"]
  to   = module.sso.authentik_application.this["prowlarr"]
}

moved {
  from = authentik_application.app["pulsarr"]
  to   = module.sso.authentik_application.this["pulsarr"]
}

moved {
  from = authentik_application.app["qbittorrent"]
  to   = module.sso.authentik_application.this["qbittorrent"]
}

moved {
  from = authentik_application.app["tv"]
  to   = module.sso.authentik_application.this["tv"]
}

moved {
  from = authentik_application.adguard_01
  to   = module.sso.authentik_application.this["adguard-01"]
}

moved {
  from = authentik_application.adguard_02
  to   = module.sso.authentik_application.this["adguard-02"]
}

moved {
  from = authentik_application.homarr
  to   = module.sso.authentik_application.this["dashboard"]
}

# --- Proxy providers (10) ----------------------------------------------------
# The ten explicit resources collapse into one `for_each` map; each resource
# name becomes the map key unchanged (providers_proxy.tf).

moved {
  from = authentik_provider_proxy.sonarr
  to   = module.sso.authentik_provider_proxy.this["sonarr"]
}

moved {
  from = authentik_provider_proxy.radarr
  to   = module.sso.authentik_provider_proxy.this["radarr"]
}

moved {
  from = authentik_provider_proxy.lidarr
  to   = module.sso.authentik_provider_proxy.this["lidarr"]
}

moved {
  from = authentik_provider_proxy.qbittorrent
  to   = module.sso.authentik_provider_proxy.this["qbittorrent"]
}

moved {
  from = authentik_provider_proxy.nzbget
  to   = module.sso.authentik_provider_proxy.this["nzbget"]
}

moved {
  from = authentik_provider_proxy.prowlarr
  to   = module.sso.authentik_provider_proxy.this["prowlarr"]
}

moved {
  from = authentik_provider_proxy.pulsarr
  to   = module.sso.authentik_provider_proxy.this["pulsarr"]
}

moved {
  from = authentik_provider_proxy.wireguard_easy
  to   = module.sso.authentik_provider_proxy.this["wireguard_easy"]
}

moved {
  from = authentik_provider_proxy.adguard_01
  to   = module.sso.authentik_provider_proxy.this["adguard_01"]
}

moved {
  from = authentik_provider_proxy.adguard_02
  to   = module.sso.authentik_provider_proxy.this["adguard_02"]
}

# --- OAuth2 providers (8) ----------------------------------------------------

moved {
  from = authentik_provider_oauth2.mealie
  to   = module.sso.authentik_provider_oauth2.this["mealie"]
}

moved {
  from = authentik_provider_oauth2.bar_assistant
  to   = module.sso.authentik_provider_oauth2.this["bar_assistant"]
}

moved {
  from = authentik_provider_oauth2.home_assistant
  to   = module.sso.authentik_provider_oauth2.this["home_assistant"]
}

moved {
  from = authentik_provider_oauth2.grafana
  to   = module.sso.authentik_provider_oauth2.this["grafana"]
}

moved {
  from = authentik_provider_oauth2.nextcloud
  to   = module.sso.authentik_provider_oauth2.this["nextcloud"]
}

moved {
  from = authentik_provider_oauth2.hermes_dashboard
  to   = module.sso.authentik_provider_oauth2.this["hermes_dashboard"]
}

moved {
  from = authentik_provider_oauth2.homarr
  to   = module.sso.authentik_provider_oauth2.this["homarr"]
}

moved {
  from = authentik_provider_oauth2.immich
  to   = module.sso.authentik_provider_oauth2.this["immich"]
}

# --- SAML provider (1) -------------------------------------------------------

moved {
  from = authentik_provider_saml.gitlab
  to   = module.sso.authentik_provider_saml.this["gitlab"]
}

# --- Scope property mapping (1) ----------------------------------------------
# The one user-authored mapping, now a `custom_scope_mappings` entry the module
# authors (providers_oauth2.tf). Mealie references it as "custom:email_verified".

moved {
  from = authentik_property_mapping_provider_scope.email_verified
  to   = module.sso.authentik_property_mapping_provider_scope.custom["email_verified"]
}

# --- Groups (18) -------------------------------------------------------------
# Keys stay the group NAME, except "authentik Admins", whose key is
# "authentik-admins" (the name carries a space) with `name` set explicitly.

moved {
  from = authentik_group.app["admin"]
  to   = module.sso.authentik_group.this["admin"]
}

moved {
  from = authentik_group.app["bar-assistant-users"]
  to   = module.sso.authentik_group.this["bar-assistant-users"]
}

moved {
  from = authentik_group.app["gitlab-admins"]
  to   = module.sso.authentik_group.this["gitlab-admins"]
}

moved {
  from = authentik_group.app["gitlab-users"]
  to   = module.sso.authentik_group.this["gitlab-users"]
}

moved {
  from = authentik_group.app["grafana-admins"]
  to   = module.sso.authentik_group.this["grafana-admins"]
}

moved {
  from = authentik_group.app["grafana-users"]
  to   = module.sso.authentik_group.this["grafana-users"]
}

moved {
  from = authentik_group.app["hermes-users"]
  to   = module.sso.authentik_group.this["hermes-users"]
}

moved {
  from = authentik_group.app["home-assistant-users"]
  to   = module.sso.authentik_group.this["home-assistant-users"]
}

moved {
  from = authentik_group.app["homarr-admins"]
  to   = module.sso.authentik_group.this["homarr-admins"]
}

moved {
  from = authentik_group.app["homarr-users"]
  to   = module.sso.authentik_group.this["homarr-users"]
}

moved {
  from = authentik_group.app["immich-users"]
  to   = module.sso.authentik_group.this["immich-users"]
}

moved {
  from = authentik_group.app["mealie-admins"]
  to   = module.sso.authentik_group.this["mealie-admins"]
}

moved {
  from = authentik_group.app["mealie-users"]
  to   = module.sso.authentik_group.this["mealie-users"]
}

moved {
  from = authentik_group.app["nextcloud-users"]
  to   = module.sso.authentik_group.this["nextcloud-users"]
}

moved {
  from = authentik_group.app["vpn-admins"]
  to   = module.sso.authentik_group.this["vpn-admins"]
}

moved {
  from = authentik_group.media_admins
  to   = module.sso.authentik_group.this["media-admins"]
}

moved {
  from = authentik_group.dns_admins
  to   = module.sso.authentik_group.this["dns-admins"]
}

moved {
  from = authentik_group.authentik_admins
  to   = module.sso.authentik_group.this["authentik-admins"]
}

# --- Policy bindings (20) ----------------------------------------------------
# The 16 keyed bindings keep their slug key. The Homarr pair keys off the
# application slug it gates ("dashboard"), not the app's display name.

moved {
  from = authentik_policy_binding.app_group["bar"]
  to   = module.sso.authentik_policy_binding.this["bar"]
}

moved {
  from = authentik_policy_binding.app_group["cloud"]
  to   = module.sso.authentik_policy_binding.this["cloud"]
}

moved {
  from = authentik_policy_binding.app_group["food"]
  to   = module.sso.authentik_policy_binding.this["food"]
}

moved {
  from = authentik_policy_binding.app_group["home"]
  to   = module.sso.authentik_policy_binding.this["home"]
}

moved {
  from = authentik_policy_binding.app_group["photos"]
  to   = module.sso.authentik_policy_binding.this["photos"]
}

moved {
  from = authentik_policy_binding.app_group["agent"]
  to   = module.sso.authentik_policy_binding.this["agent"]
}

moved {
  from = authentik_policy_binding.app_group["git"]
  to   = module.sso.authentik_policy_binding.this["git"]
}

moved {
  from = authentik_policy_binding.app_group["grafana"]
  to   = module.sso.authentik_policy_binding.this["grafana"]
}

moved {
  from = authentik_policy_binding.app_group["vpn"]
  to   = module.sso.authentik_policy_binding.this["vpn"]
}

moved {
  from = authentik_policy_binding.app_group["movies"]
  to   = module.sso.authentik_policy_binding.this["movies"]
}

moved {
  from = authentik_policy_binding.app_group["music"]
  to   = module.sso.authentik_policy_binding.this["music"]
}

moved {
  from = authentik_policy_binding.app_group["nzbget"]
  to   = module.sso.authentik_policy_binding.this["nzbget"]
}

moved {
  from = authentik_policy_binding.app_group["prowlarr"]
  to   = module.sso.authentik_policy_binding.this["prowlarr"]
}

moved {
  from = authentik_policy_binding.app_group["pulsarr"]
  to   = module.sso.authentik_policy_binding.this["pulsarr"]
}

moved {
  from = authentik_policy_binding.app_group["qbittorrent"]
  to   = module.sso.authentik_policy_binding.this["qbittorrent"]
}

moved {
  from = authentik_policy_binding.app_group["tv"]
  to   = module.sso.authentik_policy_binding.this["tv"]
}

moved {
  from = authentik_policy_binding.adguard_01
  to   = module.sso.authentik_policy_binding.this["adguard-01"]
}

moved {
  from = authentik_policy_binding.adguard_02
  to   = module.sso.authentik_policy_binding.this["adguard-02"]
}

moved {
  from = authentik_policy_binding.homarr
  to   = module.sso.authentik_policy_binding.this["dashboard-admins"]
}

moved {
  from = authentik_policy_binding.homarr_users
  to   = module.sso.authentik_policy_binding.this["dashboard-users"]
}

# --- Embedded outpost (1) ----------------------------------------------------
# The module gates it on `embedded_outpost != null` with `count`, hence the [0].

moved {
  from = authentik_outpost.embedded
  to   = module.sso.authentik_outpost.embedded[0]
}
