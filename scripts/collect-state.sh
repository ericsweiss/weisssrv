#!/usr/bin/env bash
# collect-state.sh - Collect cluster state with automatic secret redaction
# Usage: ./scripts/collect-state.sh [output_file]

set -euo pipefail

OUTPUT_FILE="${1:-cluster-state-$(date +%Y%m%d-%H%M%S).txt}"
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Hosts to collect from
PROXMOX_HOSTS=("pve-nas-01" "pve-opt-03")
DNS_HOSTS=("dns-01" "dns-02")
MAIL_HOSTS=("smtp-relay")
K3S_HOSTS=("k3s-srv-nas-01" "k3s-agt-nas-01" "k3s-agt-opt-03")

# Redaction patterns
REDACT_PATTERNS=(
    's/password\s*[:=]\s*\S+/password: <REDACTED>/gi'
    's/\btoken\s*[:=]\s*\S+/token: <REDACTED>/gi'
    's/\bsecret\s*[:=]\s*\S+/secret: <REDACTED>/gi'
    's/CF_Token=\S+/CF_Token=<REDACTED>/g'
    's/CF_Account_ID=\S+/CF_Account_ID=<REDACTED>/g'
    's/SAVED_CF_Token=\S+/SAVED_CF_Token=<REDACTED>/g'
    's/SAVED_CF_Account_ID=\S+/SAVED_CF_Account_ID=<REDACTED>/g'
    's/\$2[aby]\$[0-9]+\$[A-Za-z0-9.\/]+/<BCRYPT_HASH>/g'
    's/client-certificate-data:\s*\S+/client-certificate-data: <REDACTED>/g'
    's/client-key-data:\s*\S+/client-key-data: <REDACTED>/g'
    's/certificate-authority-data:\s*\S+/certificate-authority-data: <REDACTED>/g'
)

redact() {
    local input="$1"
    for pattern in "${REDACT_PATTERNS[@]}"; do
        input=$(echo "$input" | sed -E "$pattern")
    done
    echo "$input"
}

collect_host() {
    local host=$1
    local user=${2:-eric}
    echo "=== Collecting from $host ==="

    ssh -o ConnectTimeout=10 "${user}@${host}" bash << 'EOF' 2>/dev/null || echo "Failed to connect to $host"
echo "=== $HOSTNAME - $(date -Iseconds) ==="
echo ""
echo "--- System Info ---"
uname -a
hostname -f
uptime
echo ""
echo "--- Network ---"
ip -4 addr show | grep -E 'inet|^[0-9]'
echo ""
echo "--- Services ---"
systemctl list-units --type=service --state=running --no-pager | head -30
echo ""
echo "--- Disk Usage ---"
df -h | grep -E '^/|Filesystem'
echo ""
EOF
}

collect_proxmox() {
    local host=$1
    echo "=== Proxmox-specific: $host ==="

    ssh -o ConnectTimeout=10 "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
echo "--- Proxmox Version ---"
pveversion 2>/dev/null || echo "Not a Proxmox host"
echo ""
echo "--- Cluster Status ---"
sudo pvecm status 2>/dev/null | grep -E 'Name:|Nodes:|Quorate:' || echo "No cluster"
echo ""
echo "--- Firewall Status ---"
sudo pve-firewall status 2>/dev/null || echo "No firewall"
echo ""
echo "--- Firewall IP Sets ---"
sudo cat /etc/pve/firewall/cluster.fw 2>/dev/null | grep -A 20 '\[IPSET' | head -100 || echo "No firewall config"
echo ""
echo "--- ZFS Pools ---"
zpool list 2>/dev/null || echo "No ZFS"
echo ""
echo "--- ZFS Pool Health (All Pools) ---"
for pool in tank ssd nvme archive; do
    if zpool list "$pool" &>/dev/null; then
        echo "Pool: $pool"
        zpool status "$pool" 2>/dev/null | grep -E 'state:|scan:|errors:' || true
    fi
done
echo ""
echo "--- ZFS Datasets ---"
zfs list -o name,mountpoint,used,avail 2>/dev/null | head -50 || echo "No ZFS"
echo ""
echo "--- SMART Status ---"
sudo systemctl is-active smartd 2>/dev/null || echo "smartd not active"
echo ""
echo "--- NFS Exports ---"
sudo cat /etc/exports 2>/dev/null || echo "No exports"
echo ""
echo "--- Samba Status ---"
systemctl is-active smbd 2>/dev/null || echo "No Samba"
sudo testparm -s 2>/dev/null | grep -A 5 '\[' | head -20 || true
echo ""
echo "--- MergerFS Mounts ---"
mount | grep mergerfs || echo "No MergerFS mounts"
if mount | grep -q mergerfs; then
    echo "MergerFS union details:"
    df -h | grep -E 'media|Filesystem' || true
fi
echo ""
echo "--- Media Mover Status ---"
systemctl is-active media-mover.timer 2>/dev/null || echo "media-mover timer not active"
systemctl status media-mover.timer 2>/dev/null | grep -E 'Active:|Trigger:' || true
journalctl -u media-mover.service --since "1 day ago" --no-pager | tail -10 2>/dev/null || echo "No recent media-mover logs"
echo ""
echo "--- Postfix Status ---"
postconf myhostname relayhost 2>/dev/null || echo "No postfix"
echo ""
echo "--- Tailscale ---"
sudo tailscale status 2>/dev/null | head -5 || echo "No tailscale"
echo ""
echo "--- Oh My Zsh Plugins ---"
grep "^plugins=" ~/.zshrc 2>/dev/null || echo "No zsh config"
echo ""
EOF
}

