#!/usr/bin/env bash
# Shared shell helpers sourced by repo scripts. Function-only: NO top-level
# side effects, so sourcing is safe even under a caller's `set -e`.
#
# Source via the _SCRIPT_DIR pattern (same as collect-state-lib.sh):
#   _SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
#   # shellcheck source=scripts/shell-lib.sh
#   . "$_SCRIPT_DIR/shell-lib.sh"

# Run "$@" under a hard wall-clock bound (first arg = seconds). Prefers GNU
# coreutils `timeout`, then `gtimeout` (macOS: brew install coreutils), and
# finally — if neither exists — runs the command unbounded so callers still
# work (their own ssh ConnectTimeout/ServerAlive* options are the only guard
# on that fallback path).
timeout_cmd() {
    local seconds="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$seconds" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$seconds" "$@"
    else
        "$@"
    fi
}
