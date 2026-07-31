#!/usr/bin/env bash
# Molecule contract check for the rendered restic-offsitectl. STRUCTURAL pins
# for what the ZFS-less/network-less container can't exercise: the zvol
# clone/mount/destroy lifecycle, the full subcommand surface, restic's
# backup/forget flags, and every metric name the alerts consume. Kept as a real
# *.sh (shellcheck-lintable; its quotes don't trip Ansible's arg splitter).
set -euo pipefail

s="${1:-/usr/local/sbin/restic-offsitectl}"
[ -f "$s" ] || { echo >&2 "restic-offsitectl not rendered at $s"; exit 1; }

# The REAL render must be valid bash (CI shellcheck only lints the neutralized template).
bash -n "$s"

# `-- "$1"` so a token starting with '-' (e.g. --exclude-file) is not parsed as
# a grep option.
need() { grep -qF -- "$1" "$s" || { echo >&2 "missing contract token: $1"; exit 1; }; }

# subcommand surface (main dispatch)
need 'run) cmd_run'
need 'restore) cmd_restore'
need 'verify) cmd_verify'
need 'snapshots) cmd_snapshots'
need 'prune) cmd_prune'
need 'status) cmd_status'

# restic backup + retention flags
need '--exclude-file'
need '--exclude-caches'
need '--tag nightly'
need '--one-file-system=false'
need '--keep-daily'
need '--keep-weekly'
need '--keep-monthly'
need '--keep-yearly'
# WORM-ish floor + pinned retention grouping. Without --keep-last, corruption
# that persists 3 days walks every daily restore point out of a bucket with no
# Object Lock; without a pinned --group-by, changing the source list forks a new
# retention group and strands the old group's snapshots.
need '--keep-last'
need '--group-by'
need '--prune'
# Blast-radius ceiling: the destructive forget must be preceded by a --dry-run
# whose delete-set size is compared against FORGET_MAX_REMOVE, so a keep-policy
# or --group-by change cannot walk history out of a bucket with no Object Lock
# before anyone reads a snapshot list.
need 'FORGET_MAX_REMOVE'
need '--dry-run'
need 'REFUSING to prune'
# First-run idempotent init.
need 'restic cat config'
need 'restic init'

# freshness guard (never upload a stale tree)
need 'FRESH_MAX_AGE_H'
need 'snap_age_seconds'
need 'stale-source'

# already-uploaded short-circuit: both triggers (archive-backup's OnSuccess= and
# the fallback timer) fire nightly, so the second one must skip. The skip is
# conditional on every source being PRESENT and FRESH — dropping that condition
# would silently skip a total archsync failure instead of aborting loudly.
need 'source_snap_epochs'
need 'last_success_epoch'
need 'already-uploaded'
need '--force'

# zvol clone/mount/destroy lifecycle + EXIT-trap cleanup
# These MUST survive even though ZVOL_SOURCES is empty in molecule: reverting the
# clone/destroy/noload/EXIT-trap logic would strand clones on the real host.
need 'trap cleanup EXIT'
need 'zfs clone'
need 'zfs destroy -r'
need 'udevadm settle'
need '_CLONES_TO_DESTROY'
need '_ZVOL_MOUNTS_TO_UMOUNT'
# The in-loop mount must consume $fstype/$mopts (type/opts not hardcoded); the
# literal we search for intentionally contains unexpanded $-vars.
# shellcheck disable=SC2016
need 'mount -t "$fstype" -o "$mopts"'
need 'clone-failed'
need 'mount-failed'
# Stale-clone destroy must be gated on the dataset's ORIGIN pointing at our
# zvol — an unrelated dataset sharing the derived name must be refused, never
# recursively destroyed (data-loss guard).
need 'zfs get -H -o value origin'
need 'clone-conflict'

# single-instance lock
need 'flock -n 9'

# --- hide-only key contract: restic's default rclone.args carry
# --b2-hard-delete, which maps every delete (incl. the mid-backup lock
# refresh) to b2_delete_file_version — refused by the restricted key. The
# wrapper must strip it on every invocation.
need 'rclone.args="serve restic --stdio"'

# metric names (alert contract)
for m in \
  restic_offsite_last_run_success \
  restic_offsite_last_success_timestamp_seconds \
  restic_offsite_last_run_duration_seconds \
  restic_offsite_repo_size_bytes \
  restic_offsite_snapshot_total_bytes \
  restic_offsite_last_verify_success \
  restic_offsite_last_verify_timestamp_seconds ; do
  need "$m"
done

echo "restic-offsitectl contract OK"
