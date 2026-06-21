#!/usr/bin/env bash
# Locate which Proxmox host currently runs a given VM ID, printing the host
# name to stdout. Used by the home-assistant:vm-* and similar Taskfile tasks.
#
# Usage: scripts/find-pve-host-for-vm.sh <vmid> <host1> [host2 ...]
# Exit 0 with host on stdout if found; exit 1 with diagnostics on stderr.
#
# Resolution strategy (HA-resilient):
#   1. Find the first reachable host from the provided list.
#   2. Try ha-manager status on that host (for HA-managed services).
#   3. Fall back to pvesh /cluster/resources for any cluster-known VM.
#   4. Fall back to scanning each host with qm status (works without cluster).

set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <vmid> <host1> [host2 ...]" >&2
    exit 2
fi

VMID="$1"
shift
HOSTS=("$@")

# VMID is interpolated into a remote shell command (`grep "service vm:${VMID}"`)
# and an inline Python snippet (`if x.get('vmid') == ${VMID}`) below. Pin it to
# a positive integer up front so a hostile or malformed input can't break out of
# either context. Proxmox VMIDs are always positive integers in [100, 999999999].
if [[ ! "$VMID" =~ ^[0-9]+$ ]]; then
    echo "ERROR: VMID must be a positive integer (got: ${VMID})" >&2
    exit 2
fi

# Hard wall-clock bound for the SSH probes. ConnectTimeout=2 only bounds the
# TCP connect phase — a host that accepts the connection but stalls in PAM/sssd
# or a disk-stuck remote shell would otherwise hang the step-1 loop and step-4
# qm scan (and the Taskfile task calling this script) indefinitely. ServerAlive*
# trips on a dead post-connect channel; the outer `timeout` is the backstop.
# `timeout` is GNU coreutils (gtimeout via brew on macOS); falls through to a
# bare ssh if neither exists.
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

ssh_quick() {
    timeout_cmd 6 ssh -o ConnectTimeout=2 -o BatchMode=yes \
        -o ServerAliveInterval=2 -o ServerAliveCountMax=2 "$@"
}

# Step 1: pick a reachable host as cluster entry point.
REACHABLE=""
for host in "${HOSTS[@]}"; do
    if ssh_quick "$host" "true" 2>/dev/null; then
        REACHABLE="$host"
        break
    fi
done
if [ -z "$REACHABLE" ]; then
    echo "ERROR: no reachable host in: ${HOSTS[*]}" >&2
    exit 1
fi

# Step 2: ha-manager (preferred when service is HA-managed).
# `|| true` swallows the non-zero exit when the VM isn't HA-managed —
# without it, the upstream grep miss + pipefail aborts the script under
# `set -e` before steps 3/4 can run.
#
# Boundary: match vm:<N> followed by whitespace or end-of-line so
# vm:154 doesn't match vm:1540. The earlier form used `\b` which is a
# GNU-grep extension to ERE — fine on Debian/PVE today, but the
# explicit `( |\t|$)` is portable across BSD grep / busybox in case
# this script ever gets reused outside the Proxmox cluster.
# `sed -n ...p` only prints lines where the substitution actually matched.
# Without `-n` (and the trailing `p`), a line that grep matched but whose
# `(node,...)` shape sed can't parse would be echoed VERBATIM, putting the
# whole status line into $NODE instead of falling through to steps 3/4.
NODE=$(ssh_quick "$REACHABLE" "sudo ha-manager status 2>/dev/null | grep -E 'service vm:${VMID}([[:space:]]|\$)'" 2>/dev/null \
    | sed -n 's/.*(\([^,]*\),.*/\1/p' || true)

# Step 3: cluster resources (covers non-HA VMs known to the cluster)
if [ -z "$NODE" ]; then
    NODE=$(ssh_quick "$REACHABLE" \
        "sudo pvesh get /cluster/resources --type vm --output-format json 2>/dev/null" 2>/dev/null \
        | python3 -c "import sys, json; d = json.load(sys.stdin); v = [x for x in d if x.get('vmid') == ${VMID}]; print(v[0]['node'] if v else '')" 2>/dev/null \
        || true)
    if [ -n "$NODE" ]; then
        # Normalize: ensure pve- prefix
        NODE="pve-${NODE#pve-}"
    fi
fi

# Step 4: per-host scan (fallback when cluster API unavailable)
if [ -z "$NODE" ]; then
    for host in "${HOSTS[@]}"; do
        if ssh_quick "$host" "sudo qm status ${VMID}" 2>/dev/null | grep -q "status:"; then
            NODE="$host"
            break
        fi
    done
fi

if [ -z "$NODE" ]; then
    echo "ERROR: VM ${VMID} not found on any of: ${HOSTS[*]}" >&2
    exit 1
fi

echo "$NODE"
