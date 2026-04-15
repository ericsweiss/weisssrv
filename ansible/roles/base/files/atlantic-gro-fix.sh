#!/bin/sh
# Managed by Ansible - Aquantia/Marvell AQC113 GRO workaround
# Disables GRO on atlantic driver NICs to prevent bridge forwarding hangs
# See: https://github.com/Aquantia/AQtion/issues/67

FAILURES=0
for iface in /sys/class/net/*; do
    iface=$(basename "$iface")
    [ "$iface" = "lo" ] && continue

    driver=$(ethtool -i "$iface" 2>/dev/null | grep "^driver:" | awk '{print $2}')
    if [ "$driver" = "atlantic" ]; then
        if ethtool -K "$iface" gro off 2>/dev/null; then
            echo "Disabled GRO on $iface (atlantic driver)"
        else
            echo "WARNING: Failed to disable GRO on $iface (atlantic driver)" >&2
            FAILURES=$((FAILURES + 1))
        fi
    fi
done
exit $FAILURES
