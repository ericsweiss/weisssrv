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

# not_ready_node_names: read `kubectl get nodes --no-headers` on stdin, print the
# NAME ($1) of each node whose STATUS ($2) does not begin with "Ready" (so
# "Ready,SchedulingDisabled" — cordoned-but-healthy — is treated as ready and NOT
# listed). The verify then classifies each printed node (kured-rebooting -> warn,
# anything else -> error).
not_ready_node_names() {
  awk '$2 !~ /^Ready/ {print $1}'
}

# kured_rebooting_filter: read `node<TAB>annotation<TAB>unschedulable` rows
# (jsonpath output) on stdin and print the names of nodes kured is ACTIVELY
# rebooting. A node qualifies only if it is BOTH kured-annotated AND cordoned
# (unschedulable): kured writes the annotation BEFORE its block-check and does
# NOT clear it when blocked, so an annotated-but-still-schedulable node is
# stale/blocked, not mid-reboot — excusing it would mask a real problem.
# (Matches the annotation+cordoned logic in _wait-no-kured-server-reboot.yml.)
kured_rebooting_filter() {
  awk -F'\t' '$2 != "" && $3 == "true" {print $1}'
}

# classify_not_ready_nodes <kured_names>: read not-ready node names on stdin
# and print one verdict line per node — "excused <name>" when the node appears
# (exact match) in the newline-separated <kured_names> list, "error <name>"
# otherwise. The verify WARNs excused nodes (kured rebooting, verified next
# run) and ERRORs the rest; any "error" line also means the grace re-read is
# warranted.
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

# list_unhealthy_pods: read `kubectl get pods -A --no-headers` on stdin, print
# the rows for pods that are genuinely unhealthy. Terminal BATCH outcomes are
# skipped — Completed/Succeeded, AND Error/Failed: those statuses only occur for
# Job/CronJob pods (restartPolicy Never/OnFailure) that exited; a flaky CronJob
# leaving failed-attempt pods (e.g. cloudflare-ddns retrying a transient egress
# blip before the run succeeds) is not a maintenance regression and must not
# false-alarm the verify. A PERSISTENTLY-broken CronJob (e.g. cloudflare-ddns with
# an expired token) is intentionally NOT this check's job — the DDNSStale
# Prometheus alert (no successful run in >1h) is its backstop, independent of the
# maintenance verify. Caveat: a restartPolicy:Always pod CAN momentarily
# render as STATUS=Error (the last-terminated container reason) before the
# kubelet flips it to CrashLoopBackOff, so this filter is not a complete health
# view on its own — but a genuinely-broken app is still caught by the verify's
# 25s grace re-check (it reads as CrashLoopBackOff/Pending by then, which ARE
# flagged) and by the critical-Deployment replica check. The skip assumes bare
# Error/Failed only originate from Job/CronJob-managed pods (true for this
# cluster); the verify's failed-Job check backstops one-shot Jobs, but a future
# naked restartPolicy:Never Pod that exits non-zero would not be re-flagged here.
# A pod is unhealthy if its STATUS ($4) is not "Running" OR READY ($3, "a/b") a != b.
list_unhealthy_pods() {
  awk '
    $4 == "Completed" || $4 == "Succeeded" || $4 == "Error" || $4 == "Failed" { next }
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

# --- maintenance-rearm-self-reboot.sh helpers ------------------------------

# rearm_marker_host: read a self-host marker file's content on stdin and print
# the target hostname (first line, all whitespace stripped). Prints nothing
# for a blank/whitespace-only marker so the caller treats it as "nothing to
# re-arm" instead of running ansible against an empty host pattern.
rearm_marker_host() {
  head -n1 | tr -d '[:space:]'
}

# rearm_remote_command <prompt_delay_secs>: print the remote shell snippet that
# re-arms the prompt self-reboot. ORDER MATTERS for the safety guarantee: arm a
# SEPARATE prompt unit (maintenance-self-reboot-prompt) FIRST, and only AFTER
# systemd-run succeeds (the && gate) tear down the long fallback timer
# (maintenance-self-reboot, armed during the run). If the prompt arm fails, the
# && short-circuits so the fallback is left INTACT — the host still reboots on
# it, just later. (An earlier design reused the fallback's own --unit name and
# stopped it BEFORE arming; a systemd-run failure after that stop left NO timer
# at all, silently stranding the host despite the "still reboots" log.) The
# leading reset-failed/stop targets the PROMPT unit only (clearing any stale
# prompt from a prior after_script), never the fallback.
rearm_remote_command() {
  local delay="${1:-60}"
  printf '%s' "systemctl reset-failed maintenance-self-reboot-prompt.timer maintenance-self-reboot-prompt.service 2>/dev/null || true; systemctl stop maintenance-self-reboot-prompt.timer maintenance-self-reboot-prompt.service 2>/dev/null || true; systemd-run --no-block --collect --on-active=${delay}s --unit=maintenance-self-reboot-prompt systemctl reboot && { systemctl reset-failed maintenance-self-reboot.timer maintenance-self-reboot.service 2>/dev/null || true; systemctl stop maintenance-self-reboot.timer maintenance-self-reboot.service 2>/dev/null || true; }"
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
