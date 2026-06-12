#!/usr/bin/env bash
# Run all 6 maintenance ops in canonical order. Aborts at the first
# failure (set -e). The caller (typically `maintenance-run-with-verify.sh`)
# still runs verify after this script exits, regardless of pass/fail.

set -eo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../ansible"

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
# Restart + down-then-up readiness wait live in maintenance-ha-restart.sh
# (single implementation; this used to be a verbatim copy).
bash "$SCRIPT_DIR/maintenance-ha-restart.sh"

echo ""
echo "=== All 6 maintenance ops complete ==="
