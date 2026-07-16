#!/usr/bin/env bash
# Molecule contract check for the rendered archive-backupctl, run on the target
# via ansible.builtin.script after converge deploys it. Kept as a real *.sh file
# (not an inline `shell:` block) so it is shellcheck-lintable and its embedded
# quotes do not trip Ansible's argument splitter. These are STRUCTURAL checks —
# the container has no ZFS, so the actual zvol-vs-filesystem receive (which -o
# options ZFS accepts) is not exercised; that needs a privileged loopback pool.
#
# MAP/RMAP, the lock list, and the restore targets are all DERIVED from
# SRC_LIST inside the script (duplicates fail loudly at startup), so the only
# list-level contracts left to pin are SRC_LIST membership and the
# root-is-a-filesystem allow-list.
set -euo pipefail

s="${1:-/usr/local/sbin/archive-backupctl}"
[ -f "$s" ] || { echo >&2 "archive-backupctl not rendered at $s"; exit 1; }

# The rendered script must be valid bash. The CI shellcheck job only lints the
# neutralized template; this validates the REAL render's syntax (not semantics).
bash -n "$s"

# SRC_LIST entries (full paths) — array-element lines only, so a future quoted
# token inside an in-block comment is not mis-parsed as a dataset.
src_list="$(awk '/^SRC_LIST=\(/{f=1; next} f && /^\)/{f=0} f' "$s" \
  | grep -E '^[[:space:]]*"' | grep -oE '"[^"]+"' | tr -d '"')"
bn="$(printf '%s\n' "$src_list" | sed 's#.*/##')"
[ "$(printf '%s\n' "$bn" | grep -c .)" -ge 1 ] || { echo >&2 "no SRC_LIST datasets parsed"; exit 1; }

# The new dataset must be in SRC_LIST ITSELF — cmd_run iterates SRC_LIST to
# replicate, and every other structure is derived from it. Match the FULL path
# (not just basename immich-data, which a wrong pool like ssd/immich-data would
# also satisfy).
printf '%s\n' "$src_list" | grep -qx 'tank/immich-data' || { echo >&2 "tank/immich-data missing from SRC_LIST"; exit 1; }

# Enforce the SRC_LIST-root-is-a-filesystem invariant (documented at the SRC_LIST
# definition): the -R initial/incremental/resume receives apply RECV_SAFE_OPTS'
# -o mountpoint=none/canmount=off to each root unconditionally, which ZFS rejects
# on a zvol. The container has no ZFS, so type can't be probed — instead pin the
# roots to a known-filesystem allow-list. Adding a SRC_LIST root then fails here
# until a human confirms it is a filesystem (NOT a zvol) and adds it below.
expected_roots="tank/share tank/backups tank/nextcloud-data tank/proxmox tank/immich-data ssd/appdata ssd/databases"
while IFS= read -r root; do
  [ -n "$root" ] || continue
  case " $expected_roots " in
    *" $root "*) ;;
    *) echo >&2 "SRC_LIST root '$root' not in the known-filesystem allow-list — confirm it is a filesystem (not a zvol; see the SRC_LIST invariant) before adding it here"; exit 1 ;;
  esac
done <<< "$src_list"

# Restore labels are basenames, so they must be unique across SRC_LIST (the
# script fails loudly at startup on a collision, but that runtime check can't
# execute in this ZFS-less container — pin it statically too).
dup="$(printf '%s\n' "$bn" | sort | uniq -d)"
[ -z "$dup" ] || { echo >&2 "duplicate SRC_LIST basenames (ambiguous restore/backup target): $dup"; exit 1; }

# Re-seed type->arm coupling. The dtype capture, fail-loud abort, and receive all
# live in the per-dataset re-seed while-loop; scope their pins to that loop body
# with ALL comments stripped — whole-line (grep -vE) AND trailing, both space- and
# ;-preceded (sed) — so a stray comment carrying a pinned token can't mask a
# reverted live statement (false green). The strip is safe: the only '#' on a live
# loop line is the parameter expansion ${ds#"${src}"}, whose '#' is preceded by
# 's' (neither space nor ';'), so '[[:space:];]#' never matches it. The two arm
# pins are instead awk-anchored to their type test (the FIRST ^recv_opts= line
# after it), which already skips comments and still catches an inversion.
reseed_loop="$(awk '/while IFS= read -r snap; do/{f=1} f; /done <<< "\$snap_list"/{f=0}' "$s" \
  | grep -vE '^[[:space:]]*#' | sed -E 's/[[:space:];]#.*$//')"
