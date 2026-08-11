#!/usr/bin/env bash
# Cluster health check, runs after each maintenance op (and after the
# `maintenance-run-all` wrapper) to validate that nothing got knocked over.
#
# Used by the maintenance-* CI jobs in .gitlab-ci.yml AND by the standalone
# manual `maintenance-verify` job. Its only project-internal dependencies are
# the sibling libs: maintenance-lib.sh (pure parsers) and deploy-verify-lib.sh
# (the shared GitLab probe only — the jq helpers there are never called here).
# Everything else needs kubectl + curl, so it runs from any CI image.
#
# Exits 0 if cluster is healthy, 1 if any critical check fails.

set -euo pipefail

# Resolve to an absolute dir so sourcing works regardless of CWD / PATH
# invocation (matches maintenance-ha-restart.sh).
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/maintenance-lib.sh
. "$_SCRIPT_DIR/maintenance-lib.sh"
# gitlab_health_code — shared with deploy-verify.sh so the two probes cannot drift.
# shellcheck source=scripts/deploy-verify-lib.sh
. "$_SCRIPT_DIR/deploy-verify-lib.sh"

echo "=== Post-Maintenance Verification ==="
ERRORS=0

# Nodes kured is actively rebooting (cordon -> drain -> reboot -> uncordon).
# Their NotReady state and evicted pods are an expected transient, so the checks
# below WARN for them and ERROR for everything else — node-scoped, so an
# unrelated failure elsewhere still fails. Re-read per check: kured reboots
# serially over minutes and a single early snapshot goes stale.
# REQUIRES configuration.annotateNodes:true in kured/release.yaml (the source of
# weave.works/kured-reboot-in-progress); without it every kured reboot reads as
# a hard failure here.
kured_rebooting_nodes() {
  # Thin kubectl wrapper: the annotated-AND-cordoned filter itself lives in
  # maintenance-lib.sh (kured_rebooting_filter) so it is unit-testable.
  kubectl get nodes \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.weave\.works/kured-reboot-in-progress}{"\t"}{.spec.unschedulable}{"\n"}{end}' \
    2>/dev/null | kured_rebooting_filter || true
}

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
  # A node is not-ready if STATUS ($2) does not begin with "Ready" (so
  # "Ready,SchedulingDisabled" — cordoned-but-healthy — does NOT count). Classify
  # each not-ready node by EXACT name via classify_not_ready_nodes
  # (maintenance-lib.sh, unit-tested): one kured is actively rebooting is an
  # expected transient (WARN, surfaced); anything else is a real ERROR.
  KURED_NOW=$(kured_rebooting_nodes)
  NOT_READY_NAMES=$(echo "$NODE_OUTPUT" | not_ready_node_names)
  NODE_VERDICTS=$(printf '%s\n' "$NOT_READY_NAMES" | classify_not_ready_nodes "$KURED_NOW")
  # If anything is not-ready-and-unexcused, grace + re-read once: a node that JUST
  # finished a kured reboot can be briefly NotReady with its annotation already
  # cleared (so neither the live KURED_NOW nor the grace alone would excuse it).
  # Match on the captured verdicts, not `... | grep -q` — under pipefail grep -q's
  # early exit can SIGPIPE the upstream and flip the pipeline status.
  case "$NODE_VERDICTS" in
    *error\ *)
      sleep 20
      # Only re-classify if the FRESH node query succeeds. Otherwise keep the
      # pre-grace snapshot + KURED_NOW: mixing a stale NODE_OUTPUT with a
      # fresh-and-empty KURED_NOW could wrongly flip a still-rebooting node to ERROR
      # on a transient API blip.
      if FRESH_NODES=$(kubectl get nodes --no-headers 2>/dev/null); then
        NODE_OUTPUT="$FRESH_NODES"
        KURED_NOW=$(kured_rebooting_nodes)
        NOT_READY_NAMES=$(echo "$NODE_OUTPUT" | not_ready_node_names)
        NODE_VERDICTS=$(printf '%s\n' "$NOT_READY_NAMES" | classify_not_ready_nodes "$KURED_NOW")
      fi
      ;;
  esac
  node_errors=0
  while IFS= read -r verdict_line; do
    [ -n "$verdict_line" ] || continue
    n="${verdict_line#* }"
    if [ "${verdict_line%% *}" = "excused" ]; then
      echo "WARNING: node $n NotReady (kured rebooting; verified next run)"
    else
      echo "ERROR: node $n not Ready"
      node_errors=$((node_errors + 1))
    fi
  done <<< "$NODE_VERDICTS"
  # Stuck-cordon detection: a node Ready,SchedulingDisabled but NOT currently
  # kured-rebooting may be a kured uncordon failure (kubereboot/kured #955) or a
  # left-over op-3 cordon. WARN (not ERROR — it can also be an intentional cordon).
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    printf '%s\n' "$KURED_NOW" | grep -qxF "$n" || \
      echo "WARNING: node $n cordoned (Ready,SchedulingDisabled) with no active kured reboot — check for a stuck uncordon"
  done <<< "$(echo "$NODE_OUTPUT" | awk '$2 == "Ready,SchedulingDisabled" {print $1}')"
  if [ "$node_errors" -gt 0 ]; then
    ERRORS=$((ERRORS + node_errors))
  else
    echo "All nodes Ready${KURED_NOW:+ (kured-rebooting node(s) excused)}"
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
  kubectl get pods -A --no-headers 2>/dev/null | list_unhealthy_pods
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
  KURED_NOW=$(kured_rebooting_nodes)
  # NODE-SCOPED kured excuse. Map pod -> node via jsonpath (NOT `-o wide`, whose
  # RESTARTS "5 (3m ago)" suffix shifts columns). An unhealthy pod is excused only
  # while kured is mid-reboot AND the pod is on a rebooting node or unscheduled;
  # anything else ERRORs, CrashLoop included.
  # Accepted limitation: an unschedulable pod unrelated to kured also reads as
  # node-less and so WARNs during a reboot window — see docs/12-runbooks.md
  # § Post-maintenance verification.
  pn_ok=true
  if ! POD_NODES=$(kubectl get pods -A \
      -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}' \
      2>/dev/null); then
    pn_ok=false
    POD_NODES=""
  fi
  pod_errors=0
  pod_warn=""
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    pkey=$(echo "$line" | awk '{print $1"/"$2}')
    pstatus=$(echo "$line" | awk '{print $4}')
    if [ -z "$KURED_NOW" ]; then
      echo "ERROR: pod $pkey unhealthy (status $pstatus)"
      pod_errors=$((pod_errors + 1))
      continue
    fi
    if [ "$pn_ok" = false ]; then
      # kured active but the pod->node lookup failed: can't node-scope. WARN
      # (undetermined) like the deployment check — don't mask, don't false-fail on
      # an API blip during reboot churn.
      pod_warn="${pod_warn}  $pkey ($pstatus, node lookup inconclusive)"$'\n'
      continue
    fi
    pnode=$(printf '%s\n' "$POD_NODES" | awk -F'\t' -v p="$pkey" '$1 == p {print $2}')
    if [ -z "$pnode" ] || printf '%s\n' "$KURED_NOW" | grep -qxF "$pnode"; then
      pod_warn="${pod_warn}  $pkey ($pstatus, node ${pnode:-<unscheduled>})"$'\n'
    else
      echo "ERROR: pod $pkey unhealthy on a healthy node (status $pstatus, node $pnode)"
      pod_errors=$((pod_errors + 1))
    fi
  done <<< "$BAD"
  if [ -n "$pod_warn" ]; then
    echo "WARNING: pod(s) excused while kured is rebooting node(s) - not failing (verified next run):"
    printf '%s' "$pod_warn"
  fi
  ERRORS=$((ERRORS + pod_errors))
