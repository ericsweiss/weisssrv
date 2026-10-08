#!/usr/bin/env bash
# Immich smoke tests: web UI on both hosts, health, metrics, database, mounts.
# Exits non-zero when any probe FAILs.
#
# IMMICH_IP comes from scripts/hosts.env, which `task immich:verify` loads.
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/smoke-lib.sh
. "$_SCRIPT_DIR/smoke-lib.sh"

: "${IMMICH_IP:?IMMICH_IP is not set — run task hosts:sync and invoke via task immich:verify}"

COMPOSE_DIR="/mnt/immich-app/compose"

echo "=== Immich Smoke Tests ==="
echo ""

smoke_check "Immich Web UI (photos.ericsweiss.com)" check_http_ok "https://photos.ericsweiss.com"
smoke_check "Immich Web UI (photos.esweiss.com)" check_http_ok "https://photos.esweiss.com"

smoke_check "Immich health (/api/server/ping)" \
    smoke_url_contains "https://photos.esweiss.com/api/server/ping" 'pong'

# The compose stack publishes 8081/8082 on the VM's LAN address, not loopback.
# sg-immich scopes those ports to the k3s scrapers, so a probe from the
# operator's LAN/tailnet source is firewall-dropped and this one runs on the VM.
metrics_up() {
    local out
    out=$(ssh "eric@$IMMICH_IP" "curl -s --max-time 10 http://$IMMICH_IP:8081/metrics" 2>/dev/null) || return 1
    [ -n "$out" ]
}
smoke_check "Immich API metrics ($IMMICH_IP:8081 on the VM)" metrics_up

smoke_check "Postgres container healthy" smoke_ssh_contains "eric@$IMMICH_IP" \
    "sudo docker compose --project-directory $COMPOSE_DIR exec -T database pg_isready -U postgres -d immich" \
    'accepting connections'

mounts_present() {
    ssh "eric@$IMMICH_IP" \
        "mountpoint -q /mnt/immich-app && mountpoint -q /mnt/immich-postgres && mountpoint -q /mnt/immich-data" \
        2>/dev/null
}
smoke_check "zvol mounts (app/postgres/data)" mounts_present

smoke_summary