collect_dns() {
    local host=$1
    echo "=== DNS-specific: $host ==="

    ssh -o ConnectTimeout=10 "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
echo "--- Unbound Status ---"
systemctl is-active unbound
unbound-checkconf 2>&1 | head -5
echo ""
echo "--- AdGuard Home Status ---"
systemctl is-active AdGuardHome
ls -la /opt/AdGuardHome/ | head -10
echo ""
echo "--- AdGuard User Rules Count ---"
grep -c "user_rules:" /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null || echo "No config"
echo "First 5 user rules:"
grep -A 5 "user_rules:" /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null | head -6 || echo "None"
echo ""
echo "--- AdGuard Rewrites Count ---"
grep -c "answer:" /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null || echo "No rewrites"
echo ""
echo "--- AdGuard DHCP Status ---"
grep "dhcp:" -A 2 /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null | grep enabled || echo "DHCP config not found"
echo ""
echo "--- Listening Ports ---"
ss -lntup | grep -E ':53|:853|:443|:3000|:5335'
echo ""
echo "--- Cert Files ---"
ls -la /opt/AdGuardHome/certs/ 2>/dev/null || echo "No certs"
stat /opt/AdGuardHome/certs/*.pem 2>/dev/null | grep -E 'File:|Modify:' || true
echo ""
echo "--- acme.sh Status (dns-01 only) ---"
if [ "$(hostname)" = "dns-01" ]; then
    sudo /root/.acme.sh/acme.sh --list 2>/dev/null || echo "No acme.sh"
fi
echo ""
EOF
}

collect_mail() {
    local host=$1
    echo "=== Mail-specific: $host ==="

    ssh -o ConnectTimeout=10 "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
echo "--- Postfix Status ---"
systemctl is-active postfix
postconf myhostname mynetworks relayhost 2>/dev/null
echo ""
echo "--- Listening Ports ---"
ss -lntp | grep -E ':25|:587'
echo ""
echo "--- TLS Certs ---"
ls -la /etc/postfix/tls/ 2>/dev/null || echo "No TLS dir"
echo ""
EOF
}

collect_k3s() {
    local host=$1
    echo "=== K3s-specific: $host ==="

    ssh -o ConnectTimeout=10 "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
echo "--- K3s Service Status ---"
if systemctl is-active k3s &>/dev/null; then
    echo "k3s server: active"
    systemctl status k3s 2>/dev/null | grep -E 'Active:|Main PID:' || true
elif systemctl is-active k3s-agent &>/dev/null; then
    echo "k3s agent: active"
    systemctl status k3s-agent 2>/dev/null | grep -E 'Active:|Main PID:' || true
else
    echo "k3s: not running"
fi
echo ""
echo "--- K3s Version ---"
k3s --version 2>/dev/null || echo "k3s not installed"
echo ""
echo "--- Node Info (server only) ---"
if systemctl is-active k3s &>/dev/null; then
    sudo k3s kubectl get nodes -o wide 2>/dev/null || echo "Cannot get nodes"
    echo ""
    echo "--- Pod Status (server only) ---"
    sudo k3s kubectl get pods -A 2>/dev/null | head -30 || echo "Cannot get pods"
    echo ""
    echo "--- Services (server only) ---"
    sudo k3s kubectl get svc -A 2>/dev/null | head -20 || echo "Cannot get services"
    echo ""
    echo "--- kube-vip Status ---"
    sudo k3s kubectl get pods -n kube-system 2>/dev/null | grep kube-vip || echo "kube-vip not found"
    echo ""
    echo "--- MetalLB Status ---"
    sudo k3s kubectl get pods -n metallb-system 2>/dev/null || echo "MetalLB not deployed"
    echo ""
    echo "--- Traefik Status ---"
    sudo k3s kubectl get pods,svc -n traefik 2>/dev/null || echo "Traefik not deployed"
fi
echo ""
echo "--- K3s Config ---"
cat /etc/rancher/k3s/config.yaml 2>/dev/null || echo "No config file"
echo ""
echo "--- Disk Usage ---"
df -h | grep -E '^/|Filesystem'
echo ""
echo "--- Tailscale ---"
sudo tailscale status 2>/dev/null | head -5 || echo "No tailscale"
echo ""
EOF
}

echo "Collecting cluster state..."
echo "Output will be saved to: $OUTPUT_FILE"
echo ""

{
    echo "# Cluster State Collection"
    echo "# Generated: $(date -Iseconds)"
    echo "# Redacted: Yes"
    echo ""

    for host in "${PROXMOX_HOSTS[@]}"; do
        collect_host "$host"
        collect_proxmox "$host"
        echo ""
    done

    for host in "${DNS_HOSTS[@]}"; do
        collect_host "$host" "eric"
        collect_dns "$host"
        echo ""
    done

    for host in "${MAIL_HOSTS[@]}"; do
        collect_host "$host" "eric"
        collect_mail "$host"
        echo ""
    done

    for host in "${K3S_HOSTS[@]}"; do
        collect_host "$host"
        collect_k3s "$host"
        echo ""
    done

} > "$TEMP_DIR/raw.txt"

# Apply redaction
redact "$(cat "$TEMP_DIR/raw.txt")" > "$OUTPUT_FILE"

echo ""
echo "State collection complete: $OUTPUT_FILE"
echo "File size: $(wc -c < "$OUTPUT_FILE") bytes"
