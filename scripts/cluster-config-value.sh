#!/usr/bin/env bash
# Read keys out of kubernetes/infrastructure/sources/cluster-config.yaml, the
# single source for domains, CIDRs and VIPs. Only the `data:` scalars are read.
# Usage: cluster-config-value.sh <key> [key...] -> one value per key, space-separated.

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_CONFIG="${CLUSTER_CONFIG:-$_SCRIPT_DIR/../kubernetes/infrastructure/sources/cluster-config.yaml}"

if [ $# -eq 0 ]; then
    echo "usage: $(basename "$0") <key> [key...]" >&2
    exit 2
fi
if [ ! -f "$CLUSTER_CONFIG" ]; then
    echo "ERROR: $CLUSTER_CONFIG not found" >&2
    exit 2
fi

python3 - "$CLUSTER_CONFIG" "$@" <<'PYEOF'
import sys

import yaml

path = sys.argv[1]
with open(path) as fh:
    doc = yaml.safe_load(fh)
data = (doc or {}).get("data") or {}
values = []
for key in sys.argv[2:]:
    value = data.get(key)
    if value is None or str(value) == "":
        sys.exit(f"ERROR: {key} is not set in {path}")
    values.append(str(value))
print(" ".join(values))
PYEOF
