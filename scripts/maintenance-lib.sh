#!/usr/bin/env bash
# Pure-logic helpers shared by the maintenance scripts, so the parsing that runs
# in production maintenance CI is unit-testable without a live cluster
# (scripts/test_maintenance_lib.py).
#
# Functions only, no top-level side effects: each reads stdin or positional args
# and writes a verdict to stdout / returns a status. None call kubectl, curl or
# ansible themselves.

# not_ready_node_names: `kubectl get nodes --no-headers` on stdin -> the NAME of
# each node whose STATUS does not begin with "Ready", so "Ready,SchedulingDisabled"
# (cordoned but healthy) counts as ready and is NOT listed.
not_ready_node_names() {
  awk '$2 !~ /^Ready/ {print $1}'
}

# kured_rebooting_filter: `node<TAB>annotation<TAB>unschedulable` rows on stdin ->
# the nodes kured is ACTIVELY rebooting. A node qualifies only if BOTH annotated
# AND cordoned: kured writes the annotation before its block-check and never
# clears it when blocked, so annotated-but-schedulable means blocked, not
# mid-reboot. (Same logic as _wait-no-kured-server-reboot.yml.)
kured_rebooting_filter() {
  awk -F'\t' '$2 != "" && $3 == "true" {print $1}'
}

# classify_not_ready_nodes <kured_names>: not-ready node names on stdin -> one
# verdict line per node, "excused <name>" on an exact match in the
# newline-separated <kured_names>, "error <name>" otherwise.
classify_not_ready_nodes() {
  local kured_names="$1" n
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    if printf '%s\n' "$kured_names" | grep -qxF "$n"; then
      echo "excused $n"
    else
      echo "error $n"
    fi
  done
}

# list_unhealthy_pods: `kubectl get pods -A --no-headers` on stdin -> the rows
# for genuinely-unhealthy pods (STATUS not "Running", or READY "a/b" with a != b).
# Terminal batch outcomes (Completed/Succeeded/Error/Failed) are SKIPPED — they
# come from exited Job/CronJob pods — and the verify's failed-Job check plus the
# Prometheus alerts backstop what that drops.
list_unhealthy_pods() {
  awk '
    $4 == "Completed" || $4 == "Succeeded" || $4 == "Error" || $4 == "Failed" { next }
    # CI JOB pods (runner-<token>-project-N-...) are pipeline state, not
    # cluster health: Pending while a pipeline queues, Terminating while DinD
    # tears down — both outlive the 25s grace and false-criticaled the
    # 2026-08-20 runs twice, in both states. Excused by NAME PREFIX in the
    # runner namespaces only; the runner MANAGERS (gitlab-runner-*) stay
    # covered, and a pod stuck in any state elsewhere stays a signal (stale
    # NFS handles present exactly as stuck Terminating).
    ($1 == "gitlab-runner" || $1 == "gitlab-runner-privileged") && $2 ~ /^runner-/ { next }
    { split($3, a, "/"); if ($4 != "Running" || a[1] != a[2]) print }'
}

# deployment_replicas_ok <available> <desired>: exit 0 if available >= desired,
# defaulting blanks to 0 and 1; non-numeric input returns non-zero.
deployment_replicas_ok() {
  local avail="${1:-0}" desired="${2:-1}"
  [ "${avail:-0}" -ge "${desired:-1}" ] 2>/dev/null
}

# deployment_pod_nodes <deployment-name>: read `name<TAB>nodeName` lines on stdin
# (jsonpath output) and, for each pod belonging to <deployment>, print its
# nodeName — or the literal "<unscheduled>" when nodeName is empty (an evicted /
# not-yet-scheduled pod), so a caller can distinguish "pod on node X" from
# "deployment has an unscheduled pod" from "deployment has no pods" (no output).
# A deployment's pods are <name>-<pod-template-hash>-<suffix>; the hash is k8s
# SafeEncode (consonants + digits 2-9, NEVER vowels), so anchoring the segment
# after the name to that charset excludes a vowel-bearing sibling deployment in
# the same namespace (cert-manager-webhook for cert-manager, coredns-autoscaler
# for coredns) whose pods would otherwise match a plain name-prefix.
deployment_pod_nodes() {
  awk -F'\t' -v d="$1" '$1 ~ "^"d"-[b-df-hj-np-tv-z2-9]+-" {print ($2 == "" ? "<unscheduled>" : $2)}'
}

# maintenance-rearm-self-reboot.sh helpers

# rearm_marker_host: read a self-host marker file's content on stdin and print
# the target hostname (first line, all whitespace stripped). Prints nothing
# for a blank/whitespace-only marker so the caller treats it as "nothing to
# re-arm" instead of running ansible against an empty host pattern.
rearm_marker_host() {
  head -n1 | tr -d '[:space:]'
}

# rearm_remote_command <prompt_delay_secs>: print the remote shell snippet that
# re-arms the prompt self-reboot. ORDER IS THE SAFETY GUARANTEE: arm the
# SEPARATE maintenance-self-reboot-prompt unit first, and only after systemd-run
# succeeds (the && gate) tear down the long maintenance-self-reboot fallback. A
# failed arm short-circuits, leaving the fallback intact so the host still
# reboots, just later. The leading reset-failed/stop targets the PROMPT unit
# only, never the fallback.
rearm_remote_command() {
  local delay="${1:-60}"
  printf '%s' "systemctl reset-failed maintenance-self-reboot-prompt.timer maintenance-self-reboot-prompt.service 2>/dev/null || true; systemctl stop maintenance-self-reboot-prompt.timer maintenance-self-reboot-prompt.service 2>/dev/null || true; systemd-run --no-block --collect --on-active=${delay}s --unit=maintenance-self-reboot-prompt systemctl reboot && { systemctl reset-failed maintenance-self-reboot.timer maintenance-self-reboot.service 2>/dev/null || true; systemctl stop maintenance-self-reboot.timer maintenance-self-reboot.service 2>/dev/null || true; }"
}

# maintenance-ha-restart.sh parsers

# ha_reset_verdict: read the captured `ansible ... -m shell` output on stdin and
# print err / ok / notfound. err is checked first (a split-brain emitting both
# tokens fails closed) and both markers are anchored to column 0, so an echoed
# command source cannot match.
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
