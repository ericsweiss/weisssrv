#!/usr/bin/env bash
# Molecule behavioral check for the ZFS-free metric helpers in the rendered
# archive-backupctl and media-mover.sh: the structural pins in
# archive-contract-assert.sh prove the code EXISTS; this executes it. Runs on
# the target via ansible.builtin.script after converge deploys both scripts.
set -euo pipefail

ARCHIVE="${1:-/usr/local/sbin/archive-backupctl}"
MOVER="${2:-/usr/local/sbin/media-mover.sh}"
SWAPCLEAN="${3:-/usr/local/sbin/swap-clean.sh}"
[ -f "$ARCHIVE" ] || { echo >&2 "archive-backupctl not rendered at $ARCHIVE"; exit 1; }
[ -f "$MOVER" ] || { echo >&2 "media-mover.sh not rendered at $MOVER"; exit 1; }
[ -f "$SWAPCLEAN" ] || { echo >&2 "swap-clean.sh not rendered at $SWAPCLEAN"; exit 1; }

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

# --- swap-clean: write_prom_metrics arms + cleanup restart-failure demotion ---
# swap-clean.sh runs its logic in main() and ends with `main "$@"`, so neutralize
# the entrypoint (as the sibling archive/restic behavior tests do) and source the
# rest to get the functions + globals WITHOUT running any swapoff/swapon.
sed 's/^main "\$@"$/# main disabled for behavior test/' "$SWAPCLEAN" > "$WORK/swap-clean-lib.sh"
grep -q 'main disabled for behavior test' "$WORK/swap-clean-lib.sh" \
  || fail "could not disable swap-clean entrypoint"

# Arm 1: write_prom_metrics success emits the full series incl. the always-present
# restart-failures gauge (0 here). Each arm runs in its own env -i subshell so the
# sourced `set -euo pipefail` + globals never leak into this harness.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean.prom"
  write_prom_metrics 1 1024 0 0
  grep -qx "swap_clean_last_run_success 1" "$PROM_FILE" || exit 21
  grep -qx "swap_clean_swap_cleared_bytes 1024" "$PROM_FILE" || exit 22
  grep -qx "swap_clean_guest_restart_failures 0" "$PROM_FILE" || exit 23
  grep -q "^swap_clean_last_success_timestamp_seconds " "$PROM_FILE" || exit 24
' || fail "swap-clean write_prom_metrics success arm"

# Arm 2: a failed run records success=0 and PRESERVES the prior success timestamp.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean.prom"
  printf "swap_clean_last_success_timestamp_seconds 1650000000\n" > "$PROM_FILE"
  write_prom_metrics 0 0 0 0
  grep -qx "swap_clean_last_run_success 0" "$PROM_FILE" || exit 25
  grep -qx "swap_clean_last_success_timestamp_seconds 1650000000" "$PROM_FILE" || exit 26
  grep -qx "swap_clean_guest_restart_failures 0" "$PROM_FILE" || exit 27
' || fail "swap-clean failure arm did not preserve the last-success timestamp"

# Arm 3: cleanup() with a stubbed FAILING qm — a guest we "stopped" cannot be
# restarted, so cleanup must demote run_success to 0 AND emit restart-failures 1.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean.prom"
  # qm status -> no output (not running); qm start -> non-zero (restart fails).
  qm() { return 1; }
  run_success=1          # start from a would-be healthy run
  swap_cleared_bytes=2048
  arc_orig=""            # no ARC to restore
  guests_to_restore="999"
  cleanup
  grep -qx "swap_clean_last_run_success 0" "$PROM_FILE" || exit 28
  awk "/^swap_clean_guest_restart_failures /{print \$2}" "$PROM_FILE" | grep -qx "1" || exit 29
  grep -qx "swap_clean_guests_stopped_count 1" "$PROM_FILE" || exit 30
' || fail "swap-clean cleanup did not demote run + emit restart-failures on a failed restart"

echo "archive/media-mover/swap-clean metric behavior OK"
