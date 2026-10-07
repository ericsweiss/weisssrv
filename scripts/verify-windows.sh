#!/usr/bin/env bash
# Windows VM smoke test: RDP reachability. A closed port FAILs, and is expected
# before Windows is installed (docs/39).
# WINDOWS_IP comes from scripts/hosts.env, which `task windows:verify` loads.
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/smoke-lib.sh
. "$_SCRIPT_DIR/smoke-lib.sh"

: "${WINDOWS_IP:?WINDOWS_IP is not set — run task hosts:sync and invoke via task windows:verify}"

echo "=== Windows VM Smoke Test ==="
smoke_check "RDP 3389 ($WINDOWS_IP)" check_tcp_port "$WINDOWS_IP" 3389
if [ "$SMOKE_FAIL" -ne 0 ]; then
    echo "No RDP: the VM is stopped or Windows is not yet installed (docs/39)."
fi
smoke_summary
