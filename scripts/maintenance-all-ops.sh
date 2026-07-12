#!/usr/bin/env bash
# Run all 6 maintenance ops in canonical order. Aborts at the first
# failure (set -e). The caller (typically `maintenance-run-with-verify.sh`)
# still runs verify after this script exits, regardless of pass/fail.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../ansible"

echo "=== 1/6 OS package updates ==="
# self_reboot_delay: the executor pod can land on ANY agent (.maintenance-base
# pins no node selector) — either an opt-* host (no etcd member -> DETACHED path:
# update-packages arms a reboot that survives the job instead of a synchronous one
# that would kill it) OR an etcd-server host (pve-nas/laptop/prec -> DEFER path,
# operator-driven). _reboot-if-needed.yml routes both correctly, so the etcd-defer
# branch there is REQUIRED, not belt-and-suspenders. self_reboot_delay only applies
# to the DETACHED (opt-*) case below. The .maintenance-base after_script
# (maintenance-rearm-self-reboot.sh) RE-ARMS that reboot to +60s at job END and
# disarms this long timer, so the reboot is anchored to the real job end, not this
# fixed delay — 5400s is only the FALLBACK if the after_script never runs (runner
# crash). Sized to outlast ops 2-6 + verify (~30-45 min historically); a late
# fallback reboot is harmless, an early one is not — so raise it, never lower it.
# NOTE: a DETACHED reboot fires AFTER the in-script verify, so the executor's own
# opt-* host reboot is NOT validated by this run — the NEXT run's verify covers it
# (and its agent VM briefly going down, possibly alongside a kured agent reboot, is
# at most a transient 2-agent dip with no etcd/quorum impact).
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
