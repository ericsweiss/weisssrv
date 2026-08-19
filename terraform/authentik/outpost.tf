# The embedded outpost, adopted by import (imports.tf). The object itself is
# authentik-managed; its provider list is the one user-touched knob, and pinning
# it here means a UI removal from the outpost surfaces as drift in
# `authentik-drift-plan` instead of as a silent 404.
#
# What `terraform plan` does NOT catch: the module builds protocol_providers
# purely from this list, so a proxy provider defined in providers_proxy.tf but
# never added here plans clean and 404s at the outpost. Step 4 of the
# forward-auth checklist in README.md is the only control.
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
      "traefik_dashboard",
      "uptime_kuma",
    ]
  }
}
