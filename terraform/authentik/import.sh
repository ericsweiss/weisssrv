#!/usr/bin/env bash
# One-time / disaster-recovery state bootstrap for terraform/authentik.
#
# Runs `terraform import` for every managed resource (the same address<->id
# map as imports.tf), skipping anything already in state, so it is idempotent
# and safe to re-run. `terraform import` only READS the authentik API and
# writes Terraform state — it never modifies authentik objects and never
# applies configuration.
#
# ORDER MATTERS: providers first, then groups, then applications. The legacy
# import command validates the whole configuration on every invocation, and
# local.applications references the provider resources — importing an
# application before its provider exists in state would evaluate against an
# unknown provider (fine), but the reverse order keeps every evaluation fully
# known and mirrors the resource dependency graph.
#
# Invoke via `task terraform:authentik-import` (wraps this in `op run` with
# the TF_VAR_* credentials and TF_HTTP_* state backend env).
set -euo pipefail
cd "$(dirname "$0")"

# address|id — providers import by numeric pk, groups by uuid, applications
# by slug (matches imports.tf).
IMPORTS='
authentik_provider_proxy.sonarr|4
authentik_provider_proxy.radarr|5
authentik_provider_proxy.lidarr|6
authentik_provider_proxy.qbittorrent|7
authentik_provider_proxy.nzbget|8
authentik_provider_proxy.prowlarr|9
authentik_provider_proxy.pulsarr|10
authentik_provider_proxy.hermes|16
authentik_provider_proxy.wireguard_easy|17
authentik_provider_oauth2.mealie|1
authentik_provider_oauth2.bar_assistant|2
authentik_provider_oauth2.home_assistant|3
authentik_provider_oauth2.grafana|13
authentik_provider_oauth2.nextcloud|14
authentik_provider_oauth2.immich|15
authentik_provider_saml.gitlab|12
authentik_group.app["admin"]|53d5caec-677c-4f61-bded-d4f6abfdc987
authentik_group.app["gitlab-admins"]|c1813efe-df8a-4d1b-95fe-2fba678a7beb
authentik_group.app["gitlab-users"]|44e845b4-49fa-4178-b3f4-0064b322b783
authentik_group.app["grafana-admins"]|2aa4637f-2a59-4b7a-857e-bbeefaaa75d8
authentik_group.app["grafana-users"]|888ac6b4-b6f5-4e2f-90a6-d53847905206
authentik_group.app["hermes-users"]|e6d6c372-93c3-4be8-8601-d40bce26127e
authentik_group.app["immich-users"]|c07e7bbc-294f-49ab-9cb5-8009387cf0c9
authentik_group.app["mealie-admins"]|7bbab05a-add9-4bc4-8803-043082b9629d
authentik_group.app["mealie-users"]|63d3f306-c7dd-4b4d-a801-3cc408304c4d
authentik_group.app["nextcloud-users"]|afcfb27c-be96-4860-8613-fb81f4cce06b
authentik_group.app["vpn-admins"]|25c11794-4b0d-4344-8ddf-bd9272c04a6d
authentik_group.authentik_admins|b3dbbcbb-2207-41fb-bf2b-dacd7aeec37c
authentik_application.app["bar"]|bar
authentik_application.app["food"]|food
authentik_application.app["home"]|home
authentik_application.app["photos"]|photos
authentik_application.app["agent"]|agent
authentik_application.app["cloud"]|cloud
authentik_application.app["git"]|git
authentik_application.app["grafana"]|grafana
authentik_application.app["vpn"]|vpn
authentik_application.app["movies"]|movies
authentik_application.app["music"]|music
authentik_application.app["nzbget"]|nzbget
authentik_application.app["prowlarr"]|prowlarr
authentik_application.app["pulsarr"]|pulsarr
authentik_application.app["qbittorrent"]|qbittorrent
authentik_application.app["tv"]|tv
'

STATE="$(terraform state list 2>/dev/null || true)"
imported=0
skipped=0
while IFS='|' read -r addr id; do
  [ -n "${addr}" ] || continue
  if printf '%s\n' "${STATE}" | grep -Fxq "${addr}"; then
    skipped=$((skipped + 1))
    continue
  fi
  echo "==> terraform import '${addr}' '${id}'"
  terraform import -input=false "${addr}" "${id}"
  imported=$((imported + 1))
done <<EOF
${IMPORTS}
EOF

echo "import.sh done: ${imported} imported, ${skipped} already in state."
