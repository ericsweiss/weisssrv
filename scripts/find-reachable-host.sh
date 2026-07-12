#!/usr/bin/env bash
# Print the first reachable SSH target from the arguments (each optionally
# user@-prefixed), or exit 1 if none respond. Shared by the Taskfile tasks that
# need a cluster entry point: k3s:kubeconfig / k3s:backup (eric@<ip> targets)
# and proxmox:ha-status (bare Tailscale hostnames, ssh-config resolves the user).
#
# Usage: scripts/find-reachable-host.sh <ssh-target> [<ssh-target> ...]
#   scripts/find-reachable-host.sh eric@192.168.0.222 eric@192.168.0.223
#   scripts/find-reachable-host.sh pve-nas-01 pve-opt-01
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/shell-lib.sh
. "$_SCRIPT_DIR/shell-lib.sh"

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <ssh-target> [ssh-target ...]" >&2
    exit 2
fi

for target in "$@"; do
    # ConnectTimeout bounds the TCP connect; ServerAlive* trips a dead
    # post-connect channel; timeout_cmd (shell-lib.sh) is the wall-clock
    # backstop for a host that accepts the connection but then stalls
    # (PAM/sssd, disk-stuck remote shell).
    if timeout_cmd 6 ssh -o ConnectTimeout=2 -o BatchMode=yes \
        -o ServerAliveInterval=2 -o ServerAliveCountMax=2 \
        "$target" "true" 2>/dev/null; then
        echo "$target"
        exit 0
    fi
done

echo "ERROR: no reachable SSH target in: $*" >&2
exit 1
