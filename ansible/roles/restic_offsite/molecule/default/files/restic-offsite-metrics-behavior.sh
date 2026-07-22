#!/usr/bin/env bash
# Molecule behavioral check for the network-free metric helpers + freshness
# parser in the rendered restic-offsitectl: the contract-assert pins prove the
# code EXISTS; this executes it. Runs on the target via ansible.builtin.script.
set -euo pipefail

s="${1:-/usr/local/sbin/restic-offsitectl}"
[ -f "$s" ] || { echo >&2 "restic-offsitectl not rendered at $s"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo >&2 "FAIL: $*"; exit 1; }

# Source the script with its entrypoint disabled so the functions are callable.
sed 's/^main "\$@"$/# main disabled for behavior test/' "$s" > "$WORK/lib.sh"
grep -q 'main disabled for behavior test' "$WORK/lib.sh" \
  || fail "could not disable restic-offsitectl entrypoint"
# shellcheck disable=SC1091
source "$WORK/lib.sh"

# Redirect the metric files into the temp dir.
PROM_FILE="$WORK/restic_offsite.prom"
VERIFY_PROM_FILE="$WORK/restic_offsite_verify.prom"

# --- write_prom_metrics: a failed run preserves the last-success timestamp ---
printf 'restic_offsite_last_success_timestamp_seconds 1650000000\n' > "$PROM_FILE"
write_prom_metrics 0 42 0 0
grep -qx 'restic_offsite_last_run_success 0' "$PROM_FILE" \
  || fail "failed run did not record success=0"
grep -qx 'restic_offsite_last_success_timestamp_seconds 1650000000' "$PROM_FILE" \
  || fail "failed run did not preserve the last-success timestamp"

# A success run writes a fresh timestamp + the size gauges.
write_prom_metrics 1 7 123 45
grep -qx 'restic_offsite_last_run_success 1' "$PROM_FILE" || fail "success not written"
grep -qx 'restic_offsite_repo_size_bytes 123' "$PROM_FILE" || fail "repo size gauge missing"
grep -qx 'restic_offsite_snapshot_total_bytes 45' "$PROM_FILE" || fail "snapshot size gauge missing"

# --- write_verify_metrics: preservation mirrors the run metric ---
printf 'restic_offsite_last_verify_timestamp_seconds 1640000000\n' > "$VERIFY_PROM_FILE"
write_verify_metrics 0
grep -qx 'restic_offsite_last_verify_success 0' "$VERIFY_PROM_FILE" \
  || fail "verify failure did not record success=0"
grep -qx 'restic_offsite_last_verify_timestamp_seconds 1640000000' "$VERIFY_PROM_FILE" \
  || fail "verify failure did not preserve the last-verify timestamp"

# --- snap_age_seconds: parses archsync-YYYYMMDD-HHMMSS names ---
old_age="$(snap_age_seconds archsync-20200101-000000)"
[ "$old_age" -gt 100000000 ] || fail "old snapshot age not large ($old_age)"
now_name="archsync-$(date -u +%Y%m%d-%H%M%S)"
new_age="$(snap_age_seconds "$now_name")"
[ "$new_age" -lt 3600 ] || fail "fresh snapshot age not small ($new_age)"
bad_age="$(snap_age_seconds not-a-snapshot)"
[ "$bad_age" = "999999999" ] || fail "unparseable name did not degrade to stale ($bad_age)"

echo "restic-offsite metric/freshness behavior OK"
