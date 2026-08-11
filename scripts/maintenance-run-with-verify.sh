#!/usr/bin/env bash
# Run a maintenance command, then always run the post-maintenance verify
# script — even if the command failed. A failed maintenance action is
# exactly when you want a cluster-health snapshot, so verify must run
# unconditionally.
#
# Exit semantics:
#   - command failed:  exit with command's rc (verify still ran for diagnosis)
#   - command ok, verify failed: exit with verify's rc
#   - both ok: exit 0
#
# Usage:
#   bash scripts/maintenance-run-with-verify.sh <command> [args...]
#
# The command must be a single program with args: only "$@"'s exit status is
# captured, so a bare pipeline would mask a mid-pipe failure. Wrap one as
# `bash -c 'set -o pipefail; a | b'`.
#
# The verify script resolves via $CI_PROJECT_DIR when set, else this script's dir.

set +e  # don't let a command failure short-circuit verify

if [ "$#" -lt 1 ]; then
  echo "ERROR: $0 requires at least one argument (the command to run)" >&2
  exit 64  # EX_USAGE
fi

SCRIPT_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}/scripts"
VERIFY="${SCRIPT_DIR}/post-maintenance-verify.sh"

if [ ! -r "$VERIFY" ]; then
  # We invoke via `bash "$VERIFY"`, which doesn't need the +x bit — only
  # readability. Avoids spurious failures from CI checkouts that may not
  # preserve file modes.
  echo "ERROR: verify script not found or not readable: $VERIFY" >&2
  exit 64
fi

echo "=== Maintenance command: $* ==="
"$@"
op_rc=$?
echo ""
echo "=== Maintenance command exited with rc=$op_rc; running verify ==="
echo ""

bash "$VERIFY"
verify_rc=$?

echo ""
if [ "$op_rc" -ne 0 ]; then
  echo "=== SUMMARY: maintenance command FAILED (rc=$op_rc); verify rc=$verify_rc ==="
  exit "$op_rc"
fi
if [ "$verify_rc" -ne 0 ]; then
  echo "=== SUMMARY: maintenance command OK; verify FAILED (rc=$verify_rc) ==="
  exit "$verify_rc"
fi
echo "=== SUMMARY: maintenance command OK; verify OK ==="
