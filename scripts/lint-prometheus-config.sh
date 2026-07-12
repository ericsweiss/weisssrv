#!/usr/bin/env bash
# Lint the kube-prometheus-stack alert rules + Alertmanager config that
# kubeconform/flux-lint can't reach (PromQL in HelmRelease values; the AM config
# in an ExternalSecret template). Extracts them via
# scripts/extract-prometheus-config.py, then validates with promtool / amtool.
#
# Requires promtool + amtool on PATH (the prometheus-config-lint CI job installs
# pinned copies). Run from the repo root. Exits non-zero on any failure.
set -eo pipefail

for tool in promtool amtool python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "ERROR: $tool not found on PATH" >&2
        exit 1
    }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "=== Extracting + checking Prometheus alert rules ==="
python3 scripts/extract-prometheus-config.py rules "$work/rules.yaml"
promtool check rules "$work/rules.yaml"

echo ""
echo "=== Extracting + checking Alertmanager config ==="
python3 scripts/extract-prometheus-config.py alertmanager "$work/alertmanager.yaml"
amtool check-config "$work/alertmanager.yaml"

echo ""
echo "Prometheus rules + Alertmanager config are valid."
