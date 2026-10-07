#!/usr/bin/env bash
# Restart the Home Assistant VM (VMID 154, Proxmox-HA-managed) and wait for it
# to come back online. `qm reset` runs across all proxmox hosts; only the node
# whose `qm list` shows 154 acts, since HA may have migrated the VM.

set -euo pipefail
# Source the pure-logic helpers (token verdict + down-then-up debounce) before
# cd'ing away, resolving against this script's own directory.
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/maintenance-lib.sh
. "$_SCRIPT_DIR/maintenance-lib.sh"
# The probe URL comes from cluster-config.yaml, the single source, not a literal.
HA_URL="${HA_HEALTH_URL:-https://home.$("$_SCRIPT_DIR/cluster-config-value.sh" cluster_internal_domain)}"
cd "$_SCRIPT_DIR/../ansible"

echo "Restarting Home Assistant VM (vm:154) via qm reset on its current host..."
# `|| true`: `ansible <group>` exits non-zero if any host is unreachable. The
# remote snippet always exits 0 and reports a runtime-assembled `vmreset:$r`
# token, so the literal markers never appear in an echoed command source.
reset_output=$(op run -- ansible proxmox -i inventories/prod -m shell -a \
  "if qm list 2>/dev/null | awk 'NR>1 {print \$1}' | grep -qx 154; then if qm reset 154; then r=ok; else r=err; fi; echo \"vmreset:\$r host=\$(hostname)\"; fi" 2>&1 || true)
echo "$reset_output"
# ha_reset_verdict checks err BEFORE ok and anchors both markers to line start
# (fail-closed on a split-brain that emitted both; the `^` anchor rejects a
# mid-line match such as an echoed command source). See maintenance-lib.sh.
reset_verdict=$(printf '%s\n' "$reset_output" | ha_reset_verdict)
case "$reset_verdict" in
  err)
    echo "ERROR: 'qm reset 154' ran on its host but FAILED (VM locked by a backup, in-flight migration, or QMP error)"
    exit 1
    ;;
  ok)
    echo "Home Assistant VM reset confirmed on its host."
    ;;
  *)
    # No vmreset token at all. Distinguish a genuine "VM not found" from an
    # op/ansible tooling or auth failure, which would otherwise be misreported
    # as a missing VM.
    tool_err=$(printf '%s\n' "$reset_output" | grep -E '\[ERROR\]|authoriz|UNREACHABLE!|FAILED!' || true)
    if [ -n "$tool_err" ]; then
      echo "ERROR: could not run 'qm reset 154' — op/ansible tooling or connectivity failure (see captured output above), not necessarily a missing VM"
    else
      echo "ERROR: VM 154 not found on any reachable Proxmox host (the node hosting 154 may be unreachable)"
    fi
    exit 1
    ;;
esac

echo "Waiting for Home Assistant to come back online (up to 5 min)..."
# Success requires an observed down-then-up transition, debounced on a
# 2-failure streak. A reset that never shows down is accepted after
# HA_SETTLE_SECS as UNVERIFIED and exits HA_UNVERIFIED_EXIT (default 0).
HA_SETTLE_SECS=60
HA_UNVERIFIED_EXIT="${HA_UNVERIFIED_EXIT:-0}"
# ha_state: pending | healthy (confirmed down-then-up) | unverified (never-down
# fast-reset settle path).
ha_state=pending
ha_went_down=false
ha_down_streak=0
ha_start=$(date +%s)
while true; do
  if curl -sf --max-time 5 -o /dev/null "$HA_URL" 2>/dev/null; then
    ha_probe=up
  else
    ha_probe=down
  fi
  # Advance the debounce state machine (down-streak>=2 latches ha_went_down;
  # an up probe after a confirmed down yields "healthy"). See maintenance-lib.sh.
  read -r ha_down_streak ha_went_down ha_verdict \
    < <(ha_observe_step "$ha_down_streak" "$ha_went_down" "$ha_probe")
  if [ "$ha_verdict" = healthy ]; then
    echo "  Home Assistant confirmed down-then-up after $(( $(date +%s) - ha_start ))s"
    ha_state=healthy
    break
  fi
  elapsed=$(( $(date +%s) - ha_start ))
  # Fast-reset settle path: never observed down and the settle window elapsed.
  # See ha_settle_verdict in maintenance-lib.sh — it fails closed if ANY
  # downtime was observed (that case is owned by the ha_observe_step verdict).
  if [ "$(ha_settle_verdict "$ha_went_down" "$ha_down_streak" "$elapsed" "$HA_SETTLE_SECS")" = healthy-unverified ]; then
    echo "  WARNING: VM 154 was never observed down within ${HA_SETTLE_SECS}s; the reset's effect was NOT verified (endpoint stayed up throughout). Treating as a probable fast reset, but recovery is UNVERIFIED."
    ha_state=unverified
    break
  fi
  if [ "$elapsed" -ge 300 ]; then break; fi
  [ $((elapsed % 30)) -lt 5 ] && echo "  still waiting (${elapsed}s)..."
  sleep 5
done
case "$ha_state" in
  healthy)
    ;;  # confirmed recovery, exit 0
  unverified)
    # Distinguishable from a confirmed recovery by the explicit UNVERIFIED label
    # (and optionally by exit code — see HA_UNVERIFIED_EXIT). The never-down path
    # did NOT prove the reset cycled the VM.
    echo "WARNING: Home Assistant reset completed without observed downtime — recovery UNVERIFIED (exit ${HA_UNVERIFIED_EXIT})"
    exit "$HA_UNVERIFIED_EXIT"
    ;;
  *)
    echo "ERROR: Home Assistant did not come back online within 5 min"
    exit 1
    ;;
esac
