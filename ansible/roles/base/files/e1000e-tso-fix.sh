#!/bin/sh
# Managed by Ansible - Intel e1000e Hardware Unit Hang workaround
# Disables TSO/GSO/GRO on Intel e1000e NICs to prevent driver hangs

for iface in /sys/class/net/*; do
    iface=$(basename "$iface")
    [ "$iface" = "lo" ] && continue

    driver=$(ethtool -i "$iface" 2>/dev/null | grep "^driver:" | awk '{print $2}')
    if [ "$driver" = "e1000e" ]; then
        ethtool -K "$iface" tso off gso off gro off 2>/dev/null || true
        echo "Disabled TSO/GSO/GRO on $iface"
    fi
done
