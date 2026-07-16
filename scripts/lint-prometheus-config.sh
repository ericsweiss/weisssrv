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
echo "=== Running promtool alert unit tests ==="
# Behavioral tests for the load-bearing alerts (firing/labels/timing). The
# extracted rules keep their annotations for `promtool check rules` above; the
# unit tests run against an annotation-stripped copy so they assert alert logic,
# not churn-prone description prose. rule_files in the *.test.yaml resolve
# relative to the test file's dir, so the tests and any supplementary
# *.rules.yaml are copied alongside the stripped rules.
tests_dir="$work/rule-tests"
mkdir -p "$tests_dir"
cp scripts/prometheus-rule-tests/*.yaml "$tests_dir"/
python3 - "$work/rules.yaml" "$tests_dir" <<'PY'
import glob
import sys

import yaml


def strip_annotations(path: str, out: str) -> None:
    doc = yaml.safe_load(open(path))
    for group in doc.get("groups", []):
        for rule in group.get("rules", []):
            rule.pop("annotations", None)
    yaml.safe_dump(doc, open(out, "w"), sort_keys=False)


src, out_dir = sys.argv[1], sys.argv[2]
strip_annotations(src, f"{out_dir}/rules.yaml")
for supplementary in glob.glob(f"{out_dir}/*.rules.yaml"):
    strip_annotations(supplementary, supplementary)
PY
promtool test rules "$tests_dir"/*.test.yaml

echo ""
echo "Prometheus rules + Alertmanager config are valid; alert unit tests pass."
