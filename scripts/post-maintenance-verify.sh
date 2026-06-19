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
# Spawn a one-off busybox pod that resolves an in-cluster name (a fresh pod always
# gets ClusterFirst DNS, so this exercises CoreDNS regardless of the runner pod's
# own dnsPolicy). IMPORTANT: do NOT use `kubectl run --attach --rm` — attach races
# a fast-completing pod and loses its stdout, so DNS_PASS/DNS_FAIL was never
# captured and verify failed every run even though DNS was healthy. Instead create
# the pod, poll for it to finish, then read its output with `kubectl logs` (always
# reliable), and delete it. Retry guards a genuinely transient CoreDNS/scheduling
# blip right after maintenance.
# Every kubectl call is bounded with --request-timeout so API/network degradation
# can't hang verify (the old --attach flow had a 40s timeout we must not lose).
kctl_timeout="--request-timeout=15s"
dns_ok=false
dns_saw_fail=false
dns_last_output=""
for dns_attempt in 1 2 3 4 5; do
  dns_pod="dns-verify-${CI_JOB_ID:-$$}-${dns_attempt}"
  # Clear any leftover pod of the same name (e.g. from an interrupted run) so we
  # cannot read a stale pod's logs and return a wrong verdict.
  kubectl delete pod "$dns_pod" "$kctl_timeout" --ignore-not-found --wait=true >/dev/null 2>&1 || true
  # Don't swallow a creation failure: without the pod, a later `kubectl logs` could
  # read a same-named stale pod. On failure, record it and move to the next attempt.
  if ! kubectl run "$dns_pod" "$kctl_timeout" --restart=Never --image=busybox:1.37 --command -- \
      sh -c "nslookup kubernetes.default.svc.cluster.local >/dev/null 2>&1 && echo DNS_PASS || echo DNS_FAIL" \
      >/dev/null 2>&1; then
    dns_last_output="failed to create DNS probe pod $dns_pod"
    # A client-side timeout can report failure even though the pod was created on
    # the API server — best-effort delete so we don't leak an orphan probe pod.
    kubectl delete pod "$dns_pod" "$kctl_timeout" --ignore-not-found --wait=false >/dev/null 2>&1 || true
    sleep 5
    continue
  fi
  # Poll for a terminal phase (the probe always exits 0 -> Succeeded); ~40s budget
  # covers image pull. Avoids `kubectl wait --for=jsonpath` version dependencies.
  dns_phase=""
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    dns_phase=$(kubectl get pod "$dns_pod" "$kctl_timeout" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    case "$dns_phase" in Succeeded | Failed) break ;; esac
    sleep 2
  done
  if [ "$dns_phase" != "Succeeded" ] && [ "$dns_phase" != "Failed" ]; then
    # Never finished -> scheduling/runtime delay, not a DNS verdict. Don't read
    # logs (would be empty and masquerade as a result); clean up and retry.
    dns_last_output="DNS probe pod did not finish (last phase: ${dns_phase:-unknown})"
    kubectl delete pod "$dns_pod" "$kctl_timeout" --ignore-not-found --wait=false >/dev/null 2>&1 || true
    sleep 5
    continue
  fi
  # Read the verdict; retry briefly since log publication can lag pod completion.
  DNS_OUTPUT=""
  for _ in 1 2 3 4 5; do
    DNS_OUTPUT=$(kubectl logs "$dns_pod" "$kctl_timeout" 2>&1 || true)
    if echo "$DNS_OUTPUT" | grep -qE "DNS_PASS|DNS_FAIL"; then break; fi
    sleep 1
  done
  kubectl delete pod "$dns_pod" "$kctl_timeout" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  # Distinguish three outcomes so the error below attributes the failure honestly
  # instead of always blaming DNS:
  #   DNS_PASS         -> resolution succeeded
  #   DNS_FAIL         -> a real resolution failure
  #   neither (no mark)-> the pod finished but logs never yielded a verdict
  #                       (scheduling/API/log-publication issue), recorded clearly.
  if echo "$DNS_OUTPUT" | grep -q "DNS_PASS"; then
    dns_ok=true
    dns_last_output="$DNS_OUTPUT"
    break
  elif echo "$DNS_OUTPUT" | grep -q "DNS_FAIL"; then
    dns_saw_fail=true
    dns_last_output="$DNS_OUTPUT"
  else
    dns_last_output="DNS probe produced no verdict (phase: ${dns_phase:-unknown}; logs: ${DNS_OUTPUT:-<empty>})"
  fi
  sleep 5
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