else
  echo "All pods healthy"
fi

echo ""
echo "Checking critical deployments..."
# One kured snapshot for the whole (fast, 5-deployment) loop is intentional —
# unlike the node/pod checks that re-read per check because they span the 25s grace
# and serial node churn, this loop completes in well under a kured reboot cycle.
KURED_NOW=$(kured_rebooting_nodes)
for dep in traefik:traefik coredns:kube-system cert-manager:cert-manager metallb-controller:metallb-system authentik-server:authentik; do
  name="${dep%%:*}"
  ns="${dep##*:}"
  # Wrap kubectl in `if` so a single deployment lookup failure (RBAC, API
  # blip, missing namespace) doesn't abort the loop under set -e.
  if DEP_REPLICAS=$(kubectl get deployment "$name" -n "$ns" -o jsonpath='{.status.availableReplicas} {.spec.replicas}' 2>/dev/null); then
    AVAIL="${DEP_REPLICAS%% *}"
    DESIRED="${DEP_REPLICAS##* }"
    if deployment_replicas_ok "$AVAIL" "$DESIRED"; then
      echo "  $name ($ns): ${AVAIL:-0}/${DESIRED:-1} available"
      continue
    fi
    # Under-replicated. NODE-SCOPED kured excuse: excuse only when one of THIS
    # deployment's pods sits on a rebooting node (or is unscheduled); an API blip
    # in the lookup is 'undetermined -> WARN', never a silent mis-ERROR. jsonpath
    # name<TAB>node (NOT `-o wide` $7, whose RESTARTS "(3m ago)" suffix shifts
    # columns); the pod name is anchored to the no-vowel pod-template-hash charset
    # so a sibling deployment (cert-manager-webhook, coredns-autoscaler) is not
    # matched. Accepted limitation on multi-replica deployments — see
    # docs/12-runbooks.md § Post-maintenance verification.
    dep_excuse=no
    if [ -n "$KURED_NOW" ]; then
      if DEP_PODNODES=$(kubectl get pods -n "$ns" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}' 2>/dev/null); then
        DEP_NODES=$(echo "$DEP_PODNODES" | deployment_pod_nodes "$name")
        # Excuse if a replica is on a kured-rebooting node OR is unscheduled
        # (evicted by a kured drain and not yet rescheduled — same transient the
        # pod check excuses, kept consistent here).
        if printf '%s\n' "$DEP_NODES" | grep -qxF '<unscheduled>' \
           || printf '%s\n' "$DEP_NODES" | grep -qxFf <(printf '%s\n' "$KURED_NOW"); then
          dep_excuse=yes
        fi
      else
        dep_excuse=undetermined
      fi
    fi
    if [ "$dep_excuse" = no ]; then
      # Grace (matching the node/pod checks): a deployment can be briefly
      # under-replicated (a rolling pod, a just-rescheduled replica). Re-query once
      # after a short sleep before failing.
      sleep 10
      if DEP2=$(kubectl get deployment "$name" -n "$ns" -o jsonpath='{.status.availableReplicas} {.spec.replicas}' 2>/dev/null) \
         && deployment_replicas_ok "${DEP2%% *}" "${DEP2##* }"; then
        echo "  $name ($ns): ${DEP2%% *}/${DEP2##* } available (recovered after grace)"
      else
        echo "  ERROR: $name ($ns): ${AVAIL:-0}/${DESIRED:-?} available"
        ERRORS=$((ERRORS + 1))
      fi
    else
      sfx=""
      [ "$dep_excuse" = undetermined ] && sfx=" (pod-node lookup inconclusive)"
      echo "  WARNING: $name ($ns): ${AVAIL:-0}/${DESIRED:-?} available (kured rebooting; verified next run)$sfx"
    fi
  else
    echo "  ERROR: $name ($ns): deployment lookup failed"
    ERRORS=$((ERRORS + 1))
  fi
