#!/usr/bin/env bash
# Cluster health check, runs after each maintenance op (and after the
# `maintenance-run-all` wrapper) to validate that nothing got knocked over.
#
# Used by the maintenance-* CI jobs in .gitlab-ci.yml AND by the standalone
# manual `maintenance-verify` job. The only project-internal dependency is the
# sibling scripts/maintenance-lib.sh (always cloned alongside this script in
# CI), which holds the pure parsing helpers so they can be unit-tested without
# a live cluster. Everything else needs only kubectl + curl, so the same
# script works from any CI image.
#
# Exits 0 if cluster is healthy, 1 if any critical check fails.

set -euo pipefail

# Resolve to an absolute dir so sourcing works regardless of CWD / PATH
# invocation (matches maintenance-ha-restart.sh).
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/maintenance-lib.sh
. "$_SCRIPT_DIR/maintenance-lib.sh"

echo "=== Post-Maintenance Verification ==="
ERRORS=0

# kured (Kubernetes Reboot Daemon) reboots flagged k3s nodes one at a time,
# during AND after a maintenance run (cordon -> drain -> reboot -> uncordon). A
# node it is mid-reboot on is briefly NotReady and its pods are transiently
# evicted/rescheduling (incl. NAS-pinned single replicas on RWO storage) — a
# controlled, expected transient, NOT a maintenance regression. kured annotates
# such nodes; the checks below treat THAT node's NotReady / unavailable pods as
# expected (warn, verified next run) instead of failing — but still SURFACE them
# so a node stuck-after-reboot isn't silently dropped, and scoped to the actual
# node so an unrelated failure on a healthy node still ERRORs. Read fresh in each
# check: kured reboots serially over minutes, so a single early snapshot goes
# stale (a node may start/finish rebooting partway through verify).
# DEPENDS on configuration.annotateNodes:true in kured/release.yaml — that is what
# emits weave.works/kured-reboot-in-progress. If it is ever turned off this returns
# empty and every kured reboot looks like a hard failure here (loud, not silent).
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
  # RESTARTS "5 (3m ago)" suffix shifts columns). While kured is mid-reboot, excuse
  # an unhealthy pod ONLY if it is on a kured-rebooting node OR unscheduled (no node
  # = evicted from one, not yet rescheduled). Any unhealthy pod on a HEALTHY node —
  # or any unhealthy pod when kured is idle — ERRORs. No status-class shortcut: a
  # CrashLoop on a healthy node ERRORs (node-scoped), one on a kured-rebooting node
  # is excused and re-verified next run; bare Error/Failed is already dropped
  # upstream in maintenance-lib.sh.
  #
  # RESIDUAL (unscheduled false-green, accepted): a node-less Pending pod is excused
  # while kured is active, so a genuinely-unschedulable pod (resource pressure, bad
  # nodeSelector) UNRELATED to kured WARNs instead of ERRORs in that window. Not
  # precisely fixable from snapshots — a kured-drain-evicted pod is ALSO briefly
  # node-less and can transiently read PodScheduled=False, indistinguishable from a
  # stuck pod at one instant; only persistence-over-minutes separates them, which the
  # next run re-checks once kured is idle (the excuse only holds mid-reboot).
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
    # Under-replicated. NODE-SCOPED kured excuse: is one of THIS deployment's pods
    # on a node kured is actively rebooting (a single replica pinned there shows
    # availableReplicas=0 transiently)? An unrelated down deployment on a healthy
    # node still ERRORs even while kured reboots elsewhere. Capture the pod-node
    # lookup so an API blip is 'undetermined -> WARN', not a silent mis-ERROR.
    # jsonpath name<TAB>node (NOT `-o wide` $7, whose RESTARTS "(3m ago)" suffix
    # shifts columns); pod name anchored to the no-vowel pod-template-hash charset
    # so a vowel-bearing sibling (cert-manager-webhook for cert-manager,
    # coredns-autoscaler for coredns) is not matched.
    #
    # RESIDUAL (multi-replica masking, accepted): traefik/coredns/authentik-server
    # run >=2 replicas. If one replica is genuinely missing on a HEALTHY node while
    # a SEPARATE replica sits on a kured-rebooting node, this (available<desired +
    # any-pod-on-kured-node) excuse WARNs and masks the real shortfall for that run.
    # A precise per-replica fix is infeasible from these snapshots: a missing replica
    # has no pod object to inspect, and a pod on a kured node may still be Ready
    # (counted in availableReplicas until evicted), so (desired-available) vs
    # (#pods-on-kured-nodes) is unreliable and would false-FAIL real kured transients.
    # Bounded + backstopped: the per-POD pod check above still ERRORs a
    # CrashLoop/Pending replica on a healthy node regardless of a kured sibling; the
    # excuse only holds while kured is mid-reboot (serial, minutes) so the next run
    # re-verifies; and KubeDeploymentReplicasMismatch fires independently in Prometheus.
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
# list_unhealthy_pods skips terminal Error/Failed pods (mostly flaky CronJob retry
# pods — see maintenance-lib.sh), which could hide a genuinely-failed one-shot Job
# (e.g. a DB migration/bootstrap Job that exhausted its backoffLimit). Check Jobs
# directly: a Failed=True condition that did NOT also Complete, on a NON-CronJob
# Job. NODE-SCOPE the kured excuse like the pod/deployment checks: a one-shot Job
# with backoffLimit:0 whose pod is evicted by a kured drain can reach Failed
# through no fault of its own. Excuse (WARN) only if a pod of the Job is/was on a
# kured-rebooting node or unscheduled, OR its pods are already gone (TTL cleanup,
# can't node-scope) AND kured is active; otherwise ERROR — a real, terminal failure.
# The pods-gone-during-kured case is ambiguous (a real terminal failure looks the
# same as a reboot-evicted one once pods are TTL-cleaned), so it WARNs here — but
# the default KubeJobFailed Prometheus alert fires on ANY failed Job independently
# of this run, so a genuinely-broken Job is never lost to monitoring.
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
    # RESIDUAL (pods-gone false-green, accepted): a terminal-failed Job whose pods
    # were already TTL-cleaned/deleted has no node to attribute, so while kured is
    # active it WARNs rather than ERRORs — a real failure whose pods happened to be
    # cleaned in a kured window is indistinguishable from a kured eviction here. Not
    # precisely fixable (no pod = no node); backstopped by the next run's re-verify
    # and the independent KubeJobFailed Prometheus alert (fires on any failed Job).
    job_warn="${job_warn} $jk(pods gone; kured active)"
  # Capture-and-test, NOT `... | grep -q .`: under `set -o pipefail`, grep -q
  # closes the pipe on its first match, SIGPIPE-killing the upstream grep so the
  # pipeline returns non-zero EVEN on a match — `!` would then WRONGLY take the
  # excuse branch and mask a real failure on a healthy node. Capturing the filtered
  # list reads it fully (no early pipe close); it is empty iff every job pod is
  # unscheduled (blank) or on a kured-rebooting node. (Capture in a [ -z ] test is
  # set -e-safe — the inner pipeline's exit is not checked.) NB: SC2143 will
  # suggest `! grep -q` here — that is precisely the SIGPIPE bug being fixed, so
  # the capture form is intentional (SC2143 is style-level, below CI's --severity).
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
# Internal chain first (DNS -> Traefik VIP -> GitLab nginx), falling back to the
# external hostname ONLY on a connection-level failure ("000"/empty) — the same
# transient-ingress false-000 collect-state's probe_gitlab_http guards against
# (verify runs right after kured node reboots, peak ingress churn). A real HTTP
# status (incl 4xx/5xx) means GitLab answered, so trust it rather than let an
# external 200 mask an internal error.
# GitLab's VM disk is on an encryption-gated zvol, so a maintenance run that
# reboots pve-nas-01 leaves GitLab restarting: it can take minutes to finish
# and returns 404 on /-/readiness until Rails/Workhorse are up. Retry for up to
# ~4 min so a slow-but-healthy start is not a false failure (a genuinely-down
# GitLab still fails once the budget is spent). Mirrors deploy-verify.sh's retry;
# the internal->external fallback on a connection-level 000 is preserved.
GITLAB_CODE=""
gitlab_start_ts=$(date +%s)
while true; do
  GITLAB_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://git.esweiss.com/-/readiness 2>/dev/null || true)
  if [ -z "$GITLAB_CODE" ] || [ "$GITLAB_CODE" = "000" ]; then
    GITLAB_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://git.ericsweiss.com/-/readiness 2>/dev/null || true)
  fi
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
