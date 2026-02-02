#!/usr/bin/env bash
# collect-state.sh - Collect cluster state with automatic secret redaction
# Usage: ./scripts/collect-state.sh [output_file]

set -euo pipefail

OUTPUT_FILE="${1:-cluster-state-$(date +%Y%m%d-%H%M%S).txt}"
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Hosts to collect from
PROXMOX_HOSTS=("pve-nas-01" "pve-opt-03")
DNS_HOSTS=("192.168.0.150" "192.168.0.160")  # dns-01, dns-02 (use IPs since hostnames resolve to VIP)
MAIL_HOSTS=("smtp-relay")
K3S_HOSTS=("k3s-srv-nas-01" "k3s-agt-nas-01" "k3s-agt-opt-03")
HOME_ASSISTANT_HOST="192.168.0.154"  # home (HAOS VM)

# Redaction patterns
REDACT_PATTERNS=(
    's/password\s*[:=]\s*\S+/password: <REDACTED>/gi'
    's/\btoken\s*[:=]\s*\S+/token: <REDACTED>/gi'
    's/access_token\s*[:=]\s*\S+/access_token: <REDACTED>/gi'
    's/refresh_token\s*[:=]\s*\S+/refresh_token: <REDACTED>/gi'
    's/id_token\s*[:=]\s*\S+/id_token: <REDACTED>/gi'
    's/bearer\s+[A-Za-z0-9._~+\/=-]+/Bearer <REDACTED>/gi'
    's/\bsecret\s*[:=]\s*\S+/secret: <REDACTED>/gi'
    's/CF_Token=\S+/CF_Token=<REDACTED>/g'
    's/CF_Account_ID=\S+/CF_Account_ID=<REDACTED>/g'
    's/SAVED_CF_Token=\S+/SAVED_CF_Token=<REDACTED>/g'
    's/SAVED_CF_Account_ID=\S+/SAVED_CF_Account_ID=<REDACTED>/g'
    's/\$2[aby]\$[0-9]+\$[A-Za-z0-9.\/]+/<BCRYPT_HASH>/g'
    's/client-certificate-data:\s*\S+/client-certificate-data: <REDACTED>/g'
    's/client-key-data:\s*\S+/client-key-data: <REDACTED>/g'
    's/certificate-authority-data:\s*\S+/certificate-authority-data: <REDACTED>/g'
    's/OPENVPN_USER=\S+/OPENVPN_USER=<REDACTED>/g'
    's/OPENVPN_PASSWORD=\S+/OPENVPN_PASSWORD=<REDACTED>/g'
    's/openvpn-user:\s*\S+/openvpn-user: <REDACTED>/g'
    's/openvpn-password:\s*\S+/openvpn-password: <REDACTED>/g'
    's/api-token:\s*\S+/api-token: <REDACTED>/g'
    's/oidc_client_id:\s*\S+/oidc_client_id: <REDACTED>/g'
    's/oidc_client_secret:\s*\S+/oidc_client_secret: <REDACTED>/g'
    's/client_id:\s*\S+/client_id: <REDACTED>/g'
    's/client_secret:\s*\S+/client_secret: <REDACTED>/g'
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
# Include all mounted filesystems, not just those starting with /
# LXC containers use rootfs/overlay paths that don't start with /
df -h | grep -vE '^(tmpfs|devtmpfs|udev|overlay$)' | head -20
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
echo "--- Firewall Guest Rules ---"
for vmid in 150 151 152 160 202 206 207; do
    if [ -f "/etc/pve/firewall/${vmid}.fw" ]; then
        echo "Guest ${vmid}:"
        sudo cat "/etc/pve/firewall/${vmid}.fw" 2>/dev/null || echo "  Cannot read"
    fi
done
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
if mount | grep -q mergerfs; then
    mount | grep mergerfs
    # Check for duplicates
    mount_count=$(mount | grep -c mergerfs)
    unique_count=$(mount | grep mergerfs | sort -u | wc -l)
    if [ "$mount_count" -gt "$unique_count" ]; then
        echo ""
        echo "WARNING: Duplicate MergerFS mount entries detected!"
        echo "  Total entries: $mount_count"
        echo "  Unique entries: $unique_count"
        echo "  This indicates the filesystem is mounted multiple times."
    fi
else
    echo "No MergerFS mounts"
fi
if mount | grep -q mergerfs; then
    echo "MergerFS union details:"
    df -h | grep -E 'media|Filesystem' || true
    echo ""
    echo "MergerFS fstab options (authoritative - runtime options not queryable):"
    # NOTE: MergerFS mount-time options (inodecalc, noforget, use_ino, etc.)
    # are NOT visible in mount output or via xattrs. Only fstab is authoritative.
    for mnt in $(mount | grep "type fuse.mergerfs" | awk '{print $3}' | sort -u); do
        echo "  $mnt:"
        fstab_line=$(grep "^[^#].*[[:space:]]${mnt}[[:space:]]" /etc/fstab 2>/dev/null)
        fstab_opts=$(echo "$fstab_line" | awk '{print $4}')
        fstab_src=$(echo "$fstab_line" | awk '{print $1}')
        if [ -n "$fstab_opts" ]; then
            # Check if this is a bind mount (inherits settings from source)
            if [ "$fstab_opts" = "bind" ] || echo "$fstab_opts" | grep -qE '^bind,|,bind,|,bind$'; then
                echo "    type: BIND MOUNT (inherits MergerFS options from source)"
                echo "    source: $fstab_src"
                echo "    full opts: $fstab_opts"
            else
                # Check critical NFS options for actual MergerFS mounts
                has_inodecalc="NO"
                has_noforget="NO"
                echo "$fstab_opts" | grep -q "inodecalc=path-hash" && has_inodecalc="YES"
                echo "$fstab_opts" | grep -q "noforget" && has_noforget="YES"
                echo "    inodecalc=path-hash: $has_inodecalc"
                echo "    noforget: $has_noforget"
                echo "    full opts: $fstab_opts"
            fi
        else
            echo "    WARNING: No fstab entry found"
        fi
        # Check if .mergerfs control file exists (confirms MergerFS is active)
        control_file="${mnt}/.mergerfs"
        if [ -e "$control_file" ]; then
            echo "    control file: present (MergerFS active)"
        else
            echo "    control file: MISSING (mount may not be MergerFS)"
        fi
    done
fi
echo ""
echo "--- Media Mover Status ---"
systemctl is-active media-mover.timer 2>/dev/null || echo "media-mover timer not active"
systemctl status media-mover.timer 2>/dev/null | grep -E 'Active:|Trigger:' || true
journalctl -u media-mover.service --since "1 day ago" --no-pager | tail -10 2>/dev/null || echo "No recent media-mover logs"
echo ""
echo "--- LXC Containers ---"
sudo pct list 2>/dev/null || echo "No LXC containers"
echo ""
echo "--- Plex LXC Status (VMID 152) ---"
if sudo pct status 152 &>/dev/null; then
    sudo pct status 152
    echo "Bind mounts:"
    sudo grep "^mp" /etc/pve/lxc/152.conf 2>/dev/null || echo "No bind mounts configured"
    echo "Plex service (inside container):"
    sudo pct exec 152 -- systemctl is-active plexmediaserver 2>/dev/null || echo "Cannot check Plex service"
else
    echo "Plex LXC (152) not found"
fi
echo ""
echo "--- Home Assistant VM Status (VMID 154) ---"
if sudo qm status 154 &>/dev/null; then
    sudo qm status 154
    echo "VM Config:"
    sudo qm config 154 2>/dev/null | grep -E 'cores|memory|net0|boot|onboot|startup' || echo "Cannot read config"
    echo "Network:"
    sudo qm guest cmd 154 network-get-interfaces 2>/dev/null | grep -E 'ip-address|name' || echo "Guest agent unavailable"
else
    echo "Home Assistant VM (154) not found"
fi
echo ""
echo "--- Postfix Status ---"
if systemctl is-active postfix &>/dev/null; then
    echo "postfix: active"
    # postconf requires sudo on Proxmox hosts (null client configs)
    sudo postconf myhostname relayhost 2>/dev/null || echo "(postconf unavailable)"
else
    echo "postfix: not installed or inactive"
fi
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
sudo unbound-checkconf 2>&1 | head -5
echo ""
echo "--- AdGuard Home Status ---"
systemctl is-active AdGuardHome
ls -la /opt/AdGuardHome/ | head -10
echo ""
echo "--- AdGuard User Rules Count ---"
# User rules are stored in the user_rules array in the YAML
user_rules_count=$(sudo grep -E -c '^[[:space:]]*-.*dnsrewrite|^[[:space:]]*-.*@@' /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null || echo "0")
echo "User rules: $user_rules_count"
echo ""
echo "--- AdGuard Rewrites Count ---"
# Rewrites are stored under dns.rewrites in the YAML, count entries with '- domain:' key
rewrites_count=$(sudo grep -E -c '^[[:space:]]*- domain:' /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null || echo "0")
echo "DNS rewrites: $rewrites_count"
echo ""
echo "--- AdGuard DHCP Status ---"
sudo grep "dhcp:" -A 2 /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null | grep enabled || echo "DHCP config not found"
echo ""
echo "--- Listening Ports ---"
ss -lntup | grep -E ':53|:853|:443|:3000|:5335'
echo ""
echo "--- Cert Files ---"
sudo ls -la /opt/AdGuardHome/certs/ 2>/dev/null || echo "No certs"
sudo stat /opt/AdGuardHome/certs/*.pem 2>/dev/null | grep -E 'File:|Modify:' || true
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
    echo ""
    echo "--- Authentik Status ---"
    sudo k3s kubectl get pods -n authentik 2>/dev/null || echo "Authentik not deployed"
    sudo k3s kubectl get svc -n authentik 2>/dev/null || echo ""
    sudo k3s kubectl get ingressroute -n authentik 2>/dev/null || echo ""
    sudo k3s kubectl get middleware -n authentik 2>/dev/null || echo ""
    echo ""
    echo "--- Downloads Namespace ---"
    if sudo k3s kubectl get namespace downloads &>/dev/null; then
        echo "Pods:"
        sudo k3s kubectl get pods -n downloads -o wide 2>/dev/null || echo "No pods"
        echo ""
        echo "Services:"
        sudo k3s kubectl get svc -n downloads 2>/dev/null || echo "No services"
        echo ""
        echo "PVCs:"
        sudo k3s kubectl get pvc -n downloads 2>/dev/null || echo "No PVCs"
        echo ""
        echo "IngressRoutes:"
        sudo k3s kubectl get ingressroute -n downloads 2>/dev/null || echo "No IngressRoutes"
        echo ""
        echo "VPN ConfigMaps:"
        for app in nzbget qbittorrent; do
            if sudo k3s kubectl get configmap "${app}-vpn-config" -n downloads &>/dev/null; then
                echo "${app}:"
                sudo k3s kubectl get configmap "${app}-vpn-config" -n downloads -o jsonpath='{.data}' 2>/dev/null
                echo ""
            fi
        done
        echo ""
        echo "Gluetun VPN Status (public IP check):"
        for app in nzbget qbittorrent; do
            vpn_enabled=$(sudo k3s kubectl get configmap "${app}-vpn-config" -n downloads -o jsonpath='{.data.vpn_enabled}' 2>/dev/null || echo "unknown")
            if [ "$vpn_enabled" = "true" ]; then
                echo "${app} VPN: enabled"
                # Use checkip.amazonaws.com - reliable and doesn't block VPN IPs
                # ipinfo.io returns 403 Forbidden for VPN/datacenter IPs
                public_ip=$(sudo k3s kubectl exec -n downloads deployment/"${app}" -c gluetun -- wget -qO- --timeout=10 http://checkip.amazonaws.com 2>/dev/null || echo "unreachable")
                echo "${app} public IP: ${public_ip}"
            else
                echo "${app} VPN: disabled"
            fi
        done
    else
        echo "Downloads namespace not deployed"
    fi
    echo ""
    echo "--- Recipes Namespace ---"
    if sudo k3s kubectl get namespace recipes &>/dev/null; then
        echo "Pods:"
        sudo k3s kubectl get pods -n recipes -o wide 2>/dev/null || echo "No pods"
        echo ""
        echo "Services:"
        sudo k3s kubectl get svc -n recipes 2>/dev/null || echo "No services"
        echo ""
        echo "PVCs:"
        sudo k3s kubectl get pvc -n recipes 2>/dev/null || echo "No PVCs"
        echo ""
        echo "IngressRoutes:"
        sudo k3s kubectl get ingressroute -n recipes 2>/dev/null || echo "No IngressRoutes"
        echo ""
        echo "Deployments:"
        sudo k3s kubectl get deployments -n recipes 2>/dev/null || echo "No deployments"
        echo ""
        echo "App Versions:"
        for app in mealie mealie-postgres bar-assistant bar-assistant-redis bar-assistant-meilisearch salt-rim; do
            image=$(sudo k3s kubectl get deployment "$app" -n recipes -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "not found")
            echo "  $app: $image"
        done
        echo ""
        echo "Secrets (names only):"
        sudo k3s kubectl get secrets -n recipes 2>/dev/null || echo "No secrets"
    else
        echo "Recipes namespace not deployed"
    fi
    echo ""
    echo "--- Home Assistant (IngressRoutes and Services) ---"
    echo "IngressRoutes:"
    sudo k3s kubectl get ingressroute -A 2>/dev/null | grep -E "NAME|home-assistant" || echo "No Home Assistant IngressRoutes"
    echo ""
    echo "Service:"
    sudo k3s kubectl get svc home-assistant-backend 2>/dev/null || echo "Service not found"
    echo ""
    echo "EndpointSlice:"
    sudo k3s kubectl get endpointslice home-assistant-backend 2>/dev/null || echo "EndpointSlice not found"
    echo ""
    echo "Middleware:"
    sudo k3s kubectl get middleware -n traefik 2>/dev/null | grep -E "NAME|home-assistant" || echo "No Home Assistant middleware"
fi
echo ""
echo "--- K3s Config ---"
if [ -f /etc/rancher/k3s/config.yaml ]; then
    sudo cat /etc/rancher/k3s/config.yaml 2>/dev/null || echo "Cannot read config file (permission denied)"
else
    echo "No config file (k3s using defaults or command-line args)"
    echo "Note: Re-run 'task k3s:deploy' to create config from Ansible"
fi
echo ""
echo "--- NFS Mounts ---"
mount | grep nfs || echo "No NFS mounts on this node"
echo ""
echo "--- Disk Usage ---"
df -h | grep -E '^/|Filesystem'
echo ""
echo "--- Tailscale ---"
sudo tailscale status 2>/dev/null | head -5 || echo "No tailscale"
echo ""
EOF
}

collect_home_assistant() {
    local host=$1
    echo "=== Home Assistant (HAOS VM): $host ==="

    # Note: Home Assistant OS VM status collected from Proxmox host
    # Configuration files collected via SSH if SSH add-on is configured

    # Check if SSH is accessible (requires SSH add-on on port 22222)
    if ssh -o ConnectTimeout=5 -p 22222 "root@${host}" "echo test" &>/dev/null; then
        ssh -o ConnectTimeout=10 -p 22222 "root@${host}" bash << 'EOF' 2>/dev/null || echo "SSH command failed"
echo "--- Home Assistant System Info ---"
ha info 2>/dev/null || echo "ha CLI unavailable"
echo ""
echo "--- Home Assistant Core Status ---"
ha core info 2>/dev/null || echo "Core info unavailable"
echo ""
echo "--- Home Assistant Supervisor ---"
ha supervisor info 2>/dev/null || echo "Supervisor info unavailable"
echo ""
echo "--- Add-ons ---"
ha addons 2>/dev/null || echo "Add-ons list unavailable"
echo ""
echo "--- Configuration Files ---"
ls -la /config/*.yaml 2>/dev/null | head -20 || echo "Config directory inaccessible"
echo ""
echo "--- Configuration Check ---"
ha core check 2>/dev/null || echo "Config check unavailable"
echo ""
echo "--- Custom Components ---"
ls -la /config/custom_components/ 2>/dev/null || echo "No custom components or directory inaccessible"
echo ""
echo "--- Recent Logs (last 20 lines) ---"
tail -20 /config/home-assistant.log 2>/dev/null || echo "Log file inaccessible"
echo ""
echo "--- Network Info ---"
ha network info 2>/dev/null || echo "Network info unavailable"
echo ""
EOF
    else
        echo "SSH not accessible (port 22222)"
        echo "Requires SSH add-on to be installed and configured"
        echo "Collecting VM status from Proxmox instead..."
    fi
    echo ""
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

    # Home Assistant
    collect_home_assistant "$HOME_ASSISTANT_HOST"
    echo ""

} > "$TEMP_DIR/raw.txt"

# Apply redaction
redact "$(cat "$TEMP_DIR/raw.txt")" > "$OUTPUT_FILE"

echo ""
echo "State collection complete: $OUTPUT_FILE"
echo "File size: $(wc -c < "$OUTPUT_FILE") bytes"
