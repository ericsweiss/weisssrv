# CRITICAL: proxy_provider_keys is the outpost's entire provider list. A proxy
# provider missing from it plans clean and then 404s at the outpost. The list is
# ORDERED, so append new keys at the end. Setting embedded_outpost to null is a
# DESTROY of authentik's own outpost. See README.md § Adding a new application.
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
