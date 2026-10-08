#!/usr/bin/env bash
# Print which Proxmox host runs a VM ID: exit 0 with the host on stdout, exit 1
# with diagnostics on stderr. Usage: find-pve-host-for-vm.sh <vmid> <host>...
# Contract + env: weisssrv-lib docs/SCRIPTS.md - find-pve-host-for-vm.sh.

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

# Step 2: ha-manager, for HA-managed services. `|| true` swallows the grep miss
# for a non-HA VM so steps 3/4 still run; the `([[:space:]]|$)` boundary keeps
# vm:154 from matching vm:1540, and `sed -n …p` drops unparseable status lines.
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
# `case`, not `| grep -q`: grep exits on the first match and pipefail turns the
# producer's SIGPIPE into a false not-found.
if [ -z "$NODE" ]; then
    for host in "${HOSTS[@]}"; do
        qm_status=$(ssh_probe "$host" "sudo qm status ${VMID}" 2>/dev/null || true)
        case "$qm_status" in
            *status:*)
                NODE="$host"
                break
                ;;
        esac
    done
fi

if [ -z "$NODE" ]; then
    echo "ERROR: VM ${VMID} not found on any of: ${HOSTS[*]}" >&2
    exit 1
fi

echo "$NODE"