done

echo ""
echo "Checking for failed Jobs..."
# list_unhealthy_pods skips terminal Error/Failed pods, so a genuinely-failed
# one-shot Job is checked here directly: Failed=True without Complete=True, on a
# non-CronJob Job. The kured excuse is node-scoped like the pod/deployment checks
# (a backoffLimit:0 Job evicted by a drain fails through no fault of its own);
# the pods-already-TTL-cleaned case is ambiguous and WARNs, backstopped by the
# KubeJobFailed Prometheus alert.
KURED_NOW=$(kured_rebooting_nodes)
# Split the kubectl query from the awk filter so a query FAILURE (API/RBAC/token)
# is counted as an ERROR, not silently read as "no failed Jobs". A plain
# VAR=$(...) assignment does not trip `set -e`, and the old `... | awk ... || true`
# masked a failed `kubectl get jobs` as empty output -> false green. Mirrors the
# node/pod/deployment checks, which all count an ERROR on query failure.
jobs_query_failed=false
if JOBS_RAW=$(kubectl get jobs -A --request-timeout=15s \
  -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\t"}{.metadata.ownerReferences[0].kind}{"\t"}{range .status.conditions[*]}{.type}={.status},{end}{"\n"}{end}' \
  2>/dev/null); then
  FAILED_JOBS=$(printf '%s\n' "$JOBS_RAW" | awk -F'\t' '$2 != "CronJob" && $3 ~ /Failed=True/ && $3 !~ /Complete=True/ {print $1}')
else
  echo "ERROR: failed to query Jobs (kubectl get jobs -A failed) - cannot verify failed Jobs this run"
  ERRORS=$((ERRORS + 1))
  FAILED_JOBS=""
  jobs_query_failed=true
fi
job_errors=0
job_warn=""
while IFS= read -r jk; do
  [ -n "$jk" ] || continue
  jns="${jk%%/*}"
  jname="${jk##*/}"
  if [ -z "$KURED_NOW" ]; then
    echo "ERROR: failed Job $jk (terminal Failed condition)"
    job_errors=$((job_errors + 1))
    continue
  fi
  # Distinguish a query FAILURE from an empty result: if this pod lookup itself
  # errors (API/RBAC), we cannot node-scope the excuse, so treat the terminal Job
  # as a real ERROR rather than the benign "pods gone" WARN below (which would
  # masquerade a failed check as an excused transient).
  if ! JOB_NODES=$(kubectl get pods -n "$jns" -l batch.kubernetes.io/job-name="$jname" --request-timeout=15s \
    -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' 2>/dev/null); then
    echo "ERROR: failed Job $jk (could not query its pods to confirm a kured excuse)"
    job_errors=$((job_errors + 1))
    continue
  fi
  if [ -z "$JOB_NODES" ]; then
    # No pod left to attribute a node to, so this WARNs while kured is active.
    job_warn="${job_warn} $jk(pods gone; kured active)"
  # Capture-and-test, NOT `! ... | grep -q`: under pipefail grep -q's early pipe
  # close SIGPIPEs the upstream, so the pipeline is non-zero even on a match and
  # `!` would wrongly excuse a real failure. (SC2143 suggests exactly that bug.)
  # The captured list is empty iff every job pod is unscheduled or on a
  # kured-rebooting node.
  elif [ -z "$(printf '%s\n' "$JOB_NODES" | grep -v '^$' | grep -vxFf <(printf '%s\n' "$KURED_NOW"))" ]; then
    # Excuse ONLY if EVERY pod of the Job is unscheduled (blank) or on a
    # kured-rebooting node. If even one attempt pod failed on a HEALTHY node, that
    # is a real failure — don't let one evicted attempt mask it.
    job_warn="${job_warn} $jk(all pods unscheduled or on a kured-rebooting node)"
  else
    echo "ERROR: failed Job $jk (a pod failed on a healthy node, not a kured transient)"
    job_errors=$((job_errors + 1))
  fi
done <<< "$FAILED_JOBS"
if [ -n "$job_warn" ]; then
  echo "WARNING: failed Job(s) excused while kured is rebooting - not failing (verified next run):$job_warn"
fi
if [ "$job_errors" -eq 0 ] && [ -z "$job_warn" ] && [ "$jobs_query_failed" = false ]; then
  echo "No failed Jobs"
fi
ERRORS=$((ERRORS + job_errors))

echo ""
echo "Checking GitLab health..."
# gitlab_health_code (deploy-verify-lib.sh) owns the internal-first/external-
# fallback probe. Only the retry budget is local: GitLab's disk is on an
# encryption-gated zvol, so a run that reboots pve-nas-01 leaves it restarting
# and answering 404 on /-/readiness for minutes. 4 min keeps a slow-but-healthy
# start from failing the verify (deploy-verify's 60s budget sees no such reboot).
GITLAB_CODE=""
gitlab_start_ts=$(date +%s)
while true; do
  GITLAB_CODE=$(gitlab_health_code /-/readiness)
  if [ "$GITLAB_CODE" = "200" ]; then break; fi
  gitlab_elapsed=$(( $(date +%s) - gitlab_start_ts ))
  if [ "$gitlab_elapsed" -ge 240 ]; then break; fi
  echo "GitLab not ready (HTTP ${GITLAB_CODE:-000}, ${gitlab_elapsed}s elapsed), retrying..."
  sleep 10
done
if [ "$GITLAB_CODE" = "200" ]; then
  echo "GitLab: OK"
else
  echo "ERROR: GitLab health check failed (HTTP ${GITLAB_CODE:-000}) after 4m of retries"
  ERRORS=$((ERRORS + 1))
fi

echo ""
echo "Checking cluster DNS (internal service resolution)..."
# One-off busybox pod resolving an in-cluster name; a fresh pod always gets
# ClusterFirst DNS, so this exercises CoreDNS regardless of the runner's own
# dnsPolicy. Do NOT use `kubectl run --attach --rm`: attach races a
# fast-completing pod and loses its stdout. Create, poll for a terminal phase,
# read with `kubectl logs`, delete. Every kubectl call carries
# --request-timeout so API degradation cannot hang the verify.
kctl_timeout="--request-timeout=15s"
dns_ok=false
dns_saw_fail=false
dns_last_output=""
for dns_attempt in 1 2 3 4 5; do
  dns_pod="dns-verify-${CI_JOB_ID:-$$}-${dns_attempt}"
  # Clear any leftover pod of the same name (e.g. from an interrupted run) so we
  # cannot read a stale pod's logs and return a wrong verdict.
  kubectl delete pod "$dns_pod" "$kctl_timeout" --ignore-not-found --wait=true >/dev/null 2>&1 || true
  # A creation failure is recorded, not swallowed: without the pod a later
  # `kubectl logs` could read a same-named stale one.
  # busybox pin is gated against busybox_version in group_vars/all.yml by
  # `task lint:busybox-version-pin`; bump both together.
  if ! kubectl run "$dns_pod" "$kctl_timeout" --restart=Never --image=busybox:1.38 --command -- \
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
