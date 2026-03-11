#!/usr/bin/env bash
# collect-state.sh - Collect cluster state with automatic secret redaction
# Usage: ./scripts/collect-state.sh [output_file]

set -euo pipefail

OUTPUT_FILE="${1:-cluster-state-$(date +%Y%m%d-%H%M%S).txt}"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Hosts to collect from
# 6-node Proxmox cluster
PROXMOX_HOSTS=("pve-nas-01" "pve-laptop-01" "pve-opt-01" "pve-opt-02" "pve-opt-03" "pve-prec-01")
DNS_HOSTS=("192.168.0.150" "192.168.0.160")  # dns-01, dns-02 (use IPs since hostnames resolve to VIP)
MAIL_HOSTS=("smtp-relay")
GITLAB_HOST="gitlab"  # 192.168.0.153 (gitlab VM on pve-nas-01)
# 9-node k3s cluster (3 servers + 6 agents)
K3S_HOSTS=("k3s-srv-nas-01" "k3s-srv-laptop-01" "k3s-srv-prec-01" "k3s-agt-nas-01" "k3s-agt-laptop-01" "k3s-agt-opt-01" "k3s-agt-opt-02" "k3s-agt-opt-03" "k3s-agt-prec-01")
HOME_ASSISTANT_HOST="192.168.0.154"  # home (HAOS VM)

# Redaction patterns using POSIX-compatible character classes
# Note: Use [[:space:]] instead of \s and [^[:space:]] instead of \S for portability
# across BSD sed (macOS) and GNU sed (Linux)
# SECURITY: Patterns use (^|[^[:alnum:]]) to match at line start OR after non-alphanumeric
# This ensures patterns like "token: secret123" match even at the start of a line
REDACT_PATTERNS=(
    's/password[[:space:]]*[:=][[:space:]]*[^[:space:]]+/password: <REDACTED>/gi'
    's/(^|[^[:alnum:]])token[[:space:]]*[:=][[:space:]]*[^[:space:]]+/\1token: <REDACTED>/gi'
    's/access_token[[:space:]]*[:=][[:space:]]*[^[:space:]]+/access_token: <REDACTED>/gi'
    's/refresh_token[[:space:]]*[:=][[:space:]]*[^[:space:]]+/refresh_token: <REDACTED>/gi'
    's/id_token[[:space:]]*[:=][[:space:]]*[^[:space:]]+/id_token: <REDACTED>/gi'
    's/bearer[[:space:]]+[A-Za-z0-9._~+\/=-]+/Bearer <REDACTED>/gi'
    's/(^|[^[:alnum:]])secret[[:space:]]*[:=][[:space:]]*[^[:space:]]+/\1secret: <REDACTED>/gi'
    's/CF_Token=[^[:space:]]+/CF_Token=<REDACTED>/g'
    's/CF_Account_ID=[^[:space:]]+/CF_Account_ID=<REDACTED>/g'
    's/SAVED_CF_Token=[^[:space:]]+/SAVED_CF_Token=<REDACTED>/g'
    's/SAVED_CF_Account_ID=[^[:space:]]+/SAVED_CF_Account_ID=<REDACTED>/g'
    's/\$2[aby]\$[0-9]+\$[A-Za-z0-9.\/]+/<BCRYPT_HASH>/g'
    's/client-certificate-data:[[:space:]]*[^[:space:]]+/client-certificate-data: <REDACTED>/g'
    's/client-key-data:[[:space:]]*[^[:space:]]+/client-key-data: <REDACTED>/g'
    's/certificate-authority-data:[[:space:]]*[^[:space:]]+/certificate-authority-data: <REDACTED>/g'
    's/OPENVPN_USER=[^[:space:]]+/OPENVPN_USER=<REDACTED>/g'
    's/OPENVPN_PASSWORD=[^[:space:]]+/OPENVPN_PASSWORD=<REDACTED>/g'
    's/openvpn-user:[[:space:]]*[^[:space:]]+/openvpn-user: <REDACTED>/g'
    's/openvpn-password:[[:space:]]*[^[:space:]]+/openvpn-password: <REDACTED>/g'
    's/api-token:[[:space:]]*[^[:space:]]+/api-token: <REDACTED>/g'
    's/oidc_client_id:[[:space:]]*[^[:space:]]+/oidc_client_id: <REDACTED>/g'
    's/oidc_client_secret:[[:space:]]*[^[:space:]]+/oidc_client_secret: <REDACTED>/g'
    's/client_id:[[:space:]]*[^[:space:]]+/client_id: <REDACTED>/g'
    's/client_secret:[[:space:]]*[^[:space:]]+/client_secret: <REDACTED>/g'
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

    # Capture both stdout and stderr, show errors on failure
    # Temporarily disable errexit to capture exit code before it triggers script exit
    local ssh_output
    local ssh_rc
    set +e
    ssh_output=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "${user}@${host}" bash << 'REMOTE_EOF' 2>&1
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
echo "--- Fail2ban Status ---"
if command -v fail2ban-client &>/dev/null; then
    sudo fail2ban-client status 2>/dev/null || echo "fail2ban not running"
    echo ""
    echo "Active jails:"
    for jail in $(sudo fail2ban-client status 2>/dev/null | grep "Jail list:" | sed 's/.*Jail list:[[:space:]]*//' | tr ',' ' '); do
        echo "  $jail:"
        sudo fail2ban-client status "$jail" 2>/dev/null | grep -E 'Currently banned:|Total banned:' | sed 's/^/    /'
    done
else
    echo "fail2ban not installed"
fi
echo ""
REMOTE_EOF
)
    ssh_rc=$?
    set -e

    if [ $ssh_rc -ne 0 ]; then
        echo "Failed to connect to $host (exit code: $ssh_rc)"
        # Show first few lines of error output for diagnostics
        echo "Error details: $(echo "$ssh_output" | head -3)"
    else
        echo "$ssh_output"
    fi
}

