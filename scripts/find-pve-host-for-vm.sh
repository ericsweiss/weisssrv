#!/usr/bin/env bash
# Locate which Proxmox host currently runs a given VM ID, printing the host
# name to stdout. For task wrappers that act on a VM without pinning its node.
#
# Usage: find-pve-host-for-vm.sh <vmid> <host1> [host2 ...]
# Exit 0 with host on stdout if found; exit 1 with diagnostics on stderr.
#
# Environment:
#   PVE_NODE_PREFIX  prefix this site's SSH targets carry that the Proxmox node
#                    names do not (default "pve-"). Applied to both API-derived
#                    answers (steps 2 and 3); step 4 already yields an SSH
#                    target. Set to "" for a site whose names need no rewrite.
#
# Resolution strategy (HA-resilient):
#   1. Find the first reachable host from the provided list.
#   2. Try ha-manager status on that host (for HA-managed services).
#   3. Fall back to pvesh /cluster/resources for any cluster-known VM.
#   4. Fall back to scanning each host with qm status (works without cluster).

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/shell-lib.sh
. "$_SCRIPT_DIR/shell-lib.sh"

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <vmid> <host1> [host2 ...]" >&2
    exit 2
fi

VMID="$1"
shift
HOSTS=("$@")

# The API reports the bare Proxmox node name; the SSH target may carry a
# prefix. `${x-y}` (not `${x:-y}`) so an explicitly empty value disables the
# rewrite.
PVE_NODE_PREFIX="${PVE_NODE_PREFIX-pve-}"

# VMID is interpolated into a remote shell command and an inline Python
# snippet below, so pin it to a positive integer first.
if [[ ! "$VMID" =~ ^[0-9]+$ ]]; then
    echo "ERROR: VMID must be a positive integer (got: ${VMID})" >&2
    exit 2
fi

# Step 1: pick a reachable host as cluster entry point.
REACHABLE=""
for host in "${HOSTS[@]}"; do
    if ssh_probe "$host" "true" 2>/dev/null; then
        REACHABLE="$host"
        break
    fi
done
if [ -z "$REACHABLE" ]; then
    echo "ERROR: no reachable host in: ${HOSTS[*]}" >&2
    exit 1
fi

# Step 2: ha-manager (preferred when the service is HA-managed). `|| true`
# swallows the grep miss for a non-HA VM so steps 3/4 still run. The
# `([[:space:]]|$)` boundary keeps vm:154 from matching vm:1540, and `sed -n
# …p` prints only lines the substitution matched, so an unparseable status
# line falls through instead of landing verbatim in $NODE.
NODE=$(ssh_probe "$REACHABLE" "sudo ha-manager status 2>/dev/null | grep -E 'service vm:${VMID}([[:space:]]|\$)'" 2>/dev/null \
    | sed -n 's/.*(\([^,]*\),.*/\1/p' || true)

# Step 3: cluster resources (covers non-HA VMs known to the cluster)
if [ -z "$NODE" ]; then
    NODE=$(ssh_probe "$REACHABLE" \
        "sudo pvesh get /cluster/resources --type vm --output-format json 2>/dev/null" 2>/dev/null \
        | python3 -c "import sys, json; d = json.load(sys.stdin); v = [x for x in d if x.get('vmid') == ${VMID}]; print(v[0]['node'] if v else '')" 2>/dev/null \
        || true)
fi

# Both API-derived branches normalize here, to exactly one $PVE_NODE_PREFIX.
# Before step 4 on purpose: that branch sets NODE from the caller's own SSH
# targets, which are already connectable names.
if [ -n "$NODE" ] && [ -n "$PVE_NODE_PREFIX" ]; then
    NODE="${PVE_NODE_PREFIX}${NODE#"$PVE_NODE_PREFIX"}"
fi

# Step 4: per-host scan (fallback when cluster API unavailable)
# Capture then test, not `ssh | grep -q`: under pipefail an early pipe close can
# SIGPIPE ssh and false-report not-found.
if [ -z "$NODE" ]; then
    for host in "${HOSTS[@]}"; do
        qm_status=$(ssh_probe "$host" "sudo qm status ${VMID}" 2>/dev/null || true)
        if printf '%s' "$qm_status" | grep -q "status:"; then
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
