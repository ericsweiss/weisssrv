#!/usr/bin/env bash
# One-time / disaster-recovery state bootstrap for terraform/authentik.
#
# Runs `terraform import` for every ADOPTED resource (the same address<->id map
# as imports.tf, checked against it before anything runs — see the guard below),
# skipping anything already in state, so it is idempotent and safe to re-run.
# `terraform import` only READS the authentik API and writes Terraform
# state — it never modifies authentik objects, never applies config.
# Terraform-authored objects are deliberately absent; see the README for the
# extra DR steps they need.
#
# Addresses are MODULE-QUALIFIED (module.sso.<resource>.this["<key>"]) since the
# move onto the library authentik-sso module — the key is the map key in the
# site-data files. Order (providers, groups, applications) is a legibility
# choice; the module's for_each key sets come from configuration, not state, so
# a partially-imported state cannot make an address unresolvable.
#
# "Already in state" means EITHER address while moved.tf exists: `moved` blocks
# reach state only on apply, so pre-apply state still holds the pre-move root
# addresses, and re-importing into the addresses the moves target makes
# Terraform refuse the moves and strand the old ones as DESTROY-planned orphans.
# Pairs are read out of moved.tf, not hardcoded, so the guard cannot drift.
#
# Invoke via `task terraform:authentik-import` (wraps this in `op run` with
# the TF_VAR_* credentials and TF_HTTP_* state backend env).
set -euo pipefail
cd "$(dirname "$0")"

# address|id — providers import by numeric pk, groups by uuid, applications
# by slug (matches imports.tf).
IMPORTS='
module.sso.authentik_provider_proxy.this["sonarr"]|4
module.sso.authentik_provider_proxy.this["radarr"]|5
module.sso.authentik_provider_proxy.this["lidarr"]|6
module.sso.authentik_provider_proxy.this["qbittorrent"]|7
module.sso.authentik_provider_proxy.this["nzbget"]|8
module.sso.authentik_provider_proxy.this["prowlarr"]|9
module.sso.authentik_provider_proxy.this["pulsarr"]|10
module.sso.authentik_provider_proxy.this["wireguard_easy"]|17
module.sso.authentik_outpost.embedded[0]|436f896e-05cd-4cf7-b9db-7141ad70927b
module.sso.authentik_provider_oauth2.this["mealie"]|1
module.sso.authentik_provider_oauth2.this["bar_assistant"]|2
module.sso.authentik_provider_oauth2.this["home_assistant"]|3
module.sso.authentik_provider_oauth2.this["grafana"]|13
module.sso.authentik_provider_oauth2.this["nextcloud"]|14
module.sso.authentik_provider_oauth2.this["immich"]|15
module.sso.authentik_provider_saml.this["gitlab"]|12
module.sso.authentik_group.this["admin"]|53d5caec-677c-4f61-bded-d4f6abfdc987
module.sso.authentik_group.this["gitlab-admins"]|c1813efe-df8a-4d1b-95fe-2fba678a7beb
module.sso.authentik_group.this["gitlab-users"]|44e845b4-49fa-4178-b3f4-0064b322b783
module.sso.authentik_group.this["grafana-admins"]|2aa4637f-2a59-4b7a-857e-bbeefaaa75d8
module.sso.authentik_group.this["grafana-users"]|888ac6b4-b6f5-4e2f-90a6-d53847905206
module.sso.authentik_group.this["hermes-users"]|e6d6c372-93c3-4be8-8601-d40bce26127e
module.sso.authentik_group.this["immich-users"]|c07e7bbc-294f-49ab-9cb5-8009387cf0c9
module.sso.authentik_group.this["mealie-admins"]|7bbab05a-add9-4bc4-8803-043082b9629d
module.sso.authentik_group.this["mealie-users"]|63d3f306-c7dd-4b4d-a801-3cc408304c4d
module.sso.authentik_group.this["nextcloud-users"]|afcfb27c-be96-4860-8613-fb81f4cce06b
module.sso.authentik_group.this["vpn-admins"]|25c11794-4b0d-4344-8ddf-bd9272c04a6d
module.sso.authentik_group.this["authentik-admins"]|b3dbbcbb-2207-41fb-bf2b-dacd7aeec37c
module.sso.authentik_application.this["bar"]|bar
module.sso.authentik_application.this["food"]|food
module.sso.authentik_application.this["home"]|home
module.sso.authentik_application.this["photos"]|photos
module.sso.authentik_application.this["agent"]|agent
module.sso.authentik_application.this["cloud"]|cloud
module.sso.authentik_application.this["git"]|git
module.sso.authentik_application.this["grafana"]|grafana
module.sso.authentik_application.this["vpn"]|vpn
module.sso.authentik_application.this["movies"]|movies
module.sso.authentik_application.this["music"]|music
module.sso.authentik_application.this["nzbget"]|nzbget
module.sso.authentik_application.this["prowlarr"]|prowlarr
module.sso.authentik_application.this["pulsarr"]|pulsarr
module.sso.authentik_application.this["qbittorrent"]|qbittorrent
module.sso.authentik_application.this["tv"]|tv
'

