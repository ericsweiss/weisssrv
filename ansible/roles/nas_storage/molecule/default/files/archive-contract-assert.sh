#!/usr/bin/env bash
# Molecule contract check for the rendered archive-backupctl, run on the target
# via ansible.builtin.script after converge deploys it. Kept as a real *.sh file
# (not an inline `shell:` block) so it is shellcheck-lintable and its embedded
# quotes do not trip Ansible's argument splitter. These are STRUCTURAL checks —
# the container has no ZFS, so the actual zvol-vs-filesystem receive (which -o
# options ZFS accepts) is not exercised; that needs a privileged loopback pool.
set -euo pipefail

s="${1:-/usr/local/sbin/archive-backupctl}"
[ -f "$s" ] || { echo >&2 "archive-backupctl not rendered at $s"; exit 1; }

# The rendered script must be valid bash. The CI shellcheck job only lints the
# neutralized template; this validates the REAL render's syntax (not semantics).
bash -n "$s"

# SRC_LIST entries (full paths) — array-element lines only, so a future quoted
# token inside an in-block comment is not mis-parsed as a dataset. Basenames (bn)
# are the restore-case labels; the full paths are kept for the membership check.
src_list="$(awk '/^SRC_LIST=\(/{f=1; next} f && /^\)/{f=0} f' "$s" \
  | grep -E '^[[:space:]]*"' | grep -oE '"[^"]+"' | tr -d '"')"
bn="$(printf '%s\n' "$src_list" | sed 's#.*/##')"
[ "$(printf '%s\n' "$bn" | grep -c .)" -ge 1 ] || { echo >&2 "no SRC_LIST datasets parsed"; exit 1; }

# The new dataset must be in SRC_LIST ITSELF — cmd_run iterates SRC_LIST to
# replicate, so a dataset present only in MAP/RMAP/restore/lock is silently never
# backed up. Match the FULL path (not just basename immich-data, which a wrong
# pool like ssd/immich-data would also satisfy) against the SRC_LIST entries —
# not a free-floating substring the MAP key + RMAP value would also satisfy.
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

# Restore labels are basenames, so they must be unique across SRC_LIST (a leaf
# collision like tank/proxmox + ssd/proxmox would make a restore case ambiguous).
dup="$(printf '%s\n' "$bn" | sort | uniq -d)"
[ -z "$dup" ] || { echo >&2 "duplicate SRC_LIST basenames (ambiguous restore case): $dup"; exit 1; }

# The lock-list block — the one parallel list with NO runtime self-protection
# (MAP/RMAP fail loudly under set -u; a dataset missing from the lock list is
# silently skipped by lock_backup_tree, leaving its archive copy writable).
locklist="$(awk '/^_lock_existing_backup_datasets\(\)/{f=1} f && /for dst in/{g=1; next} g && /^[[:space:]]*do$/{g=0; f=0} g' "$s")"

# Every dataset must be restorable individually AND via `restore all` AND locked.
miss=0
while IFS= read -r d; do
  [ -n "$d" ] || continue
  for mode in safe force; do
    grep -qF "${d}) _restore_one \"\${POOL_DST}/${d}\" ${mode}" "$s" \
      || { echo >&2 "no per-target ${mode} restore case: $d"; miss=1; }
    [ "$(grep -cF "_restore_one \"\${POOL_DST}/${d}\" ${mode}" "$s")" -ge 2 ] \
      || { echo >&2 "missing all) ${mode} entry: $d"; miss=1; }
  done
  printf '%s\n' "$locklist" | grep -qF "\"\${POOL_DST}/${d}\"" \
    || { echo >&2 "missing lock-list entry: $d"; miss=1; }
done <<< "$bn"
[ "$miss" -eq 0 ] || exit 1

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

echo "archive-backupctl contract OK"
