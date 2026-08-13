# The embedded outpost, adopted by import (imports.tf). The object itself is
# authentik-managed; its provider list is the one user-touched knob, and pinning
# it here means `terraform plan` catches a forward-auth app that was never
# assigned (which otherwise surfaces as a 404 at the outpost).
#
# Rules:
# - proxy_provider_keys is ORDERED (the API preserves insertion order) — APPEND
#   new providers at the end.
# - The module leaves `config` and `service_connection` unset on purpose, so the
#   authentik-managed settings JSON is never diffed or rewritten.
# - Setting this back to null would be a DESTROY of authentik's own outpost;
#   the module's prevent_destroy refuses it (detach with `terraform state rm`).
locals {
  embedded_outpost = {
    name = "authentik Embedded Outpost"

    proxy_provider_keys = [
      "radarr",
      "qbittorrent",
      "pulsarr",
      "sonarr",
      "wireguard_easy",
      "nzbget",
      "prowlarr",
      "lidarr",
      "adguard_01",
      "adguard_02",
    ]
  }
}