# Cross-check the hand-maintained IMPORTS list against imports.tf so no
# address|id pair can drift — a transposed uuid binds a resource to the wrong
# object and the next apply rewrites both. The extractor handles exactly
# imports.tf's three block shapes and aborts on a fourth rather than
# under-reporting.
derived_imports="$(awk '
function fail(msg) { print "extractor: " msg > "/dev/stderr"; aborted = 1; exit 2 }
function strip(s) { gsub(/^[ \t]*[a-z_]+[ \t]*=[ \t]*/, "", s); gsub(/[ \t]+$/, "", s); return s }
function dequote(s) { gsub(/^"|"$/, "", s); return s }
function process(   i, line, fe, to, id, addr, j, k, v) {
  fe = ""; to = ""; id = ""
  split("", pairk); split("", pairv); npairs = 0
  for (i = 1; i <= nlines; i++) {
    line = lines[i]
    if (line ~ /^[ \t]*(#|\/\/)/ || line ~ /^[ \t]*$/) continue
    if (line ~ /^[ \t]*for_each[ \t]*=/) { fe = strip(line); continue }
    if (line ~ /^[ \t]*to[ \t]*=/)       { to = strip(line); continue }
    if (line ~ /^[ \t]*id[ \t]*=/)       { id = dequote(strip(line)); continue }
    if (line ~ /^[ \t]*"[^"]+"[ \t]*=[ \t]*"[^"]+"[ \t]*$/) {
      j = index(line, "="); npairs++
      k = substr(line, 1, j - 1); v = substr(line, j + 1)
      gsub(/^[ \t]*"|"[ \t]*$/, "", k); gsub(/^[ \t]*"|"[ \t]*$/, "", v)
      pairk[npairs] = k; pairv[npairs] = v
      continue
    }
    if (line ~ /^[ \t]*\}[ \t]*$/) continue
    fail("unrecognised line in an import block: " line)
  }
  if (to == "") fail("import block with no `to`")
  if (fe == "") {
    if (id == "") fail("import block with no `id`: " to)
    print to "|" id
  } else if (fe == "local.imported_application_slugs") {
    if (to !~ /\[each\.value\]$/ || id != "each.value") fail("unexpected slug for_each: " to)
    for (j = 1; j <= nslugs; j++) {
      addr = to; sub(/\[each\.value\]$/, "[\"" slugs[j] "\"]", addr)
      print addr "|" slugs[j]
    }
  } else if (fe == "{") {
    if (to !~ /\[each\.key\]$/ || id != "each.value") fail("unexpected map for_each: " to)
    if (npairs == 0) fail("inline for_each map with no entries: " to)
    for (j = 1; j <= npairs; j++) {
      addr = to; sub(/\[each\.key\]$/, "[\"" pairk[j] "\"]", addr)
      print addr "|" pairv[j]
    }
  } else {
    fail("unrecognised for_each source: " fe)
  }
}
/imported_application_slugs = toset\(\[/ { in_slugs = 1; next }
in_slugs && /^[ \t]*\]\)/               { in_slugs = 0; next }
in_slugs {
  if (match($0, /"[^"]+"/)) slugs[++nslugs] = substr($0, RSTART + 1, RLENGTH - 2)
  next
}
/^import[ \t]*\{/ { inblock = 1; depth = 1; nlines = 0; next }
inblock {
  saved = $0
  depth += gsub(/\{/, "{")
  depth -= gsub(/\}/, "}")
  if (depth <= 0) { process(); inblock = 0; next }
  lines[++nlines] = saved
}
END { if (!aborted && inblock) fail("unterminated import block") }
' imports.tf | sort)"

listed_imports="$(printf '%s\n' "${IMPORTS}" | sed '/^[[:space:]]*$/d' | sort)"

if [ "${derived_imports}" != "${listed_imports}" ]; then
  echo "ERROR: import.sh's IMPORTS list is out of sync with imports.tf." >&2
  echo "Only in imports.tf:" >&2
  comm -23 <(printf '%s\n' "${derived_imports}") <(printf '%s\n' "${listed_imports}") >&2
  echo "Only in import.sh:" >&2
  comm -13 <(printf '%s\n' "${derived_imports}") <(printf '%s\n' "${listed_imports}") >&2
  exit 1
fi

# new_address|old_address, derived from moved.tf's from=/to= pairs (see the
# header). Absent moved.tf, this is empty and every check falls back to the new
# address alone.
moved_pairs=""
if [ -f moved.tf ]; then
  moved_pairs="$(awk '
    function strip(s) { sub(/^[ \t]*[a-z]+[ \t]*=[ \t]*/, "", s); sub(/[ \t]+$/, "", s); return s }
    /^[ \t]*from[ \t]*=/ { from = strip($0); next }
    /^[ \t]*to[ \t]*=/   { if (from != "") { print strip($0) "|" from; from = "" } next }
  ' moved.tf)"
fi

STATE="$(terraform state list 2>/dev/null || true)"
imported=0
skipped=0
skipped_premove=0
while IFS='|' read -r addr id; do
  [ -n "${addr}" ] || continue
  if printf '%s\n' "${STATE}" | grep -Fxq "${addr}"; then
    skipped=$((skipped + 1))
    continue
  fi
  old_addr="$(printf '%s\n' "${moved_pairs}" | awk -F'|' -v a="${addr}" '$1 == a { print $2; exit }')"
  if [ -n "${old_addr}" ] && printf '%s\n' "${STATE}" | grep -Fxq "${old_addr}"; then
    echo "--> skip '${addr}': state still holds it at its pre-move address '${old_addr}'"
    skipped=$((skipped + 1))
    skipped_premove=$((skipped_premove + 1))
    continue
  fi
  echo "==> terraform import '${addr}' '${id}'"
  terraform import -input=false "${addr}" "${id}"
  imported=$((imported + 1))
done <<EOF
${IMPORTS}
EOF

echo "import.sh done: ${imported} imported, ${skipped} already in state."
if [ "${skipped_premove}" -gt 0 ]; then
  echo "NOTE: ${skipped_premove} of those are still at their pre-move addresses — moved.tf has not been applied yet. Run the supervised 'task terraform:authentik-apply' to persist the moves." >&2
fi
