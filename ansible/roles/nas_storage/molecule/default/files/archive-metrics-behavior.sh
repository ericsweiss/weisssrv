#!/usr/bin/env bash
# Molecule behavioral check for the ZFS-free metric helpers in the rendered
# archive-backupctl and media-mover.sh: the structural pins in
# archive-contract-assert.sh prove the code EXISTS; this executes it. Runs on
# the target via ansible.builtin.script after converge deploys both scripts.
set -euo pipefail

ARCHIVE="${1:-/usr/local/sbin/archive-backupctl}"
MOVER="${2:-/usr/local/sbin/media-mover.sh}"
[ -f "$ARCHIVE" ] || { echo >&2 "archive-backupctl not rendered at $ARCHIVE"; exit 1; }
[ -f "$MOVER" ] || { echo >&2 "media-mover.sh not rendered at $MOVER"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo >&2 "FAIL: $*"; exit 1; }

# --- archive-backupctl: _load_prev_dataset_metrics + write_prom_metrics ------
# Source the script with its entrypoint disabled so the functions are callable.
sed 's/^main "\$@"$/# main disabled for behavior test/' "$ARCHIVE" > "$WORK/archive-lib.sh"
grep -q 'main disabled for behavior test' "$WORK/archive-lib.sh" \
  || fail "could not disable archive-backupctl entrypoint"

# shellcheck disable=SC1091
source "$WORK/archive-lib.sh"
PROM_FILE="$WORK/archive_backup.prom"

cat > "$PROM_FILE" <<'EOM'
archive_backup_last_run_duration_seconds 10
archive_backup_last_run_success 0
archive_backup_last_success_timestamp_seconds 1700000000
archive_backup_dataset_last_success_timestamp_seconds{dataset="tank/share"} 1700000001
archive_backup_dataset_last_success_timestamp_seconds{dataset="tank/proxmox"} 1600000002
archive_backup_dataset_last_success_timestamp_seconds{dataset="tank/removed-from-src-list"} 1500000000
archive_backup_dataset_deferred_runs{dataset="tank/proxmox"} 2
EOM

_load_prev_dataset_metrics
[ "${_DS_SUCCESS_TS[tank/share]}" = "1700000001" ] || fail "seed: tank/share timestamp not loaded"
[ "${_DS_SUCCESS_TS[tank/proxmox]}" = "1600000002" ] || fail "seed: tank/proxmox timestamp not loaded"
[ "${_DS_DEFERRED[tank/proxmox]}" = "2" ] || fail "seed: deferred counter not loaded"

# Simulate: share replicated now, proxmox deferred again, run failed overall.
_DS_SUCCESS_TS[tank/share]=1800000000
_DS_DEFERRED[tank/share]=0
_DS_DEFERRED[tank/proxmox]=$(( ${_DS_DEFERRED[tank/proxmox]} + 1 ))
write_prom_metrics 0 42

grep -qx 'archive_backup_last_run_success 0' "$PROM_FILE" \
  || fail "whole-run success not written"
# Failure runs must PRESERVE the whole-run last-success timestamp.
grep -qx 'archive_backup_last_success_timestamp_seconds 1700000000' "$PROM_FILE" \
  || fail "failed run did not preserve the whole-run last-success timestamp"
grep -qxF 'archive_backup_dataset_last_success_timestamp_seconds{dataset="tank/share"} 1800000000' "$PROM_FILE" \
  || fail "successful dataset timestamp not updated"
grep -qxF 'archive_backup_dataset_last_success_timestamp_seconds{dataset="tank/proxmox"} 1600000002' "$PROM_FILE" \
  || fail "deferred dataset timestamp not preserved"
grep -qxF 'archive_backup_dataset_deferred_runs{dataset="tank/proxmox"} 3' "$PROM_FILE" \
  || fail "deferred counter not incremented"
grep -qxF 'archive_backup_dataset_deferred_runs{dataset="tank/share"} 0' "$PROM_FILE" \
  || fail "successful dataset deferred counter not reset"
# A dataset no longer in SRC_LIST must age out, not persist as an orphan series.
grep -q 'tank/removed-from-src-list' "$PROM_FILE" \
  && fail "orphan series for a removed dataset was re-emitted"

# --- media-mover: failure branch preserves the last-success timestamp --------
# media-mover.sh runs top-level code on source, so extract just the function.
awk '/^write_prom_metrics\(\) \{/{f=1} f; f && /^\}/{exit}' "$MOVER" > "$WORK/mover-fn.sh"
grep -q 'media_mover_last_run_success' "$WORK/mover-fn.sh" \
  || fail "could not extract media-mover write_prom_metrics"

env -i bash -c '
  set -euo pipefail
  PROM_FILE="'"$WORK"'/media_mover.prom"
  # shellcheck disable=SC1091
  source "'"$WORK"'/mover-fn.sh"
  printf "media_mover_last_success_timestamp_seconds 1650000000\n" > "$PROM_FILE"
  write_prom_metrics 0 7
  grep -qx "media_mover_last_run_success 0" "$PROM_FILE"
  grep -qx "media_mover_last_success_timestamp_seconds 1650000000" "$PROM_FILE"
' || fail "media-mover failure run did not preserve the last-success timestamp"

echo "archive/media-mover metric behavior OK"
