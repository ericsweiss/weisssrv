#!/bin/sh
# Managed by Ansible - Aquantia/Marvell AQC113 GRO workaround
# Disables GRO on atlantic driver NICs to prevent bridge forwarding hangs
# See: https://github.com/Aquantia/AQtion/issues/67

# Log-only on per-NIC failure (exit 0) so a transient ethtool error on one
# interface does not mark the boot oneshot unit failed/degraded — matching the
# e1000e-tso-fix.sh `|| true` tolerance.
for iface in /sys/class/net/*; do
    iface=$(basename "$iface")
    [ "$iface" = "lo" ] && continue

    driver=$(ethtool -i "$iface" 2>/dev/null | grep "^driver:" | awk '{print $2}')
    if [ "$driver" = "atlantic" ]; then
        if ethtool -K "$iface" gro off 2>/dev/null; then
            echo "Disabled GRO on $iface (atlantic driver)"
        else
            echo "WARNING: Failed to disable GRO on $iface (atlantic driver)" >&2
        fi
    fi
done
exit 0
