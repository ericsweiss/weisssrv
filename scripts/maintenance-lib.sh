#!/usr/bin/env bash
# Pure-logic helpers shared by the maintenance scripts, extracted so the
# parsing/state-machine logic that runs in production maintenance CI can be
# unit-tested without a live cluster.
#
# This file defines functions only (no top-level side effects) so it is safe
# to `source` from both the prod scripts and the pytest harness
# (scripts/test_maintenance_lib.py). Each function reads its input from stdin
# or positional args and writes a verdict to stdout / returns an exit status —
# none of them call kubectl/curl/ansible themselves.

# --- post-maintenance-verify.sh parsers -----------------------------------

# count_not_ready_nodes: read `kubectl get nodes --no-headers` on stdin, print
# the number of nodes whose STATUS ($2) does not begin with "Ready". Accepts
# both "Ready" and "Ready,SchedulingDisabled" (cordoned-but-healthy) as ready.
count_not_ready_nodes() {
  awk '$2 !~ /^Ready/ {count++} END {print count+0}'
}

# list_unhealthy_pods: read `kubectl get pods -A --no-headers` on stdin, print
# the rows for pods that are genuinely unhealthy. Completed/Succeeded batch
# pods are skipped; a pod is unhealthy if its STATUS ($4) is not "Running" OR
# its READY column ($3, "a/b") has a != b.
list_unhealthy_pods() {
  awk '
    $4 == "Completed" || $4 == "Succeeded" { next }
    { split($3, a, "/"); if ($4 != "Running" || a[1] != a[2]) print }'
}

# deployment_replicas_ok <available> <desired>: exit 0 if available >= desired,
# defaulting available to 0 and desired to 1 when blank (mirrors the verify
# script's `[ "${AVAIL:-0}" -ge "${DESIRED:-1}" ]` guard, which also tolerates
# non-numeric input by returning non-zero).
deployment_replicas_ok() {
  local avail="${1:-0}" desired="${2:-1}"
  [ "${avail:-0}" -ge "${desired:-1}" ] 2>/dev/null
}

# --- maintenance-ha-restart.sh parsers -------------------------------------

# ha_reset_verdict: read the captured `ansible ... -m shell` output on stdin and
# print one of: err / ok / notfound. Checks err BEFORE ok and anchors both
# markers to line start (column 0, after Ansible's `>>`) so:
#   - a split-brain that emitted both tokens fails closed (err wins),
#   - an echoed command source (e.g. a failed task dumping the cmd) cannot
#     match, since the real tokens are runtime-assembled and only ever printed
#     at column 0.
ha_reset_verdict() {
  local out
  out=$(cat)
  if printf '%s\n' "$out" | grep -qE "^vmreset:err host="; then
    echo err
  elif printf '%s\n' "$out" | grep -qE "^vmreset:ok host="; then
    echo ok
  else
    echo notfound
  fi
}

# ha_observe_step <prev_streak> <prev_went_down> <probe_result>: advance the
# down-then-up debounce state machine by one probe.
#   probe_result: "up" (curl succeeded) or "down" (curl failed)
# Prints "<new_streak> <new_went_down> <verdict>" where verdict is one of:
#   healthy  -> endpoint is up AND we previously observed it go down
#   waiting  -> not yet a confirmed down-then-up transition
# went_down latches true once the down streak reaches 2 (debounce against a
# single transient curl error).
ha_observe_step() {
  local streak="$1" went_down="$2" probe="$3"
  local verdict="waiting"
  if [ "$probe" = "up" ]; then
    streak=0
    if [ "$went_down" = "true" ]; then
      verdict="healthy"
    fi
  else
    streak=$((streak + 1))
    if [ "$streak" -ge 2 ]; then
      went_down="true"
    fi
  fi
  echo "$streak $went_down $verdict"
}

# ha_settle_verdict <went_down> <down_streak> <elapsed> <settle_secs>: decide
# whether the fast-reset settle window has closed out with NO observed downtime.
# A `qm reset` can complete before two consecutive probes fail, so the endpoint
# may stay healthy throughout and ha_observe_step never reports "healthy".
# Prints one of:
#   healthy-unverified -> never observed down AND the settle window has elapsed
#                         (the reset's effect was NOT verified — we never saw
#                         the VM go down, only that it is up now)
#   keep-waiting       -> any downtime observed (went_down=true OR streak>0), or
#                         the settle window has not yet elapsed
# Fails closed: if ANY downtime was observed this path does NOT short-circuit —
# the caller's ha_observe_step debounce owns the confirmed down-then-up verdict.
ha_settle_verdict() {
  local went_down="$1" streak="$2" elapsed="$3" settle="$4"
  if [ "$went_down" != true ] && [ "$streak" -eq 0 ] && [ "$elapsed" -ge "$settle" ]; then
    echo "healthy-unverified"
  else
    echo "keep-waiting"
  fi
}
