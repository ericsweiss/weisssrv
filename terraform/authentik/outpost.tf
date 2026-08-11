# The embedded outpost, adopted by import (imports.tf). The object itself is
# authentik-managed; its provider list is the one user-touched knob, and pinning
# it here means `terraform plan` catches a forward-auth app that was never
# assigned (which otherwise surfaces as a 404 at the outpost).
#
# Rules:
# - protocol_providers is ORDERED (the API preserves insertion order) — APPEND
#   new providers at the end.
# - `config` and `service_connection` stay unset on purpose, so the
#   authentik-managed settings JSON is never diffed or rewritten.
resource "authentik_outpost" "embedded" {
  name = "authentik Embedded Outpost"
  type = "proxy"

  protocol_providers = [
    authentik_provider_proxy.radarr.id,
    authentik_provider_proxy.qbittorrent.id,
    authentik_provider_proxy.pulsarr.id,
    authentik_provider_proxy.sonarr.id,
    authentik_provider_proxy.wireguard_easy.id,
    authentik_provider_proxy.nzbget.id,
    authentik_provider_proxy.prowlarr.id,
    authentik_provider_proxy.lidarr.id,
    authentik_provider_proxy.adguard_01.id,
    authentik_provider_proxy.adguard_02.id,
  ]

  lifecycle {
    prevent_destroy = true
  }
}
