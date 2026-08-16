#!/usr/bin/env bash
# Read one key out of kubernetes/infrastructure/sources/cluster-config.yaml.
#
# The ConfigMap is the cluster's identity source of truth (domains, CIDRs, VIPs)
# and check-cluster-literals.py holds it equal to the Ansible inventory. Host-side
# tooling — the Taskfile, the diagnostic scripts — sat outside both single-source
# mechanisms and re-spelled the VIPs as literals; this is how it reads them
# instead. scripts/hosts.env cannot carry them: its generator resolves inventory
# GROUPS, and a VIP is not a host.
#
#   scripts/cluster-config-value.sh cluster_api_vip
#   scripts/cluster-config-value.sh cluster_metallb_public_vip cluster_api_vip
#
# Prints one value per key, space-separated, and fails if any key is absent — an
# empty value would silently become a no-op sed or an empty probe list.

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLUSTER_CONFIG="${CLUSTER_CONFIG:-$_SCRIPT_DIR/../kubernetes/infrastructure/sources/cluster-config.yaml}"

if [ $# -eq 0 ]; then
    echo "usage: $(basename "$0") <key> [key...]" >&2
    exit 2
fi
if [ ! -f "$CLUSTER_CONFIG" ]; then
    echo "ERROR: $CLUSTER_CONFIG not found" >&2
    exit 1
fi

out=""
for key in "$@"; do
    # Only the `data:` scalars are of this shape; quotes are optional in YAML,
    # so both spellings are accepted and neither is emitted.
    value=$(sed -n "s/^[[:space:]]*${key}:[[:space:]]*[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*$/\1/p" \
        "$CLUSTER_CONFIG" | head -1)
    if [ -z "$value" ]; then
        echo "ERROR: $key is not set in $CLUSTER_CONFIG" >&2
        exit 1
    fi
    out="${out:+$out }$value"
done
printf '%s\n' "$out"
