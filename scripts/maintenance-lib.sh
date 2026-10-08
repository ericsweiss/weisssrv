#!/usr/bin/env bash
# Pure-logic helpers shared by the maintenance scripts, unit-tested by
# scripts/test_maintenance_lib.py. Functions only, no side effects: each reads
# stdin or arguments and writes a verdict. None call kubectl, curl or ansible.

# not_ready_node_names: `kubectl get nodes --no-headers` on stdin -> the NAME of
# each node whose STATUS does not begin with "Ready", so "Ready,SchedulingDisabled"
# (cordoned but healthy) counts as ready and is NOT listed.
not_ready_node_names() {
  awk '$2 !~ /^Ready/ {print $1}'
}

# kured_rebooting_filter: `node<TAB>annotation<TAB>unschedulable` rows on stdin ->
# the nodes kured is actively rebooting. Annotated AND cordoned is required:
# annotated-but-schedulable means kured is blocked, not mid-reboot.
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
# for unhealthy pods (STATUS not "Running", or READY "a/b" with a != b). Terminal
# Job outcomes go to the verify's failed-Job check (non-CronJob) and KubeJobFailed.

# The runner namespaces and pod prefix mirror maintenance_ci_runner_namespaces
# and maintenance_ci_executor_pod_prefix in group_vars/all.yml; override them in
# the environment to keep the two in step.
list_unhealthy_pods() {
  awk -v runner_ns="${MAINTENANCE_CI_RUNNER_NAMESPACES:-gitlab-runner gitlab-runner-privileged}" \
      -v runner_prefix="${MAINTENANCE_CI_EXECUTOR_POD_PREFIX:-runner-}" '
    BEGIN { n = split(runner_ns, ns, " "); for (i = 1; i <= n; i++) runner[ns[i]] = 1 }
    $4 == "Completed" || $4 == "Succeeded" || $4 == "Error" || $4 == "Failed" { next }
    # Runner JOB pods are pipeline state, not cluster health: Pending while
    # queued, Terminating while DinD tears down. The runner MANAGERS stay covered.
    ($1 in runner) && index($2, runner_prefix) == 1 { next }
    { split($3, a, "/"); if ($4 != "Running" || a[1] != a[2]) print }'
}

# deployment_replicas_ok <available> <desired>: exit 0 if available >= desired,
# defaulting blanks to 0 and 1; non-numeric input returns non-zero.
deployment_replicas_ok() {
  local avail="${1:-0}" desired="${2:-1}"
  [ "${avail:-0}" -ge "${desired:-1}" ] 2>/dev/null
}

# deployment_pod_nodes <deployment-name>: `name<TAB>nodeName` rows on stdin ->
# each of <deployment>'s pods' nodeName, or "<unscheduled>". The SafeEncode
# pod-template-hash match keeps a name-prefix sibling out.
deployment_pod_nodes() {
  awk -F'\t' -v d="$1" '$1 ~ "^"d"-[b-df-hj-np-tv-z2-9]+-" {print ($2 == "" ? "<unscheduled>" : $2)}'
}

# maintenance-rearm-self-reboot.sh helpers

# rearm_marker_host: read a self-host marker file's content on stdin and print
# the target hostname (first line, whitespace stripped). Prints nothing for a
# blank marker, so the caller treats it as "nothing to re-arm".
rearm_marker_host() {
  head -n1 | tr -d '[:space:]'
}

# rearm_remote_command <prompt_delay_secs>: the remote snippet that re-arms the
# prompt self-reboot. Order is the safety guarantee: the long fallback is torn
# down only behind the && gate, so a failed arm still leaves the host rebooting.
rearm_remote_command() {
  local delay="${1:-60}"
  printf '%s' "systemctl reset-failed maintenance-self-reboot-prompt.timer maintenance-self-reboot-prompt.service 2>/dev/null || true; systemctl stop maintenance-self-reboot-prompt.timer maintenance-self-reboot-prompt.service 2>/dev/null || true; systemd-run --no-block --collect --on-active=${delay}s --unit=maintenance-self-reboot-prompt systemctl reboot && { systemctl reset-failed maintenance-self-reboot.timer maintenance-self-reboot.service 2>/dev/null || true; systemctl stop maintenance-self-reboot.timer maintenance-self-reboot.service 2>/dev/null || true; }"
}

# maintenance-ha-restart.sh parsers

# ha_reset_verdict: captured `ansible ... -m shell` output on stdin -> err / ok /
# notfound. err wins when both tokens appear, and both markers are anchored to
# column 0 so an echoed command line cannot match.
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
# down-then-up debounce by one probe, printing "<streak> <went_down> <verdict>".
# verdict is healthy once up follows a down streak of 2 or more.
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

# ha_settle_verdict <went_down> <down_streak> <elapsed> <settle_secs>: print
# healthy-unverified when the settle window elapses with no downtime seen.
# Observed downtime prints keep-waiting, leaving the verdict to ha_observe_step.
ha_settle_verdict() {
  local went_down="$1" streak="$2" elapsed="$3" settle="$4"
  if [ "$went_down" != true ] && [ "$streak" -eq 0 ] && [ "$elapsed" -ge "$settle" ]; then
    echo "healthy-unverified"
  else
    echo "keep-waiting"
  fi
}
