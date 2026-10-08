# CRITICAL: proxy_provider_keys is the outpost's entire provider list. The
# module fails the plan when a proxy provider is missing from this list. The
# list is ORDERED, so append new keys at the end. Setting embedded_outpost to
# null is a DESTROY of authentik's own outpost. See README.md § Adding a new
# application.
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
