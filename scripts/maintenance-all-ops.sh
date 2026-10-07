#!/usr/bin/env bash
# Run all 6 maintenance ops in canonical order. Aborts at the first
# failure (set -e). The caller (typically `maintenance-run-with-verify.sh`)
# still runs verify after this script exits, regardless of pass/fail.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../ansible"

echo "=== 1/6 OS package updates ==="
# CRITICAL: self_reboot_delay is the fallback reboot for a runner crash; the
# after_script (maintenance-rearm-self-reboot.sh) re-arms it to +60s at job end.
# Raise it, never lower it — an early fallback reboot kills the executor
# mid-run. It is sized to outlast ops 2-6 plus verify.
# The detached reboot arms only on an opt-* host (no etcd member); on an
# etcd-server host _reboot-if-needed.yml defers to the operator.
op run -- ansible-playbook -i inventories/prod playbooks/maintenance/update-packages.yml \
  -e auto_reboot=true -e self_reboot_delay=5400
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
# (single implementation).
bash "$SCRIPT_DIR/maintenance-ha-restart.sh"

echo ""
echo "=== All 6 maintenance ops complete ==="
