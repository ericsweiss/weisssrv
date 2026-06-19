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
# Capture failure as a verify error rather than aborting under set -e.
node_query_failed=false
NODE_OUTPUT=$(kubectl get nodes --no-headers 2>/dev/null) || node_query_failed=true
echo "$NODE_OUTPUT"
if [ "$node_query_failed" = true ]; then
  echo "ERROR: kubectl get nodes failed"
  ERRORS=$((ERRORS + 1))
elif [ -z "$NODE_OUTPUT" ]; then
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
# Report genuinely-unhealthy pods only. A single snapshot false-alarms on
# transient pods — a just-spawned CronJob pod (ContainerCreating/Pending) or a
# Completed batch pod — which is what failed a maintenance verify once (a 0s
# cloudflare-ddns CronJob pod). Exclude Completed/Succeeded, then re-check after
# a grace window so only pods STILL unhealthy afterwards are flagged.
# `set -o pipefail` is active: a kubectl failure surfaces as a non-zero return
# that the `|| {...}` guard turns into a counted error instead of aborting verify.
# One awk catches both non-running ($4 != Running) and Running-but-not-ready
# (READY a/b with a != b).
list_unhealthy() {
  kubectl get pods -A --no-headers 2>/dev/null | awk '
    $4 == "Completed" || $4 == "Succeeded" { next }
    { split($3, a, "/"); if ($4 != "Running" || a[1] != a[2]) print }'
}
BAD=$(list_unhealthy) || {
  echo "ERROR: failed to query pods"
  ERRORS=$((ERRORS + 1))
  BAD=""
}
if [ -n "$BAD" ]; then
  # Grace: let transient startup / CronJob pods settle, then re-check.
  sleep 25
  # Fail safe: if the re-query itself fails (transient API outage), keep the
  # pre-grace unhealthy snapshot and count an error rather than clearing BAD,
  # so a query failure can't mask real problems as "All pods healthy".
  if RECHECK=$(list_unhealthy); then
    BAD="$RECHECK"
  else
    echo "ERROR: failed to re-query pods after grace (keeping pre-grace result)"
    ERRORS=$((ERRORS + 1))
  fi
fi
if [ -n "$BAD" ]; then
  echo "ERROR: pod(s) still unhealthy after grace:"
  echo "$BAD"
  ERRORS=$((ERRORS + 1))
else
  echo "All pods healthy"
fi

echo ""
echo "Checking critical deployments..."
for dep in traefik:traefik coredns:kube-system cert-manager:cert-manager metallb-controller:metallb-system authentik-server:authentik; do
  name="${dep%%:*}"
  ns="${dep##*:}"
  # Wrap kubectl in `if` so a single deployment lookup failure (RBAC, API
  # blip, missing namespace) doesn't abort the loop under set -e.
  if DEP_REPLICAS=$(kubectl get deployment "$name" -n "$ns" -o jsonpath='{.status.availableReplicas} {.spec.replicas}' 2>/dev/null); then
    AVAIL="${DEP_REPLICAS%% *}"
    DESIRED="${DEP_REPLICAS##* }"
    if [ "${AVAIL:-0}" -ge "${DESIRED:-1}" ] 2>/dev/null; then
      echo "  $name ($ns): ${AVAIL:-0}/${DESIRED:-1} available"
    else
      echo "  ERROR: $name ($ns): ${AVAIL:-0}/${DESIRED:-?} available"
      ERRORS=$((ERRORS + 1))
    fi
  else
    echo "  ERROR: $name ($ns): deployment lookup failed"
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
# Retry: the probe schedules a one-off pod, which can fail to start within the
# timeout on a node mid-restart during maintenance — a transient that does NOT
# mean DNS is broken. Pass on the first DNS_PASS; only fail after all attempts.
dns_ok=false
dns_saw_fail=false
dns_last_output=""
for dns_attempt in 1 2 3 4 5; do
  DNS_OUTPUT=$(kubectl run "dns-verify-${CI_JOB_ID:-$$}-${dns_attempt}" --attach --rm --restart=Never --timeout=40s \
      --image=busybox:1.37 --command -- sh -c \
      "nslookup kubernetes.default.svc.cluster.local > /dev/null 2>&1 && echo DNS_PASS || echo DNS_FAIL" 2>&1 || true)
  dns_last_output="$DNS_OUTPUT"
  if echo "$DNS_OUTPUT" | grep -q "DNS_PASS"; then
    dns_ok=true
    break
  fi
  # Record whether the probe actually ran and resolution failed (DNS_FAIL marker)
  # vs. the probe never producing a verdict (scheduling/API/image/attach failure),
  # so the error below can attribute the failure honestly instead of always
  # blaming DNS.
  if echo "$DNS_OUTPUT" | grep -q "DNS_FAIL"; then
    dns_saw_fail=true
  fi
  sleep 10
done
if [ "$dns_ok" = true ]; then
  echo "Cluster DNS: OK (kubernetes.default.svc.cluster.local resolves)"
elif [ "$dns_saw_fail" = true ]; then
  echo "ERROR: Cluster DNS cannot resolve kubernetes.default.svc.cluster.local"
  echo "$dns_last_output"
  ERRORS=$((ERRORS + 1))
else
  echo "ERROR: cluster DNS probe could not run after retries (pod scheduling/API/image issue — not necessarily DNS itself):"
  echo "$dns_last_output"
  ERRORS=$((ERRORS + 1))
fi

echo ""
if [ "$ERRORS" -gt 0 ]; then
  echo "=== VERIFICATION FAILED: $ERRORS critical issue(s) ==="
  exit 1
fi
echo "=== Post-Maintenance Verification Passed ==="
