#!/usr/bin/env bash
# GitLab smoke tests: web UI on both hosts, registry, pages, SSH, readiness and
# the Web IDE extension host. Exits non-zero when any probe FAILs.

# GITLAB_IP comes from scripts/hosts.env, which `task gitlab:verify` loads.
# --http-only drops the two nc probes for callers with no netcat (the CI
# verify job): GITLAB_IP is then read if set and never required.
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/smoke-lib.sh
. "$_SCRIPT_DIR/smoke-lib.sh"

HTTP_ONLY=0
case "${1:-}" in
    --http-only) HTTP_ONLY=1 ;;
    "") ;;
    *) echo "usage: $(basename "$0") [--http-only]" >&2; exit 2 ;;
esac

if [ "$HTTP_ONLY" -eq 0 ]; then
    : "${GITLAB_IP:?GITLAB_IP is not set — run task hosts:sync and invoke via task gitlab:verify}"
fi

echo "=== GitLab Smoke Tests ==="
echo ""

smoke_check "GitLab Web UI (git.ericsweiss.com)" check_http_ok "https://git.ericsweiss.com"
smoke_check "GitLab Web UI (git.esweiss.com)" check_http_ok "https://git.esweiss.com"
smoke_check "Container Registry (registry.git.ericsweiss.com)" \
    check_registry_ok "https://registry.git.ericsweiss.com/v2/"
smoke_optional "GitLab Pages (pages.git.ericsweiss.com)" \
    "pages service not responding - may not be configured yet" \
    check_http_below_500 "https://pages.git.ericsweiss.com"
if [ "$HTTP_ONLY" -eq 0 ]; then
    smoke_check "Git SSH port 22 ($GITLAB_IP)" check_tcp_port "$GITLAB_IP" 22
    smoke_check "Git SSH port 2222 ($GITLAB_IP)" check_tcp_port "$GITLAB_IP" 2222
fi

# Hard arm: monitoring_whitelist admits both sources this script runs from (the
# homelab LAN and a runner pod), and a degraded Gitaly/Redis/Postgres still
# serves HTML, so only readiness catches it.
smoke_check "GitLab health check (readiness)" \
    smoke_url_contains "https://git.esweiss.com/-/readiness" '"status":"ok"'

# *.ide.git.ericsweiss.com hairpins via Cloudflare, so GitLab sees a WAN source
# the whitelist excludes. Proves the wildcard host is wired, not the
# application_settings flip.
smoke_optional "Web IDE extension host route (probe.ide.git.ericsweiss.com)" \
    "monitoring_whitelist-gated; the hairpinned WAN source is not whitelisted" \
    check_http_ok "https://probe.ide.git.ericsweiss.com/-/health"

smoke_summary
