#!/usr/bin/env bash
# Cluster health check, runs after each maintenance op (and after the
# `maintenance-run-all` wrapper) to validate that nothing got knocked over.
#
# Used by the maintenance-* CI jobs in .gitlab-ci.yml AND by the standalone
# manual `maintenance-verify` job. Keep self-contained — no project-internal
# helpers — so the same script works from any CI image with kubectl + curl.
#
# Exits 0 if cluster is healthy, 1 if any critical check fails.

set -eo pipefail

echo "=== Post-Maintenance Verification ==="
ERRORS=0

echo "Checking k3s node status..."
NODE_OUTPUT=$(kubectl get nodes --no-headers)
echo "$NODE_OUTPUT"
if [ -z "$NODE_OUTPUT" ]; then
  echo "ERROR: kubectl returned no nodes"
  ERRORS=$((ERRORS + 1))
else
  # `$2 !~ /^Ready/` accepts both "Ready" and "Ready,SchedulingDisabled"
  # (cordoned-but-healthy node, e.g., during a k3s rolling upgrade).
  NOT_READY=$(echo "$NODE_OUTPUT" | awk '$2 !~ /^Ready/ {count++} END {print count+0}')
  if [ "$NOT_READY" -gt 0 ]; then
    echo "ERROR: $NOT_READY node(s) not Ready"
    ERRORS=$((ERRORS + 1))
  else
    echo "All nodes Ready"
  fi
fi

echo ""
echo "Checking for unhealthy pods..."
# Guard the kubectl pipelines so a transient API failure (combined with
# `set -o pipefail`) doesn't abort verify before subsequent checks run.
UNHEALTHY=$(kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded --no-headers 2>/dev/null | wc -l) || {
  echo "ERROR: failed to query non-running pods"
  ERRORS=$((ERRORS + 1))
  UNHEALTHY=0
}
# $3 is READY column (e.g. "1/1"); split on "/" and compare numerator vs denominator
DEGRADED=$(kubectl get pods -A --no-headers 2>/dev/null | awk '$4 != "Completed" && $4 != "Succeeded" {split($3, a, "/"); if (a[1] != a[2]) print}' | wc -l) || {
  echo "ERROR: failed to query degraded pods"
  ERRORS=$((ERRORS + 1))
  DEGRADED=0
}
if [ "$UNHEALTHY" -gt 0 ] || [ "$DEGRADED" -gt 0 ]; then
  echo "ERROR: $UNHEALTHY non-running + $DEGRADED degraded pod(s):"
  kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || true
  kubectl get pods -A --no-headers 2>/dev/null | awk '$4 != "Completed" && $4 != "Succeeded" {split($3, a, "/"); if (a[1] != a[2]) print}' || true
  ERRORS=$((ERRORS + 1))
else
  echo "All pods healthy"
fi

echo ""
echo "Checking critical deployments..."
for dep in traefik:traefik coredns:kube-system cert-manager:cert-manager metallb-controller:metallb-system authentik-server:authentik; do
  name="${dep%%:*}"
  ns="${dep##*:}"
  AVAIL=$(kubectl get deployment "$name" -n "$ns" -o jsonpath='{.status.availableReplicas}' 2>/dev/null)
  DESIRED=$(kubectl get deployment "$name" -n "$ns" -o jsonpath='{.spec.replicas}' 2>/dev/null)
  if [ -n "$AVAIL" ] && [ "$AVAIL" -ge "${DESIRED:-1}" ] 2>/dev/null; then
    echo "  $name ($ns): $AVAIL/$DESIRED available"
  else
    echo "  ERROR: $name ($ns): ${AVAIL:-0}/${DESIRED:-?} available"
    ERRORS=$((ERRORS + 1))
  fi
done

echo ""
echo "Checking GitLab health..."
if curl -sf --max-time 10 https://git.ericsweiss.com/-/readiness > /dev/null 2>&1; then
  echo "GitLab: OK"
else
  echo "ERROR: GitLab health check failed"
  ERRORS=$((ERRORS + 1))
fi

echo ""
echo "Checking cluster DNS (internal service resolution)..."
DNS_OUTPUT=$(kubectl run "dns-verify-${CI_JOB_ID:-$$}" --attach --rm --restart=Never --timeout=30s \
    --image=busybox:1.37 --command -- sh -c \
    "nslookup kubernetes.default.svc.cluster.local > /dev/null 2>&1 && echo DNS_PASS || echo DNS_FAIL" 2>&1 || true)
if echo "$DNS_OUTPUT" | grep -q "DNS_PASS"; then
  echo "Cluster DNS: OK (kubernetes.default.svc.cluster.local resolves)"
else
  echo "ERROR: Cluster DNS cannot resolve kubernetes.default.svc.cluster.local"
  ERRORS=$((ERRORS + 1))
fi

echo ""
if [ "$ERRORS" -gt 0 ]; then
  echo "=== VERIFICATION FAILED: $ERRORS critical issue(s) ==="
  exit 1
fi
echo "=== Post-Maintenance Verification Passed ==="