# Pin the FULL guarded capture (the 2>/dev/null || true routes a get error to the
# fail-loud else, not set -e).
printf '%s\n' "$reseed_loop" | grep -qF 'dtype="$(zfs get -H -o value type "$ds" 2>/dev/null || true)"' \
  || { echo >&2 "guarded dtype capture not found in re-seed loop"; exit 1; }
awk '/\[\[ "\$dtype" == "volume" \]\]; then/{f=1} f && /^[[:space:]]*recv_opts=/{print; exit}' "$s" \
  | grep -qF 'recv_opts=( -o readonly=on )' || { echo >&2 "volume arm not coupled to readonly-only"; exit 1; }
awk '/\[\[ "\$dtype" == "filesystem" \]\]; then/{f=1} f && /^[[:space:]]*recv_opts=/{print; exit}' "$s" \
  | grep -qF 'recv_opts=( "${RECV_SAFE_OPTS[@]}" )' || { echo >&2 "filesystem arm not coupled to RECV_SAFE_OPTS"; exit 1; }
printf '%s\n' "$reseed_loop" | grep -qF 'Re-seed aborted: cannot determine type of' \
  || { echo >&2 "unknown-type abort missing from re-seed loop"; exit 1; }
# The receive must CONSUME recv_opts. Pinning the arms is meaningless if the
# receive hardcodes a fixed opt set: reverting this line to "${RECV_SAFE_OPTS[@]}"
# IS the original zvol-rejection bug, and SC2034 (recv_opts unused) is CI-excluded.
printf '%s\n' "$reseed_loop" | grep -qF '| zfs receive -s -u "${recv_opts[@]}" "$sub"' \
  || { echo >&2 "in-loop re-seed receive not coupled to recv_opts"; exit 1; }

# Deferral contract: the vzdump-quiesce timeout must exit 75 (EX_TEMPFAIL), and
# cmd_run must branch on it separately from real failures — reverting the guard
# to `return 1` re-creates the nightly ArchiveBackupFailed false alarm the DEFER
# semantics fixed. Anchor to the DEFER log line so a comment can't satisfy it.
awk '/log "DEFER \$\{src\}/{f=1} f && /return/{print; exit}' "$s" \
  | grep -qF 'return 75' || { echo >&2 "vzdump-quiesce timeout does not return 75 (DEFER)"; exit 1; }
grep -qF 'elif [[ "$rc" -eq 75 ]]; then' "$s" \
  || { echo >&2 "cmd_run does not branch on the DEFER exit code 75"; exit 1; }
# Missing-source SKIP must exit 73 (not 0): a rc-0 SKIP would refresh the
# dataset's last-success timestamp and silence ArchiveBackupDatasetStale for a
# vanished source.
grep -qF 'return 73; }' "$s" \
  || { echo >&2 "missing-source SKIP does not return 73"; exit 1; }
grep -qF 'elif [[ "$rc" -eq 73 ]]; then' "$s" \
  || { echo >&2 "cmd_run does not branch on the missing-source exit code 73"; exit 1; }

# Per-dataset metric contract: both series must be emitted via the shared
# gauge helper (the dataset-stale + chronically-deferred alerts consume them),
# the helper must filter to current SRC_LIST members (no orphan series), and
# state must be seeded from the previous run so a deferral/abort preserves
# series for datasets the run never reached. Behavior is exercised by
# archive-metrics-behavior.sh; these pins catch a wholesale removal.
grep -qF '_emit_dataset_gauge archive_backup_dataset_last_success_timestamp_seconds' "$s" \
  || { echo >&2 "per-dataset last-success metric not emitted"; exit 1; }
grep -qF '_emit_dataset_gauge archive_backup_dataset_deferred_runs' "$s" \
  || { echo >&2 "per-dataset deferred-runs metric not emitted"; exit 1; }
awk '/_emit_dataset_gauge\(\) \{/{f=1} f && /MAP\[\$ds\]/{print; exit}' "$s" \
  | grep -q 'continue' || { echo >&2 "emit helper lost its SRC_LIST (MAP) orphan filter"; exit 1; }
[ "$(grep -c '_load_prev_dataset_metrics' "$s")" -ge 2 ] \
  || { echo >&2 "_load_prev_dataset_metrics not defined+called (previous-run seeding lost)"; exit 1; }

echo "archive-backupctl contract OK"
