#!/usr/bin/env bash
# Restart the Home Assistant VM via ha-manager and wait for it to come
# back online. Replaces the prior fixed `sleep 30`, which would mark
# the job successful even if HA was still down.
#
# VM 154 is Proxmox-HA-managed; targeting any proxmox node works because
# ha-manager is cluster-aware and forwards the request to the node
# currently hosting the VM. `'proxmox[0]'` is quoted so bash globbing
# here doesn't expand the host pattern before ansible parses it.

set -eo pipefail
cd "$(dirname "$0")/../ansible"

echo "Restarting Home Assistant VM..."
op run -- ansible "proxmox[0]" -i inventories/prod -m shell -a "ha-manager restart vm:154"

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
