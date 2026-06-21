#!/usr/bin/env bash
# Restart the Home Assistant VM and wait for it to come back online.
# Replaces the prior fixed `sleep 30`, which would mark the job successful
# even if HA was still down.
#
# VM 154 (Home Assistant) is Proxmox-HA-managed. There is NO `ha-manager restart`
# subcommand (the prior version of this script used it and failed with
# "unknown command 'ha-manager restart'"). Restart it with `qm reset` — a hard
# reset HA tolerates (the VM stays on its node) and which, unlike `qm reboot`,
# avoids a QEMU Guest Agent timeout when HAOS lacks agent config (matches the
# home-assistant:vm-restart task). The command runs across ALL proxmox hosts;
# only the node whose `qm list` shows 154 acts (the VM may have HA-migrated off
# proxmox[0]).

set -euo pipefail
# Source the pure-logic helpers (token verdict + down-then-up debounce) before
# cd'ing away, resolving against this script's own directory.
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/maintenance-lib.sh
. "$_SCRIPT_DIR/maintenance-lib.sh"
cd "$_SCRIPT_DIR/../ansible"

echo "Restarting Home Assistant VM (vm:154) via qm reset on its current host..."
# Run across the whole proxmox group; only the node whose `qm list` shows 154
# acts (HA may have migrated it off proxmox[0]). Capture output with `|| true`
# so an UNRELATED unreachable host (common mid-maintenance, when nodes may be
# rebooting) does not abort this step under `set -e` — `ansible <group>` exits
# non-zero if ANY host is unreachable, even when the reset succeeded on 154's
# host.
#
# The remote snippet ALWAYS exits 0 and reports its outcome as a status token
# rather than letting `qm reset` failure fail the Ansible task. Why: a FAILED
# task echoes the command SOURCE back in its result dump, so a success marker
# that is a literal substring of that source would let grep match the echoed
# command instead of a real reset (false success — e.g. when `qm reset` fails
# because the VM is locked by a concurrent vzdump backup). The token is
# assembled at runtime (`vmreset:$r`), so the grepped strings `vmreset:ok` /
# `vmreset:err` never appear literally in the source — collision-proof even
# under `-v`, where successful tasks also dump cmd.
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
    echo "ERROR: VM 154 not found on any reachable Proxmox host (the node hosting 154 may be unreachable)"
    exit 1
    ;;
esac

echo "Waiting for Home Assistant to come back online (up to 5 min)..."
# Wait for an observed down-then-up transition. ha-manager restart returns
# immediately, and the HTTP endpoint can still respond for a few seconds
# before the VM actually goes down — without the down requirement we'd risk
# marking the job successful before the restart even took effect. Debounce
# `ha_went_down` with a 2-failure streak so a single transient curl error
# doesn't satisfy the down requirement on its own.
#
# A `qm reset` can be fast enough that we never catch the 2-failure down
# streak (the VM is back before two consecutive probes fail). In that case the
# endpoint stays healthy throughout and we NEVER observe the VM go down, so we
# cannot confirm the reset actually cycled it (the endpoint may simply have
# stayed up because the reset had no observable effect on the HTTP probe). To
# avoid timing out at 300s with a misleading "did not come back online" error,
# we accept this never-down path once a short settle window (HA_SETTLE_SECS)
# has elapsed — but we mark it UNVERIFIED so it is not reported identically to
# a confirmed down-then-up recovery.
#
# Exit semantics for the UNVERIFIED path are controlled by HA_UNVERIFIED_EXIT
# (default 0): the never-down fast reset is the EXPECTED, benign outcome of a
# quick `qm reset`, and this script runs as the last step of maintenance-all-ops
# under `set -e`, so a non-zero default would flip an entire routine maintenance
# run to FAILED. The path is therefore loudly LABELED as UNVERIFIED in the
# output (not silently equated with a verified recovery), and a caller that
# wants strict semantics can set HA_UNVERIFIED_EXIT=75 (EX_TEMPFAIL) to make
# the never-down run exit distinctly from both success and hard failure.
HA_SETTLE_SECS=60
HA_UNVERIFIED_EXIT="${HA_UNVERIFIED_EXIT:-0}"
# ha_state: pending | healthy (confirmed down-then-up) | unverified (never-down
# fast-reset settle path).
ha_state=pending
ha_went_down=false
ha_down_streak=0
ha_start=$(date +%s)
while true; do
  if curl -sf --max-time 5 -o /dev/null https://home.esweiss.com 2>/dev/null; then
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
