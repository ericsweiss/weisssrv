#!/usr/bin/env bash
# vzdump hookscript: publishes Proxmox nightly-backup health metrics to the
# node_exporter textfile collector, mirroring the gitlab/archive backup
# collectors. Wired via the jobs.cfg `script` property (proxmox_backup role).
# node_exporter_host deploys it to every Proxmox host, because a cluster-wide
# `all` vzdump job runs on each node for that node's local guests, so the hook
# is invoked on all of them — a node missing it would abort its own backups.
#
# vzdump invokes this as: <script> <phase> [args...]. Phases used:
#   backup-abort  a single guest's backup failed -> mark the run degraded
#   job-end       the run finished; success only if no guest aborted
#   job-abort     the whole job failed
# job-end fires even when individual guests failed, so a per-run marker records
# any backup-abort and downgrades the job-end verdict. Metrics:
#   vzdump_backup_last_run_success                 1 clean, 0 any failure
#   vzdump_backup_last_success_timestamp_seconds   unix time of last SUCCESS
#
# A failure writing metrics must never abort the backup, so this always exits 0
# and does NOT `set -e`.
set -uo pipefail

PHASE="${1:-}"
TEXTFILE_DIR="/var/lib/node_exporter"
PROM="${TEXTFILE_DIR}/vzdump_backup.prom"
# Per-run marker: its presence means at least one guest aborted this run. Keyed
# on vzdump's job id (falling back to the parent PID) so concurrent or repeated
# runs don't clobber each other's state.
FAIL_MARKER="/run/vzdump-metrics-hook.${VZDUMP_JOBID:-${PPID:-0}}.failed"

case "$PHASE" in
  backup-abort)
    : > "$FAIL_MARKER" 2>/dev/null || true
    exit 0
    ;;
  job-end)
    if [ -e "$FAIL_MARKER" ]; then SUCCESS=0; else SUCCESS=1; fi
    rm -f "$FAIL_MARKER" 2>/dev/null || true
    ;;
  job-abort)
    SUCCESS=0
    rm -f "$FAIL_MARKER" 2>/dev/null || true
    ;;
  *) exit 0 ;;
esac

now="$(date +%s)"

# Preserve the last *successful* run timestamp across a failed run: on failure
# reuse the existing value (0 if none yet); on success stamp now.
if [ "$SUCCESS" -eq 1 ]; then
  last_success="$now"
else
  last_success="$(sed -n 's/^vzdump_backup_last_success_timestamp_seconds \([0-9][0-9]*\)$/\1/p' "$PROM" 2>/dev/null || true)"
  last_success="${last_success:-0}"
fi

# Atomic write so node_exporter never scrapes a half-written file.
{
  tmp="$(mktemp "${PROM}.XXXXXX")" || exit 0
  cat > "$tmp" <<EOF
# HELP vzdump_backup_last_run_success Whether the last Proxmox vzdump job run backed up every guest (1) or had a failure (0).
# TYPE vzdump_backup_last_run_success gauge
vzdump_backup_last_run_success ${SUCCESS}
# HELP vzdump_backup_last_success_timestamp_seconds Unix time of the last fully successful Proxmox vzdump job run.
# TYPE vzdump_backup_last_success_timestamp_seconds gauge
vzdump_backup_last_success_timestamp_seconds ${last_success}
EOF
  chmod 0644 "$tmp"
  mv -f "$tmp" "$PROM"
} || true

exit 0