collect_proxmox() {
    local host=$1
    echo "=== Proxmox-specific: $host ==="

    ssh -o ConnectTimeout=10 -o BatchMode=yes "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
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
# All VMIDs: DNS (150,160), SMTP (151), Plex (152), HA (154), k3s servers (222,223,227), k3s agents (202-207)
for vmid in 150 151 152 154 160 202 203 204 205 206 207 222 223 227; do
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
# Dynamically discover pools on this host (NAS has tank/ssd/nvme/archive, compute nodes have local-ssd)
zpool list -H -o name 2>/dev/null | while IFS= read -r pool; do
    echo "Pool: $pool"
    zpool status "$pool" 2>/dev/null | grep -E 'state:|scan:|errors:' || true
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
    mount | grep "type fuse.mergerfs" | awk '{print $3}' | sort -u | while IFS= read -r mnt; do
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
# Query cluster to find which node hosts LXC 152
# Note: Pipeline guarded with || true to continue state collection if cluster API unavailable
plex_node=$(sudo pvesh get /cluster/resources --type vm --output-format json 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(next((v['node'] for v in d if v.get('vmid')==152),''))" 2>/dev/null || true)
if [ -z "$plex_node" ]; then
    echo "Plex LXC location: unknown (cluster API unavailable or VMID 152 not found)"
elif [ "$(hostname)" = "$plex_node" ]; then
    if sudo pct status 152 &>/dev/null; then
        sudo pct status 152
        echo "Bind mounts:"
        sudo grep "^mp" /etc/pve/lxc/152.conf 2>/dev/null || echo "No bind mounts configured"
        echo "Plex service (inside container):"
        sudo pct exec 152 -- systemctl is-active plexmediaserver 2>/dev/null || echo "Cannot check Plex service"
    else
        echo "Plex LXC (152) not found"
    fi
else
    echo "Plex LXC runs on $plex_node (skipping - this is $(hostname))"
fi
echo ""
echo "--- Home Assistant VM Status (VMID 154) ---"
# Query cluster to find which node hosts VM 154
# Note: Pipeline guarded with || true to continue state collection if cluster API unavailable
ha_node=$(sudo pvesh get /cluster/resources --type vm --output-format json 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(next((v['node'] for v in d if v.get('vmid')==154),''))" 2>/dev/null || true)
if [ -z "$ha_node" ]; then
    echo "Home Assistant VM location: unknown (cluster API unavailable or VMID 154 not found)"
elif [ "$(hostname)" = "$ha_node" ]; then
    if sudo qm status 154 &>/dev/null; then
        sudo qm status 154
        echo "VM Config:"
        sudo qm config 154 2>/dev/null | grep -E 'cores|memory|net0|boot|onboot|startup' || echo "Cannot read config"
        echo "Network:"
        sudo qm guest cmd 154 network-get-interfaces 2>/dev/null | grep -E 'ip-address|name' || echo "Guest agent unavailable"
    else
        echo "Home Assistant VM (154) not found"
    fi
else
    echo "Home Assistant VM runs on $ha_node (skipping - this is $(hostname))"
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
if command -v tailscale &>/dev/null; then
    ts_output=$(sudo tailscale status 2>/dev/null | head -5)
    if [ -n "$ts_output" ]; then
        echo "$ts_output"
    else
        echo "Tailscale installed but not connected or no peers"
    fi
else
    echo "Tailscale not installed"
fi
echo ""
echo "--- Oh My Zsh Plugins ---"
# Plugins span multiple lines in .zshrc, extract the entire block
if [ -f ~/.zshrc ]; then
    # Use sed to extract plugins=( ... ) block (handles multi-line)
    sed -n '/^plugins=(/,/^)/p' ~/.zshrc 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g' || echo "No plugins found"
else
    echo "No zsh config"
fi
echo ""
echo "--- Proxmox HA Status ---"
sudo ha-manager status 2>/dev/null || echo "HA not configured"
echo ""
echo "--- HA Resources ---"
sudo ha-manager config 2>/dev/null | grep -E '^(ct|vm):' || echo "No HA resources"
echo ""
echo "--- HA Rules ---"
sudo ha-manager rules list 2>/dev/null || echo "No HA rules (Proxmox 9+ feature)"
echo ""
echo "--- Storage Replication ---"
sudo pvesr list 2>/dev/null || echo "No replication jobs"
sudo pvesr status 2>/dev/null | head -10 || true
echo ""
EOF
}

collect_dns() {
    local host=$1
    echo "=== DNS-specific: $host ==="

    ssh -o ConnectTimeout=10 -o BatchMode=yes "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
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

    ssh -o ConnectTimeout=10 -o BatchMode=yes "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
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

    ssh -o ConnectTimeout=10 -o BatchMode=yes "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
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
if command -v tailscale &>/dev/null; then
    ts_output=$(sudo tailscale status 2>/dev/null | head -5)
    if [ -n "$ts_output" ]; then
        echo "$ts_output"
    else
        echo "Tailscale installed but not connected or no peers"
    fi
else
    echo "Tailscale not installed"
fi
echo ""
EOF
}

collect_home_assistant() {
    local host=$1
    echo "=== Home Assistant (HAOS VM): $host ==="

    # Note: Home Assistant OS VM status collected from Proxmox host
    # Configuration files collected via SSH if SSH add-on is configured

    # Check if SSH is accessible (requires SSH add-on on port 22222)
    if ssh -o ConnectTimeout=5 -o BatchMode=yes -p 22222 "root@${host}" "echo test" &>/dev/null; then
        ssh -o ConnectTimeout=10 -o BatchMode=yes -p 22222 "root@${host}" bash << 'EOF' 2>/dev/null || echo "SSH command failed"
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

collect_gitlab() {
    local host=$1
    echo "=== GitLab-specific: $host ==="

    ssh -o ConnectTimeout=10 -o BatchMode=yes "eric@${host}" bash << 'EOF' 2>/dev/null || echo "Failed"
echo "--- GitLab Version ---"
if [ -x /opt/gitlab/bin/gitlab-ctl ]; then
    sudo gitlab-rake gitlab:env:info 2>/dev/null | grep -E 'GitLab.*:' | head -5 || \
        cat /opt/gitlab/version-manifest.txt 2>/dev/null | grep gitlab-ee | head -1 || \
        echo "Version check failed"
else
    echo "GitLab not installed"
fi
echo ""
echo "--- GitLab Status ---"
if [ -x /opt/gitlab/bin/gitlab-ctl ]; then
    sudo /opt/gitlab/bin/gitlab-ctl status 2>/dev/null | head -20 || echo "gitlab-ctl status failed"
else
    echo "GitLab not installed"
fi
echo ""
echo "--- GitLab Health Check ---"
if command -v curl &>/dev/null; then
    # Check GitLab health endpoint (internal, no TLS)
    health=$(curl -s --connect-timeout 5 http://localhost/-/health 2>/dev/null)
    if [ -n "$health" ]; then
        echo "Health endpoint: $health"
    else
        echo "Health endpoint: unreachable"
    fi
fi
echo ""
echo "--- GitLab Disk Usage ---"
echo "Repository storage:"
sudo du -sh /mnt/gitlab-repos/git-data 2>/dev/null || echo "  External storage not configured or inaccessible"
echo "Backups:"
sudo du -sh /var/opt/gitlab/backups 2>/dev/null || echo "  Backup dir inaccessible"
ls -la /var/opt/gitlab/backups/*.tar 2>/dev/null | tail -3 || echo "  No backup files found"
echo ""
echo "--- Container Registry ---"
if [ -f /etc/gitlab/gitlab.rb ]; then
    registry_enabled=$(sudo grep -E "^registry_external_url" /etc/gitlab/gitlab.rb 2>/dev/null | head -1)
    if [ -n "$registry_enabled" ]; then
        echo "Registry configured: $registry_enabled"
        sudo /opt/gitlab/bin/gitlab-ctl status registry 2>/dev/null || echo "Registry status unavailable"
    else
        echo "Container registry not configured"
    fi
fi
echo ""
echo "--- GitLab Pages ---"
if [ -f /etc/gitlab/gitlab.rb ]; then
    pages_enabled=$(sudo grep -E "^pages_external_url" /etc/gitlab/gitlab.rb 2>/dev/null | head -1)
    if [ -n "$pages_enabled" ]; then
        echo "Pages configured: $pages_enabled"
        sudo /opt/gitlab/bin/gitlab-ctl status gitlab-pages 2>/dev/null || echo "Pages status unavailable"
    else
        echo "GitLab Pages not configured"
    fi
fi
echo ""
echo "--- GitLab SSH (Port Redirect) ---"
sudo iptables -t nat -L PREROUTING -n 2>/dev/null | grep -E '2222.*22' || echo "No port redirect configured"
echo ""
echo "--- Fail2ban GitLab SSH Jail ---"
if command -v fail2ban-client &>/dev/null; then
    sudo fail2ban-client status gitlab-ssh 2>/dev/null || echo "gitlab-ssh jail not configured"
else
    echo "fail2ban not installed"
fi
echo ""
echo "--- Recent GitLab Logs ---"
# NOTE: GitLab logs are NOT included in state collection because they may contain:
# - Personal email addresses (user PII)
# - Session tokens and API keys in request parameters
# - OAuth tokens in callback URLs
# - Git push/pull authentication details
# To view GitLab errors manually, run on the GitLab host:
#   sudo tail -100 /var/log/gitlab/gitlab-rails/production.log | grep -iE 'error|exception|fail'
#   sudo gitlab-ctl tail
echo "GitLab logs excluded from state collection (may contain PII/tokens)"
echo "Run 'sudo gitlab-ctl tail' on gitlab host for live logs"
echo ""
echo "--- GitLab SMTP Test ---"
# Test SMTP connectivity (non-blocking check)
if [ -f /etc/gitlab/gitlab.rb ]; then
    smtp_addr=$(sudo grep -E "gitlab_rails\['smtp_address'\]" /etc/gitlab/gitlab.rb 2>/dev/null | head -1)
    if [ -n "$smtp_addr" ]; then
        echo "SMTP configured: $smtp_addr"
    else
        echo "SMTP not configured in gitlab.rb"
    fi
fi
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

    # Home Assistant
    collect_home_assistant "$HOME_ASSISTANT_HOST"
    echo ""

    # GitLab
    collect_host "$GITLAB_HOST"
    collect_gitlab "$GITLAB_HOST"
    echo ""

} > "$TEMP_DIR/raw.txt"

# Apply redaction
redact "$(cat "$TEMP_DIR/raw.txt")" > "$OUTPUT_FILE"

echo ""
echo "State collection complete: $OUTPUT_FILE"
echo "File size: $(wc -c < "$OUTPUT_FILE") bytes"
