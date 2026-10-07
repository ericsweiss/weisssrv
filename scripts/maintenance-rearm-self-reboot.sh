#!/usr/bin/env bash
# Run from a maintenance CI job's `after_script`. If the run armed a detached
# self-host reboot (marker from _reboot-if-needed.yml), re-arm it to fire at
# +60s and disarm the long fallback timer. No-op without a recorded self-host.
set -uo pipefail

MARKER="${1:-/tmp/maintenance-self-host}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/maintenance-lib.sh
. "$SCRIPT_DIR/maintenance-lib.sh"

if [ ! -f "$MARKER" ]; then
  echo "maintenance-rearm: no self-host marker ($MARKER); nothing to re-arm."
  exit 0
fi

HOST=$(rearm_marker_host < "$MARKER")
if [ -z "$HOST" ]; then
  echo "maintenance-rearm: empty self-host marker; nothing to re-arm."
  rm -f "$MARKER"
  exit 0
fi

echo "maintenance-rearm: re-arming a prompt (+60s) reboot on self-host '$HOST' and disarming the long fallback."
cd "$SCRIPT_DIR/../ansible" || { echo "maintenance-rearm: cannot cd to ansible dir"; exit 0; }

# ansible ignores a config in a world-writable dir, and after_script runs in a
# fresh shell without the before_script's ANSIBLE_CONFIG: stage a mode-600 copy
# here.
ANSIBLE_CONFIG=$(mktemp /tmp/ansible-rearm-XXXXXXXX.cfg)
trap 'rm -f "$ANSIBLE_CONFIG"' EXIT
# On failure, bail to the long fallback timer - it still reboots the host.
if ! install -m 600 ansible.cfg "$ANSIBLE_CONFIG"; then
  echo "maintenance-rearm: WARN could not stage ansible.cfg; relying on the long fallback timer." >&2
  exit 0
fi
export ANSIBLE_CONFIG

# Uses the same auth as the run's plays: the `op` binary, OP_SERVICE_ACCOUNT_TOKEN
# and the on-disk SSH key from inventories/prod. The remote snippet is built by
# rearm_remote_command in maintenance-lib.sh, where it is unit-tested.
if op run -- ansible "$HOST" -i inventories/prod -b -m shell -a \
  "$(rearm_remote_command 60)"; then
  echo "maintenance-rearm: prompt reboot armed on $HOST."
  rm -f "$MARKER"
else
  # The long fallback timer armed during the run still reboots the host, up to
  # self_reboot_delay later instead of +60s, so the job need not fail.
  echo "maintenance-rearm: WARN re-arm FAILED for $HOST — falling back to the long timer armed during the run. Investigate op/SSH; the host WILL still reboot, just later." >&2
fi
exit 0
