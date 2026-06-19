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
cd "$(dirname "$0")/../ansible"

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
# Check err BEFORE ok, and anchor both markers to line start. In a healthy
# cluster only the single hosting node emits a token, but if a split-brain
# state ever emitted both, treat any failure as a failure (fail-closed). The
# `^` anchor rejects an accidental mid-line match (the real markers print at
# column 0 after Ansible's `>>`).
if echo "$reset_output" | grep -qE "^vmreset:err host="; then
  echo "ERROR: 'qm reset 154' ran on its host but FAILED (VM locked by a backup, in-flight migration, or QMP error)"
  exit 1
elif echo "$reset_output" | grep -qE "^vmreset:ok host="; then
  echo "Home Assistant VM reset confirmed on its host."
else
  echo "ERROR: VM 154 not found on any reachable Proxmox host (the node hosting 154 may be unreachable)"
  exit 1
fi

echo "Waiting for Home Assistant to come back online (up to 5 min)..."
# Wait for an observed down-then-up transition. ha-manager restart returns
# immediately, and the HTTP endpoint can still respond for a few seconds
# before the VM actually goes down — without the down requirement we'd risk
# marking the job successful before the restart even took effect. Debounce
# `ha_went_down` with a 2-failure streak so a single transient curl error
# doesn't satisfy the down requirement on its own.
ha_healthy=false
ha_went_down=false
ha_down_streak=0
ha_start=$(date +%s)
while true; do
  if curl -sf --max-time 5 -o /dev/null https://home.esweiss.com 2>/dev/null; then
    ha_down_streak=0
    if [ "$ha_went_down" = true ]; then
      echo "  Home Assistant healthy after $(( $(date +%s) - ha_start ))s"
      ha_healthy=true
      break
    fi
  else
    ha_down_streak=$((ha_down_streak + 1))
    if [ "$ha_down_streak" -ge 2 ]; then
      ha_went_down=true
    fi
  fi
  elapsed=$(( $(date +%s) - ha_start ))
  if [ "$elapsed" -ge 300 ]; then break; fi
  [ $((elapsed % 30)) -lt 5 ] && echo "  still waiting (${elapsed}s)..."
  sleep 5
done
if [ "$ha_healthy" != "true" ]; then
  echo "ERROR: Home Assistant did not come back online within 5 min"
  exit 1
fi
