#!/usr/bin/env bash
# Classification helpers for the verify scripts, extracted so the logic that
# gates deploy-verify can be unit-tested without a live cluster
# (scripts/test_deploy_verify_lib.py, same pattern as collect-state-lib.sh).
#
# Functions only — no top-level side effects, safe to `source` under `set -e`.
# Each reads its input from stdin (recorded `kubectl get ... -o json`, or a
# `kubectl get ... --no-headers` table) or positional args, and writes a verdict
# to stdout / returns an exit status. None call kubectl/flux themselves.
#
# Most helpers need jq, which the deploy-verify env provides (.k3s-deploy-base).
# post-maintenance-verify.sh sources this file ONLY for gitlab_health_code
# (curl-only) and keeps its own jq-free classifiers in maintenance-lib.sh, since
# it must run from any kubectl+curl CI image — see that script's header.

# jq fragment: select list items whose Ready condition is missing or not True.
# Shared by the count/name/dump helpers below (and referenced by deploy-verify.sh
# for the projections that print richer per-item detail).
JQ_NOT_READY='select((.status.conditions // []) | map(select(.type == "Ready")) | (length == 0 or .[0].status != "True"))'

# count_not_ready: read a `kubectl get <kind> -o json` list on stdin, print the
# number of items whose Ready condition is missing or not True. Emits 999
# (treat-as-not-ready) only when jq ERRORS on non-JSON garbage; on EMPTY input
# jq exits 0 with no output, so this prints nothing. Callers whose upstream
# kubectl can fail (empty pipe under pipefail) must therefore keep their own
# outer `|| echo 999` guard — the helper alone is not fail-closed.
count_not_ready() {
  jq "[.items[] | $JQ_NOT_READY] | length" 2>/dev/null || echo "999"
}

# not_ready_ns_names: read a list on stdin, print "  <namespace>/<name>" for each
# not-Ready item (used to enumerate non-Ready ExternalSecrets).
not_ready_ns_names() {
  jq -r ".items[] | $JQ_NOT_READY | \"  \(.metadata.namespace)/\(.metadata.name)\"" 2>/dev/null || true
}

# steady_state: given the pre-reconcile count of not-Ready Kustomizations, print
# "true" when it is exactly 0 (a steady-state push — non-Ready ExternalSecrets/
# pods are then failures) else "false" (bootstrap/recovery — they are tolerated).
# A blank/non-numeric count is treated as not-steady (bootstrap).
steady_state() {
  if [ "${1:-}" = "0" ]; then echo "true"; else echo "false"; fi
}

# nodes_not_ready_count: read `kubectl get nodes --no-headers` on stdin, print
# the number of nodes whose STATUS ($2) is not exactly "Ready".
nodes_not_ready_count() {
  awk '$2 != "Ready" {count++} END {print count+0}'
}

# pods_not_running_or_completed: read `kubectl get pods --no-headers` on stdin,
# print the rows whose STATUS ($3) is not Running or Completed (the "bad" set).
pods_not_running_or_completed() {
  awk '$3 !~ /^(Running|Completed)$/'
}

# pods_non_transient: read pod rows on stdin, print those whose STATUS ($3) is NOT
# on the transient allowlist — i.e. the genuinely-failing pods that fail a verify
# even during bootstrap/recovery. Feed it the pods_not_running_or_completed set.
pods_non_transient() {
  awk '$3 !~ /^(Pending|ContainerCreating|PodInitializing|Terminating|Init:[0-9]+\/[0-9]+)$/'
}

# pods_running_unready: read pod rows on stdin, print the Running pods whose READY
# column ($2, "a/b") has a != b (a failing readiness probe, container not ready).
pods_running_unready() {
  awk '$3=="Running"{split($2,a,"/"); if(a[1]!=a[2]) print}'
}

# helmreleases_not_ready_names: read `kubectl get helmreleases -o json` on stdin,
# print the .metadata.name of each HR whose Ready condition is missing or not True.
helmreleases_not_ready_names() {
  jq -r ".items[] | $JQ_NOT_READY | .metadata.name" 2>/dev/null || true
}

# helmreleases_hard_failed: read HR JSON on stdin, print the name of each HR that
# is a HARD failure even during bootstrap/recovery — Ready != True AND either the
# Ready reason is a terminal failure (InstallFailed/UpgradeFailed/TestFailed/
# RollbackFailed) OR .status.failures > 0 (the controller has retried at least
# once). Catches degraded HRs whose Ready reason isn't on the explicit allowlist.
helmreleases_hard_failed() {
  jq -r '
    .items[]
    | (.status.conditions // [] | map(select(.type=="Ready")) | .[0]) as $ready
    | select(
        $ready.status != "True"
        and (
          (($ready.reason // "") | test("InstallFailed|UpgradeFailed|TestFailed|RollbackFailed"))
          or ((.status.failures // 0) > 0)
        )
      )
    | .metadata.name' 2>/dev/null || true
}

# gitlab_health_code <path>: single GitLab health probe (curl only, no jq).
# Tries the internal chain (DNS -> Traefik VIP -> GitLab nginx) first and falls
# back to the external hostname ONLY on a connection-level failure ("000"/empty)
# — the internal Traefik->VM leg can stall past the timeout on a healthy GitLab,
# and both verify scripts run during peak ingress churn. A real HTTP status
# (incl. 4xx/5xx) means GitLab answered, so it is trusted as-is rather than let
# an external 200 mask an internal error. Echoes the status code; callers own
# their own retry budget. The two base URLs are env-overridable for tests.
gitlab_health_code() {
  local path="${1:-/-/health}" code
  local internal="${GITLAB_HEALTH_INTERNAL:-https://git.esweiss.com}"
  local external="${GITLAB_HEALTH_EXTERNAL:-https://git.ericsweiss.com}"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${internal}${path}" 2>/dev/null || true)
  if [ -z "$code" ] || [ "$code" = "000" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${external}${path}" 2>/dev/null || true)
  fi
  echo "$code"
}
