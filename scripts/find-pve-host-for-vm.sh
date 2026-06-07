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

ssh_quick() {
    ssh -o ConnectTimeout=2 -o BatchMode=yes "$@"
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
NODE=$(ssh_quick "$REACHABLE" "sudo ha-manager status 2>/dev/null | grep -E 'service vm:${VMID}([[:space:]]|\$)'" 2>/dev/null \
    | sed 's/.*(\([^,]*\),.*/\1/' || true)

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
