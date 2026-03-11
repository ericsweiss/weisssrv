#!/bin/bash
# Diagnostic script for network instability issues
# Run from workstation with SSH access to all nodes
#
# Note: We intentionally do NOT use 'set -e' here because we want the script
# to continue gathering diagnostics from reachable hosts even if some hosts
# are unreachable (which is useful during network troubleshooting).

echo "=== Network Diagnostics for weisssrv Cluster ==="
echo "Generated: $(date)"
echo ""

# Define hosts
PVE_HOSTS="192.168.0.102 192.168.0.103 192.168.0.104 192.168.0.105 192.168.0.106 192.168.0.107"
K3S_SERVERS="192.168.0.222 192.168.0.223 192.168.0.227"

# SSH options for timeout handling during network issues
SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

# Helper function to run commands with timeout
# Note: timeout is GNU coreutils and not available on stock macOS
# On macOS, install via 'brew install coreutils' for gtimeout
timeout_cmd() {
    local seconds="$1"
    shift
    if command -v timeout &>/dev/null; then
        timeout "$seconds" "$@"
    elif command -v gtimeout &>/dev/null; then
        # macOS with coreutils installed via Homebrew
        gtimeout "$seconds" "$@"
    else
        # Fallback: run without timeout (command may hang)
        "$@"
    fi
}

# Helper function to run SSH with timeout
ssh_cmd() {
    timeout_cmd 10 ssh $SSH_OPTS "$@"
}

# Helper function for cross-platform ping with timeout
# On Linux: -W is timeout in seconds
# On macOS/BSD: -W is timeout in milliseconds, -t is TTL (not timeout)
ping_check() {
    local host="$1"
    local timeout_sec="${2:-2}"
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: -W is timeout in milliseconds, so multiply seconds by 1000
        local timeout_ms=$((timeout_sec * 1000))
        ping -c 1 -W "$timeout_ms" "$host" &>/dev/null
    else
        # Linux: -W is timeout in seconds
        ping -c 1 -W "$timeout_sec" "$host" &>/dev/null
    fi
}

echo "========================================"
echo "1. PROXMOX CLUSTER STATE"
echo "========================================"
ssh_cmd eric@192.168.0.102 "sudo pvecm status 2>/dev/null || echo 'Cluster status unavailable'" || echo "Host unreachable"
echo ""

echo "========================================"
echo "2. HA MANAGER STATE"
echo "========================================"
ssh_cmd eric@192.168.0.102 "sudo ha-manager status 2>/dev/null || echo 'HA status unavailable'" || echo "Host unreachable"
echo ""

echo "========================================"
echo "3. ZFS REPLICATION STATUS"
echo "========================================"
ssh_cmd eric@192.168.0.102 "sudo pvesr status 2>/dev/null || echo 'Replication status unavailable'" || echo "Host unreachable"
echo ""

echo "========================================"
echo "4. NETWORK INTERFACE STATS (checking for errors/drops)"
echo "========================================"
for host in $PVE_HOSTS; do
    echo "--- $host ---"
    ssh_cmd eric@$host "ip -s link show | grep -A 4 'vmbr0\|eth0\|enp' 2>/dev/null | head -20" || echo "Host unreachable"
    echo ""
done

echo "========================================"
echo "5. ARP TABLE STATE (looking for churn)"
echo "========================================"
for host in $PVE_HOSTS; do
    echo "--- $host ARP entries for VIPs ---"
    ssh_cmd eric@$host "ip neigh show | grep -E '(\.100|\.101|\.150|\.151|\.154|\.160|\.161)'" 2>/dev/null || echo "No VIP entries or host unreachable"
    echo ""
done

echo "========================================"
echo "6. COROSYNC RING STATUS"
echo "========================================"
ssh_cmd eric@192.168.0.102 "sudo corosync-cfgtool -s 2>/dev/null || echo 'Corosync status unavailable'" || echo "Host unreachable"
echo ""

echo "========================================"
echo "7. KUBE-VIP LEADER STATUS"
echo "========================================"
if command -v kubectl &> /dev/null; then
    echo "Current kube-vip leader lease:"
    timeout_cmd 10 kubectl get lease -n kube-system plndr-cp-lock -o yaml 2>/dev/null | grep -E "holderIdentity|acquireTime|renewTime" || echo "Cannot query k3s (timeout or unreachable)"
    echo ""
    echo "kube-vip pod status:"
    timeout_cmd 10 kubectl get pods -n kube-system -l app.kubernetes.io/name=kube-vip -o wide 2>/dev/null || echo "Cannot query k3s (timeout or unreachable)"
else
    echo "kubectl not available on this host"
fi
echo ""

echo "========================================"
echo "8. METALLB SPEAKER STATUS"
echo "========================================"
if command -v kubectl &> /dev/null; then
    timeout_cmd 10 kubectl get pods -n metallb-system -o wide 2>/dev/null || echo "Cannot query k3s (timeout or unreachable)"
    echo ""
    echo "MetalLB speaker logs (last 10 lines each):"
    for pod in $(timeout_cmd 10 kubectl get pods -n metallb-system -l component=speaker -o name 2>/dev/null); do
        echo "--- $pod ---"
        timeout_cmd 10 kubectl logs $pod -n metallb-system --tail=10 2>/dev/null || echo "Cannot get logs"
    done
else
    echo "kubectl not available on this host"
fi
echo ""

echo "========================================"
echo "9. BRIDGE CONFIGURATION"
echo "========================================"
for host in $PVE_HOSTS; do
    echo "--- $host bridges ---"
    # Use ping to check reachability first, then fetch config
    # NOTE: Ping may be blocked by firewall on some networks. If all hosts show
    # "Host unreachable" but you know they're up, try running SSH directly:
    #   ssh eric@192.168.0.102 "cat /etc/network/interfaces | grep -A 10 'auto vmbr'"
    if ping_check "$host" 2; then
        ssh_cmd eric@$host "cat /etc/network/interfaces 2>/dev/null | grep -A 10 'auto vmbr'" || echo "SSH failed (host reachable but SSH error)"
    else
        # Try SSH anyway in case ICMP is blocked but host is reachable
        ssh_cmd eric@$host "cat /etc/network/interfaces 2>/dev/null | grep -A 10 'auto vmbr'" 2>/dev/null || echo "Host unreachable (ping failed, SSH failed)"
    fi
    echo ""
done

echo "========================================"
echo "10. RECENT KERNEL NETWORK ERRORS"
echo "========================================"
for host in $PVE_HOSTS; do
    echo "--- $host dmesg (last 5 network-related) ---"
    ssh_cmd eric@$host "sudo dmesg | grep -iE '(eth|network|arp|link|drop)' | tail -5" 2>/dev/null || echo "Host unreachable"
    echo ""
done

echo "========================================"
echo "11. K3S SERVER NODE STATUS"
echo "========================================"
for host in $K3S_SERVERS; do
    echo "--- $host ---"
    ssh_cmd eric@$host "sudo systemctl is-active k3s 2>/dev/null && sudo k3s kubectl get nodes 2>/dev/null | head -5" || echo "Host unreachable or k3s not running"
    echo ""
done

echo "========================================"
echo "12. K3S NODE STATUS (local kubectl)"
echo "========================================"
if command -v kubectl &> /dev/null; then
    timeout_cmd 10 kubectl get nodes -o wide 2>/dev/null || echo "Cannot query k3s (timeout or unreachable)"
else
    echo "kubectl not available"
fi
echo ""

echo "=== Diagnostics Complete ==="
