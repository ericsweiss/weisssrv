#!/usr/bin/env bash
# Nextcloud smoke tests: web UI on both hosts, occ status, exporter metrics.
# Exits non-zero when any probe FAILs.
#
# NEXTCLOUD_IP comes from scripts/hosts.env, which `task nextcloud:verify` loads.
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/smoke-lib.sh
. "$_SCRIPT_DIR/smoke-lib.sh"

: "${NEXTCLOUD_IP:?NEXTCLOUD_IP is not set — run task hosts:sync and invoke via task nextcloud:verify}"

COMPOSE="/mnt/nextcloud-app/compose/docker-compose.yml"

echo "=== Nextcloud Smoke Tests ==="
echo ""

# SSO-only, so a 302 redirect to Authentik is a pass.
smoke_check "Nextcloud Web UI (cloud.ericsweiss.com)" check_http_ok "https://cloud.ericsweiss.com"
smoke_check "Nextcloud Web UI (cloud.esweiss.com)" check_http_ok "https://cloud.esweiss.com"

smoke_check "Nextcloud occ status (installed)" smoke_ssh_contains "eric@$NEXTCLOUD_IP" \
    "sudo docker compose -f $COMPOSE exec -T -u www-data nextcloud php occ status --output=json" \
    '"installed":true'

# sg-nextcloud scopes :9205 to k3s_nodes (the in-cluster scrape source), so the
# deploy host is firewall-blocked by design — probe the VM's loopback instead.
smoke_check "Nextcloud exporter (:9205 on $NEXTCLOUD_IP)" smoke_ssh_matches \
    "eric@$NEXTCLOUD_IP" "curl -s --max-time 10 http://localhost:9205/metrics" '^nextcloud_up'

smoke_summary
