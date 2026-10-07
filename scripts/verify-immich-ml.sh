#!/usr/bin/env bash
# Immich ML LXC smoke tests: compose stack up, /dev/dri passed through, /ping
# answers. Exits non-zero when any probe FAILs.
#
# IMMICH_ML_IP comes from scripts/hosts.env, which `task immich-ml:verify` loads.
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/smoke-lib.sh
. "$_SCRIPT_DIR/smoke-lib.sh"

: "${IMMICH_ML_IP:?IMMICH_ML_IP is not set — run task hosts:sync and invoke via task immich-ml:verify}"

COMPOSE_DIR="/opt/immich-ml/compose"

echo "=== Immich ML Smoke Tests ==="
echo ""

compose_running() {
    local out
    out=$(ssh "eric@$IMMICH_ML_IP" \
        "sudo docker compose --project-directory $COMPOSE_DIR ps --status running --quiet" \
        2>/dev/null) || return 1
    [ -n "$out" ]
}
smoke_check "compose stack running" compose_running

gpu_device_present() {
    ssh "eric@$IMMICH_ML_IP" "test -e /dev/dri/renderD128" 2>/dev/null
}
smoke_check "GPU device /dev/dri present" gpu_device_present

# sg-immich-ml admits :3003 from the Immich VM only, so a probe from the
# operator's LAN source is dropped by design.
ml_ping_ok() {
    ssh "eric@$IMMICH_ML_IP" "curl -sf --max-time 10 http://127.0.0.1:3003/ping" >/dev/null 2>&1
}
smoke_check "ML /ping answers" ml_ping_ok

smoke_summary
