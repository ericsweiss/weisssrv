#!/usr/bin/env bash
# Run all 6 maintenance ops in canonical order. Aborts at the first
# failure (set -e). The caller (typically `maintenance-run-with-verify.sh`)
# still runs verify after this script exits, regardless of pass/fail.

set -eo pipefail
cd "$(dirname "$0")/../ansible"

echo "=== 1/6 OS package updates ==="
op run -- ansible-playbook -i inventories/prod playbooks/maintenance/update-packages.yml -e auto_reboot=true
echo ""

echo "=== 2/6 Application updates (AdGuard, Tailscale, Plex) ==="
op run -- ansible-playbook -i inventories/prod playbooks/maintenance/update-applications.yml
echo ""

echo "=== 3/6 K3s node rolling update ==="
op run -- ansible-playbook -i inventories/prod playbooks/maintenance/update-k3s-nodes.yml
echo ""

echo "=== 4/6 K3s VM provisioning ==="
op run -- ansible-playbook -i inventories/prod playbooks/k3s-provision-vms.yml
op run -- ansible-playbook -i inventories/prod playbooks/k3s.yml
echo ""

echo "=== 5/6 Proxmox HA configuration ==="
op run -- ansible-playbook -i inventories/prod playbooks/proxmox-ha.yml
echo ""

echo "=== 6/6 Home Assistant restart ==="
# `'proxmox[0]'` quoted so bash globbing doesn't expand the host pattern
# before ansible sees it.
op run -- ansible "proxmox[0]" -i inventories/prod -m shell -a "ha-manager restart vm:154"

# Readiness loop instead of a fixed sleep — VM startup time varies (cold
# reboot vs warm restart) and a hard 30s often isn't enough. Require an
# observed down-then-up transition so we don't mark success on the first
# curl before the restart has actually taken effect. Debounce the down
# detection with a 2-failure streak so one transient curl error doesn't
# count as "went down" on its own.
echo "Waiting for Home Assistant to come back online (up to 5 min)..."
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

echo ""
echo "=== All 6 maintenance ops complete ==="
