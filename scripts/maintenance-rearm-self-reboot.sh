#!/usr/bin/env bash
# Run from a maintenance CI job's `after_script`. If the run armed a DETACHED
# self-host reboot (marker written by _reboot-if-needed.yml when the executor's
# own Proxmox host needs one), re-arm it to fire at +60s and disarm the long
# fallback timer. No-op if no self-host was recorded.
#
# after_script always runs (success, failure, cancel), so this anchors the reboot
# to the real job end rather than a fixed delay that could fire mid-job. The long
# timer stays as the fallback for a runner crash or an after_script timeout.
# Quorum-safe by construction: _reboot-if-needed.yml only takes the detached path
# on an opt-* host, which carries no etcd member.
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

# ansible IGNORES a config that lives in a world-writable dir (the CI checkout),
# so the job's before_script installs ansible.cfg to a mode-600 temp and exports
# ANSIBLE_CONFIG. That ran in a SEPARATE shell whose EXIT trap already deleted the
# temp before this after_script — so ANSIBLE_CONFIG is unset here and ssh/become/
# host-key settings would be lost (the re-arm would fail and always fall back to
# the long timer). Recreate the secure copy so the prompt re-arm actually works.
ANSIBLE_CONFIG=$(mktemp /tmp/ansible-rearm-XXXXXXXX.cfg)
trap 'rm -f "$ANSIBLE_CONFIG"' EXIT
# Guard the staging copy: under `set -uo pipefail` (no -e) a failed install would
# otherwise leave ANSIBLE_CONFIG pointing at an empty file and the re-arm would run
# with ansible's defaults (wrong ssh/become) and fail anyway. Bail to the long
# fallback timer instead, which still reboots the host.
if ! install -m 600 ansible.cfg "$ANSIBLE_CONFIG"; then
  echo "maintenance-rearm: WARN could not stage ansible.cfg; relying on the long fallback timer." >&2
  exit 0
fi
export ANSIBLE_CONFIG

# Depends on the same auth the run's plays use, all present in the after_script
# shell (same job pod): the apt-installed `op` binary, OP_SERVICE_ACCOUNT_TOKEN,
# and the on-disk SSH key + ansible_user/become from inventories/prod.
# Best-effort. The remote snippet (arm-prompt-first, &&-gated fallback teardown
# — the ordering IS the safety guarantee) is built by rearm_remote_command in
# maintenance-lib.sh, where it is unit-tested.
if op run -- ansible "$HOST" -i inventories/prod -b -m shell -a \
  "$(rearm_remote_command 60)"; then
  echo "maintenance-rearm: prompt reboot armed on $HOST."
  rm -f "$MARKER"
else
  # Do NOT swallow this. The long fallback timer armed during the run still
  # reboots the host, so the job need not fail — but make the missed prompt re-arm
  # loud in the job log (the marker is ephemeral pod /tmp, so the log IS the
  # breadcrumb). Leave the marker so a manual re-run of this script can retry.
  echo "maintenance-rearm: WARN re-arm FAILED for $HOST — falling back to the long timer armed during the run. Investigate op/SSH; the host WILL still reboot, just later." >&2
fi
exit 0
