#!/usr/bin/env bash
# Guest-smoke helpers a consumer's per-guest verify-*.sh sources. Function-only
# apart from the two counters, so sourcing is safe under a caller's `set -e`.
# Contract: weisssrv-lib docs/SCRIPTS.md - Shell helpers.

SMOKE_PASS=0
SMOKE_FAIL=0

# Echo the numeric HTTP status of a HEAD request, or 000 when nothing answered.
# Always returns 0: a bare pipeline here would kill an errexit caller.
http_status() {
    local status
    status=$(curl -sI --max-time "${SMOKE_LIB_CURL_MAX_TIME:-10}" "$1" 2>/dev/null | head -1 |
        grep -oE 'HTTP/[0-9.]+ [0-9]+' | grep -oE '[0-9]+$') || true
    printf '%s\n' "${status:-000}"
}

# 2xx or 3xx.
check_http_ok() {
    local status
    status=$(http_status "$1")
    [ "$status" -ge 200 ] && [ "$status" -lt 400 ]
}

# 401 counts: it proves a container registry is running without credentials.
check_registry_ok() {
    local status
    status=$(http_status "$1")
    [ "$status" = "401" ] || { [ "$status" -ge 200 ] && [ "$status" -lt 400 ]; }
}

# Anything below 500 that actually answered — a 404 means the service is up
# with no content published.
check_http_below_500() {
    local status
    status=$(http_status "$1")
    [ "$status" != "000" ] && [ "$status" -lt 500 ]
}

check_tcp_port() {
    nc -z -w 5 "$1" "$2" 2>/dev/null
}

# CRITICAL: capture then test. `| grep -q` exits on the first match and the
# writer's SIGPIPE would invert a PASS into a FAIL under pipefail. Needles are
# literal, unlike shell-lib.sh's url_contains/ssh_contains, so a `.` or `[` in
# one cannot match output the caller did not mean.
smoke_url_contains() {
    local out
    out=$(curl -s --max-time "${SMOKE_LIB_CURL_MAX_TIME:-10}" "$1" 2>/dev/null) || return 1
    printf '%s' "$out" | grep -qF -- "$2"
}

smoke_ssh_contains() {
    local out
    out=$(ssh "$1" "$2" 2>/dev/null) || return 1
    printf '%s' "$out" | grep -qF -- "$3"
}

# smoke_ssh_contains with a deliberate extended regex, for an anchored needle.
smoke_ssh_matches() {
    local out
    out=$(ssh "$1" "$2" 2>/dev/null) || return 1
    printf '%s' "$out" | grep -qE -- "$3"
}

smoke_check() {
    local label="$1"
    shift
    printf '%s... ' "$label"
    if "$@"; then
        echo "PASS"
        SMOKE_PASS=$((SMOKE_PASS + 1))
    else
        echo "FAIL"
        SMOKE_FAIL=$((SMOKE_FAIL + 1))
    fi
}

# A probe that may legitimately not be there yet: PASS counts, a miss prints the
# reason and counts as neither pass nor fail.
smoke_optional() {
    local label="$1" reason="$2"
    shift 2
    printf '%s... ' "$label"
    if "$@"; then
        echo "PASS"
        SMOKE_PASS=$((SMOKE_PASS + 1))
    else
        echo "SKIP ($reason)"
    fi
}

smoke_summary() {
    echo ""
    echo "=== Results: $SMOKE_PASS passed, $SMOKE_FAIL failed ==="
    [ "$SMOKE_FAIL" -eq 0 ]
}
