#!/usr/bin/env bash
# collect-state.sh - Collect cluster state with automatic secret redaction
# Usage: ./scripts/collect-state.sh [--json] [output_file]
#   --json: Output machine-readable JSON health summary to stdout (no file written)

set -euo pipefail

# Classification invariant: regular mode (OK/PARTIAL/FAILED) and --json
# mode (healthy/degraded/neither) are the same tri-state classifier fed
# by the same probes (Proxmox SSH, k3s nodes ALL Ready, Flux not-ready,
# ZFS degraded, GitLab /-/health via the internal VIP;
# probe failures default to 0 / unhealthy-only-degrades).
# Recent Warning events are collected and reported but are advisory only:
# they do not gate OK/healthy (transient scheduling/Flux warnings are
# common and would otherwise make a green run unachievable).
# Differences, both deliberate:
#   - the host-coverage floor is regular-only (only regular collects from
#     auxiliary hosts, and a sparse artifact must not overwrite
#     CLUSTER_STATUS.txt);
#   - regular OK requires ALL collected hosts reachable (DNS/mail/k3s
#     VMs/GitLab — HAOS is probed separately and not in the counters)
#     while --json healthy only counts the 6 Proxmox hosts — regular is
#     strictly stricter, never the reverse.
# When adding a signal to one mode, mirror it in the other.

PVE_HOSTS=(pve-nas-01 pve-laptop-01 pve-opt-01 pve-opt-02 pve-opt-03 pve-prec-01)

# Handle --json mode
if [ "${1:-}" = "--json" ]; then
    # Quick health check mode - outputs JSON summary to stdout (built via jq -n at end)

    # Proxmox nodes reachability
    PVE_UP=0; PVE_TOTAL=0
    for host in "${PVE_HOSTS[@]}"; do
        PVE_TOTAL=$((PVE_TOTAL + 1))
        if ssh -o ConnectTimeout=3 -o BatchMode=yes "eric@${host}" "true" 2>/dev/null; then
            PVE_UP=$((PVE_UP + 1))
        fi
    done

    # K3s nodes (use mktemp to avoid /tmp collision/symlink issues)
    K3S_READY=0; K3S_TOTAL=0; K3S_VERSION=""
    K3S_NODES_JSON=$(mktemp)
    K3S_PODS_JSON=$(mktemp)
    trap 'rm -f "$K3S_NODES_JSON" "$K3S_PODS_JSON"' EXIT
    # All jq parses use fallbacks so malformed/partial JSON won't abort --json mode under set -e
    if kubectl get nodes -o json 2>/dev/null > "$K3S_NODES_JSON" && [ -s "$K3S_NODES_JSON" ]; then
        K3S_TOTAL=$(jq '.items | length' "$K3S_NODES_JSON" 2>/dev/null || echo 0)
        # Use any() with ? for null safety when conditions array is missing
        K3S_READY=$(jq '[.items[] | select(any(.status.conditions[]?; .type=="Ready" and .status=="True"))] | length' "$K3S_NODES_JSON" 2>/dev/null || echo 0)
        K3S_VERSION=$(jq -r '.items[0].status.nodeInfo.kubeletVersion // "unknown"' "$K3S_NODES_JSON" 2>/dev/null || echo "unknown")
    fi

    # K3s pods
    POD_TOTAL=0; POD_RUNNING=0
    if kubectl get pods -A -o json 2>/dev/null > "$K3S_PODS_JSON" && [ -s "$K3S_PODS_JSON" ]; then
        POD_TOTAL=$(jq '.items | length' "$K3S_PODS_JSON" 2>/dev/null || echo 0)
        POD_RUNNING=$(jq '[.items[] | select(.status.phase=="Running" or .status.phase=="Succeeded")] | length' "$K3S_PODS_JSON" 2>/dev/null || echo 0)
    fi

    # ZFS pool health — aggregate across ALL reachable Proxmox hosts (a
    # degraded local-ssd on a compute node must not hide behind a healthy
    # NAS). Pool detail list keeps the first reachable host's pools.
    ZFS_POOLS="[]"
    ZFS_DEGRADED=0
    for host in "${PVE_HOSTS[@]}"; do
        if POOLS=$(ssh -o ConnectTimeout=3 -o BatchMode=yes "eric@${host}" "zpool list -H -o name,health,size,alloc,free 2>/dev/null" 2>/dev/null); then
            HOST_DEGRADED=$(echo "$POOLS" | awk -F'\t' 'NF>=2 && $2 != "ONLINE" {c++} END{print c+0}')
            ZFS_DEGRADED=$((ZFS_DEGRADED + HOST_DEGRADED))
            if [ "$ZFS_POOLS" = "[]" ]; then
                ZFS_POOLS=$(echo "$POOLS" | jq -R -s '[split("\n")[] | select(length>0) | split("\t") | {name:.[0], health:.[1], size:.[2], alloc:.[3], free:.[4]}]' 2>/dev/null || echo "[]")
            fi
        fi
    done

    # Flux readiness — count Kustomizations and HelmReleases that are NOT Ready=True.
    FLUX_NOT_READY=0
    if FLUX_JSON=$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io -A -o json 2>/dev/null); then
        FLUX_NOT_READY=$(echo "$FLUX_JSON" | jq '[.items[] | select(any(.status.conditions[]?; .type=="Ready" and .status!="True"))] | length' 2>/dev/null || echo 0)
    fi

    # Recent warning events (last hour). Spikes here often surface Flux/HelmRelease
    # / scheduling issues before the explicit Ready=False alerts trip.
    WARNING_EVENTS=0
    if EVENTS_JSON=$(kubectl get events -A --field-selector type=Warning -o json 2>/dev/null); then
        WARNING_EVENTS=$(echo "$EVENTS_JSON" | jq --arg cutoff "$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" '[.items[] | select(.lastTimestamp >= $cutoff)] | length' 2>/dev/null || echo 0)
    fi

    # GitLab application health through the full delivery chain (internal
    # DNS -> Traefik VIP -> GitLab nginx). GitLab is the GitOps source of
    # truth, so its health gates green (degrades; never catastrophic).
    # TLS is verified (no -k): a broken cert chain or failed rotation is
    # a real degradation this probe should surface, not mask.
    GITLAB_OK=0
    GITLAB_HTTP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 https://git.esweiss.com/-/health 2>/dev/null || true)
    [ "$GITLAB_HTTP" = "200" ] && GITLAB_OK=1

    # ---------------------------------------------------------------------
    # Collector context — distinguishes "cluster genuinely unhealthy" (e.g.
    # nodes truly unreachable) from "collector misconfigured" (e.g. wrong
    # kube_context, no ssh-agent keys, run from a host without LAN access).
    # A `healthy: false` with `ssh_agent_keys: 0` is operator-side, not
    # cluster-side. Every value below tolerates the "unset / not available"
    # case so the script never aborts because (e.g.) ssh-agent isn't running.
    # ---------------------------------------------------------------------
    CTX_HOST=$(hostname -s 2>/dev/null || echo "unknown")
    CTX_USER=$(id -un 2>/dev/null || echo "unknown")
    CTX_KUBECONFIG="${KUBECONFIG:-}"
    CTX_KUBE_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "none")
    # Count loaded identities (lines starting with a bit count); `|| true`
    # absorbs ssh-add's non-zero exit when no agent/identities exist.
    CTX_SSH_AGENT_KEYS=$({ ssh-add -l 2>/dev/null || true; } | awk '/^[0-9]+ /{c++} END{print c+0}')
    CTX_GIT_SHA=$(git -C "$(dirname "$0")/.." rev-parse HEAD 2>/dev/null | cut -c1-12)
    CTX_GIT_SHA="${CTX_GIT_SHA:-unknown}"

    # Tri-state (mutually exclusive): healthy = green (strict: full
    # Proxmox coverage, zero Flux/ZFS imperfections; Warning events are
    # advisory and do not gate green); degraded = yellow (any imperfection
    # with core infra still up — the gate keeps a fully-down cluster from
    # reading as merely degraded); neither = red/catastrophic.
    jq -n \
        --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson pve_up "$PVE_UP" \
        --argjson pve_total "$PVE_TOTAL" \
        --argjson k3s_ready "$K3S_READY" \
        --argjson k3s_total "$K3S_TOTAL" \
        --arg k3s_version "$K3S_VERSION" \
        --argjson pod_total "$POD_TOTAL" \
        --argjson pod_running "$POD_RUNNING" \
        --argjson zfs_pools "$ZFS_POOLS" \
        --argjson zfs_degraded "$ZFS_DEGRADED" \
        --argjson flux_not_ready "$FLUX_NOT_READY" \
        --argjson warning_events "$WARNING_EVENTS" \
        --argjson gitlab_ok "$GITLAB_OK" \
        --arg ctx_host "$CTX_HOST" \
        --arg ctx_user "$CTX_USER" \
        --arg ctx_kubeconfig "$CTX_KUBECONFIG" \
        --arg ctx_kube_context "$CTX_KUBE_CONTEXT" \
        --argjson ctx_ssh_agent_keys "$CTX_SSH_AGENT_KEYS" \
        --arg ctx_git_sha "$CTX_GIT_SHA" \
        '{
            timestamp: $ts,
            healthy: (($pve_up > 0)
                     and ($pve_up == $pve_total)
                     and ($k3s_total > 0)
                     and ($k3s_ready == $k3s_total)
                     and ($flux_not_ready == 0)
                     and ($zfs_degraded == 0)
                     and ($gitlab_ok == 1)),
            degraded: ((($pve_up < $pve_total)
                       or ($k3s_ready < $k3s_total)
                       or ($flux_not_ready > 0)
                       or ($zfs_degraded > 0)
                       or ($gitlab_ok == 0))
                      and ($pve_up > 0)
                      and ($k3s_ready > 0)),
            proxmox: { reachable: $pve_up, total: $pve_total },
            k3s: { nodes_ready: $k3s_ready, nodes_total: $k3s_total, version: $k3s_version, pods_running: $pod_running, pods_total: $pod_total },
            zfs: { pools: $zfs_pools, degraded_count: $zfs_degraded },
            flux: { not_ready_count: $flux_not_ready },
            gitlab: { healthy: ($gitlab_ok == 1) },
            events: { warnings_last_hour: $warning_events },
            collector_context: {
                host: $ctx_host,
                user: $ctx_user,
                kubeconfig: $ctx_kubeconfig,
                kube_context: $ctx_kube_context,
                ssh_agent_keys: $ctx_ssh_agent_keys,
                git_sha: $ctx_git_sha,
                collector_run_at: $ts
            }
        }'

    # Exit non-zero if no infrastructure is reachable
    if [ "$PVE_UP" -eq 0 ] && [ "$K3S_TOTAL" -eq 0 ]; then
        exit 1
    fi
    exit 0
fi

OUTPUT_FILE="${1:-cluster-state-$(date +%Y%m%d-%H%M%S).txt}"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Hosts to collect from
PROXMOX_HOSTS=("${PVE_HOSTS[@]}")
DNS_HOSTS=("192.168.0.150" "192.168.0.160")  # dns-01, dns-02 (use IPs since hostnames resolve to VIP)
MAIL_HOSTS=("smtp-relay")
GITLAB_HOST="gitlab"  # 192.168.0.153 (gitlab VM on pve-nas-01)
# 9-node k3s cluster (3 servers + 6 agents)
K3S_HOSTS=("k3s-srv-nas-01" "k3s-srv-laptop-01" "k3s-srv-prec-01" "k3s-agt-nas-01" "k3s-agt-laptop-01" "k3s-agt-opt-01" "k3s-agt-opt-02" "k3s-agt-opt-03" "k3s-agt-prec-01")
HOME_ASSISTANT_HOST="192.168.0.154"  # home (HAOS VM)

# Flag to avoid collecting cluster-wide k3s data multiple times (runs on first server node only)
K3S_CLUSTER_COLLECTED=false

# ---------------------------------------------------------------------
# Run-quality tracking
# Counters are incremented during collection; a status header is
# rendered at the end of the run and the script exits non-zero when
# coverage falls below the floor (see status logic at end of script).
# ---------------------------------------------------------------------
HOSTS_TOTAL=0     # number of host SSHes attempted across all sections
HOSTS_OK=0        # number of host SSHes that returned rc=0
K3S_API_OK=false  # set true if `kubectl get nodes` succeeds locally
COVERAGE_FLOOR_PCT=50  # below this, the run is FAILED and CLUSTER_STATUS.txt is NOT overwritten

# Redaction patterns using POSIX-compatible character classes
# Note: Use [[:space:]] instead of \s and [^[:space:]] instead of \S for portability
# across BSD sed (macOS) and GNU sed (Linux)
# SECURITY: Patterns use (^|[^[:alnum:]]) to match at line start OR after non-alphanumeric
# This ensures patterns like "token: secret123" match even at the start of a line
# shellcheck disable=SC2016 # All patterns are sed-quoted regexes; $-escapes are intentional literals
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
    's/glrt-[A-Za-z0-9_-]+/<GITLAB_RUNNER_TOKEN>/g'
    's/gh[oprsu]_[A-Za-z0-9]{30,}/<GITHUB_TOKEN>/g'
    's/ops_[A-Za-z0-9_.-]{40,}/<OP_SA_TOKEN>/g'
    's/xox[abprs]-[A-Za-z0-9-]{10,}/<SLACK_TOKEN>/g'
    's/eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/<JWT>/g'
    's|https://discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+|https://discord.com/api/webhooks/<REDACTED>|g'
)

redact_file() {
    local infile="$1"
    local outfile="$2"
    local sed_args=()
    for pattern in "${REDACT_PATTERNS[@]}"; do
        sed_args+=(-e "$pattern")
    done
    # Defensive multi-line redaction for PEM private-key blocks. No current
    # collector cats key material, so this is fail-safe insurance: if a future
    # probe ever emits a `-----BEGIN ... PRIVATE KEY-----` block, the whole
    # block (which the single-line s/// patterns above can't catch) is collapsed
    # to a marker. Done as a separate awk pass so the range logic stays portable
    # across BSD (macOS) and GNU sed/awk (Linux) — `sed` range-change syntax
    # differs between the two, awk does not.
    sed -E "${sed_args[@]}" "$infile" \
        | awk '
            /-----BEGIN [A-Z ]*PRIVATE KEY-----/ { print "<PRIVATE_KEY_REDACTED>"; inkey=1; next }
            /-----END [A-Z ]*PRIVATE KEY-----/   { inkey=0; next }
            inkey { next }
            { print }
        ' > "$outfile"
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
echo "--- Memory ---"
free -h
echo ""
echo "--- CPU ---"
nproc
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

    HOSTS_TOTAL=$((HOSTS_TOTAL + 1))
    if [ $ssh_rc -ne 0 ]; then
        echo "Failed to connect to $host (exit code: $ssh_rc)"
        # Show first few lines of error output for diagnostics
        echo "Error details: $(echo "$ssh_output" | head -3)"
    else
        HOSTS_OK=$((HOSTS_OK + 1))
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
sudo cat /etc/pve/firewall/cluster.fw 2>/dev/null | grep --no-group-separator -A 20 '\[IPSET' | head -100 || echo "No firewall config"
echo ""
echo "--- Firewall Guest Rules ---"
# All VMIDs: DNS (150,160), SMTP (151), Plex (152), HA (154), k3s servers (222,223,227), k3s agents (202-207)
for vmid in 150 151 152 153 154 160 202 203 204 205 206 207 222 223 227; do
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
for pool in $(zpool list -H -o name 2>/dev/null); do
    echo "Pool: $pool"
    zpool status "$pool" 2>/dev/null | grep -E 'state:|scan:|errors:' || true
done
echo ""
echo "--- ZFS Datasets ---"
zfs list -o name,mountpoint,used,avail 2>/dev/null | head -50 || echo "No ZFS"
echo ""
echo "--- ZFS Encryption Keystatus ---"
zfs get -H -o name,value keystatus 2>/dev/null | awk '$2 == "available" || $2 == "unavailable"' | head -20
echo ""
echo "--- SMART Status ---"
sudo systemctl is-active smartd 2>/dev/null || echo "smartd not active"
echo ""
echo "--- SMART Pending/Reallocated Sectors (SATA) ---"
for d in /dev/sd?; do
    [ -b "$d" ] || continue
    v=$(sudo smartctl -A "$d" 2>/dev/null | awk '/Reallocated_Sector_Ct|Current_Pending_Sector/ {printf "%s=%s ", $2, $10}')
    [ -n "$v" ] && echo "  $d: $v"
done
echo ""
echo "--- Boot-time Unlock Units (ZFS native key-load) ---"
systemctl list-units --all --no-legend 'zfs-load-key@*' 2>/dev/null | awk '{print "  " $1, $3, $4}'
[ -z "$(systemctl list-units --all --no-legend 'zfs-load-key@*' 2>/dev/null)" ] && echo "  none configured on this host"
echo ""
echo "--- NFS Exports ---"
sudo cat /etc/exports 2>/dev/null || echo "No exports"
echo ""
echo "--- Samba Status ---"
systemctl is-active smbd 2>/dev/null || echo "No Samba"
sudo testparm -s 2>/dev/null | grep --no-group-separator -A 5 '\[' | head -20 || true
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
echo "--- VM List ---"
qm list 2>/dev/null || echo 'Cannot list VMs'
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
    grep --no-group-separator -A 50 '^plugins=(' ~/.zshrc 2>/dev/null | sed -n '/^plugins=(/,/)/p' | head -20 || echo "Not found"
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
echo "--- DNS Resolution Test ---"
dig +short google.com @127.0.0.1 2>/dev/null || echo 'DNS resolution failed'
dig +short esweiss.com @127.0.0.1 2>/dev/null || echo 'Internal DNS resolution failed'
echo ""
echo "--- AdGuard Sync Timer ---"
systemctl list-timers adguardhome-sync* 2>/dev/null || echo 'No sync timer found'
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
echo "--- Mail Queue ---"
sudo postqueue -p 2>/dev/null | tail -1 || echo 'Cannot check mail queue'
echo ""
EOF
}

collect_k3s() {
    local host=$1
    echo "=== K3s-specific: $host ==="

    # Per-node data (always collected)
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
echo "--- K3s Config (sanitized, non-sensitive keys only) ---"
if [ -f /etc/rancher/k3s/config.yaml ]; then
    # Only show known-safe configuration keys; exclude token/secret-related fields
    sudo grep -E '^(write-kubeconfig-mode|tls-san|node-ip|node-external-ip|cluster-cidr|service-cidr|cluster-domain|disable|secrets-encryption|node-label|node-taint|bind-address|advertise-address):' \
        /etc/rancher/k3s/config.yaml 2>/dev/null || echo "Cannot read config file (permission denied)"
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

    # Cluster-wide data (collected once from the first server node)
    # Uses exit codes: 0 = collected, 2 = not a server (try next), other = SSH/remote failure
    if [ "$K3S_CLUSTER_COLLECTED" = "false" ]; then
        # Temporarily disable set -e so we can check the exit code
        set +e
        ssh -o ConnectTimeout=10 -o BatchMode=yes "eric@${host}" bash << 'EOF' 2>/dev/null
if systemctl is-active k3s &>/dev/null; then
    # Readiness probe: verify the kubectl API actually responds before
    # proceeding. Without this, downstream failures are masked by || echo
    # and K3S_CLUSTER_COLLECTED=true would be set with no actual data.
    # rc=3 triggers retry on the next server node.
    sudo k3s kubectl get nodes -o wide >/dev/null 2>&1 || exit 3
    echo "--- Node Info (cluster-wide, collected once) ---"
    sudo k3s kubectl get nodes -o wide 2>/dev/null || echo "Cannot get nodes"
    echo ""
    echo "--- Pod Status ---"
    sudo k3s kubectl get pods -A 2>/dev/null | head -60 || echo "Cannot get pods"
    echo ""
    echo "--- Services ---"
    sudo k3s kubectl get svc -A 2>/dev/null | head -40 || echo "Cannot get services"
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
    echo ""
    echo "--- GitLab Runner (gitlab-runner namespace) ---"
    sudo k3s kubectl get pods -n gitlab-runner 2>/dev/null || echo "Cannot get gitlab-runner pods"
    echo ""
    echo "--- cert-manager ---"
    sudo k3s kubectl get clusterissuer 2>/dev/null || echo "Cannot get ClusterIssuers"
    sudo k3s kubectl get certificate -A 2>/dev/null || echo "Cannot get certificates"
    echo ""
    echo "--- MetalLB Configuration ---"
    sudo k3s kubectl get ipaddresspool -n metallb-system 2>/dev/null || echo "Cannot get IP pools"
    sudo k3s kubectl get l2advertisement -n metallb-system 2>/dev/null || echo "Cannot get L2 advertisements"
    echo ""
    echo "--- Flux Kustomizations ---"
    sudo k3s kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A 2>/dev/null || echo "Cannot get Flux Kustomizations"
    echo ""
    echo "--- Flux System ---"
    sudo k3s kubectl get pods -n flux-system 2>/dev/null || echo "Cannot get flux-system pods"
    sudo k3s kubectl get gitrepositories -A 2>/dev/null || echo "Cannot get GitRepositories"
    echo ""
    echo "--- External Secrets Namespace ---"
    sudo k3s kubectl get pods -n external-secrets 2>/dev/null || echo "Cannot get external-secrets pods"
    sudo k3s kubectl get helmreleases -n external-secrets 2>/dev/null || echo "Cannot get external-secrets HelmReleases"
    echo ""
    echo "--- ClusterSecretStore ---"
    sudo k3s kubectl get clustersecretstore 2>/dev/null || echo "Cannot get ClusterSecretStores"
    echo ""
    echo "--- ExternalSecrets (all namespaces) ---"
    sudo k3s kubectl get externalsecrets -A 2>/dev/null || echo "Cannot get ExternalSecrets"
    echo ""
    echo "--- HelmReleases (all namespaces) ---"
    sudo k3s kubectl get helmreleases -A 2>/dev/null || echo "Cannot get HelmReleases"
    echo ""
    echo "--- PersistentVolumes ---"
    sudo k3s kubectl get pv 2>/dev/null || echo "Cannot get PersistentVolumes"
    echo ""
    echo "--- Autoscaling (HPA / VPA) ---"
    sudo k3s kubectl get hpa -A 2>/dev/null || echo "Cannot get HPAs"
    sudo k3s kubectl get vpa -A 2>/dev/null || echo "Cannot get VPAs (CRD absent?)"
    echo ""
    echo "--- NetworkPolicies (all namespaces) ---"
    sudo k3s kubectl get networkpolicies -A 2>/dev/null || echo "Cannot get NetworkPolicies"
    echo ""
    echo "--- Observability Namespace ---"
    sudo k3s kubectl get pods -n observability -o wide 2>/dev/null || echo "Cannot get observability pods"
    sudo k3s kubectl get pvc -n observability 2>/dev/null || echo "Cannot get observability PVCs"
    sudo k3s kubectl get servicemonitors -A 2>/dev/null || echo "Cannot get ServiceMonitors"
    sudo k3s kubectl get podmonitors -A 2>/dev/null || echo "Cannot get PodMonitors"
    sudo k3s kubectl get prometheusrules -n observability 2>/dev/null || echo "Cannot get PrometheusRules"
    echo ""
    echo "--- Grafana Dashboards ---"
    sudo k3s kubectl get configmap -n observability -l grafana_dashboard --no-headers 2>/dev/null | wc -l | xargs -I{} echo "Dashboard ConfigMaps: {}"
    echo ""
    echo "--- Alertmanager Active Alerts ---"
    sudo k3s kubectl -n observability exec alertmanager-kube-prometheus-stack-alertmanager-0 -c alertmanager -- \
        amtool --alertmanager.url=http://localhost:9093 alert query 2>/dev/null | head -30 || echo "Cannot query alertmanager"
    echo ""
    echo "--- Cluster Warning Events (last 20) ---"
    sudo k3s kubectl get events -A --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -20 || echo "Cannot get events"
else
    echo "(Not a k3s server node, skipping cluster-wide data)"
    exit 2
fi
echo ""
EOF
        rc=$?
        set -e
        if [ "$rc" -eq 0 ]; then
            K3S_CLUSTER_COLLECTED=true
        elif [ "$rc" -eq 2 ]; then
            echo "No cluster-wide data on $host (not a server), will try next server node"
        elif [ "$rc" -eq 3 ]; then
            echo "kubectl API unresponsive on $host, will retry on next server node"
        else
            echo "Failed to collect cluster-wide data from $host (rc=$rc), will retry on next server node"
        fi
    fi
}

# Alloy log-shipper status across all hosts, checked from the collector
# machine (the k3s nodes have no SSH keys to other hosts, so this cannot
# run nested inside collect_k3s — it previously reported every host
# "unreachable" for that reason).
collect_alloy_status() {
    echo "=== Alloy host log shippers ==="
    # plex by IP: the short name resolves through the AdGuard rewrite to the
    # Traefik VIP, not the LXC (same trap as DNS_HOSTS).
    for host in "${PVE_HOSTS[@]}" "${DNS_HOSTS[@]}" "${MAIL_HOSTS[@]}" "$GITLAB_HOST" 192.168.0.152 "${K3S_HOSTS[@]}"; do
        local status
        status=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "eric@${host}" 'systemctl is-active alloy 2>/dev/null' 2>/dev/null) || true
        echo "  $host: ${status:-unreachable}"
    done
    echo ""
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
    # nginx serves the git.esweiss.com cert; --resolve pins that name to
    # loopback so the probe stays local AND validates the chain (no -k).
    code=$(curl -s --resolve git.esweiss.com:443:127.0.0.1 -o /dev/null -w '%{http_code}' --connect-timeout 5 https://git.esweiss.com/-/health 2>/dev/null)
    echo "Health endpoint (https://git.esweiss.com/-/health via 127.0.0.1): HTTP ${code:-unreachable}"
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

# Probe local kubectl reachability up front so the run-quality logic
# can mark the K3s API as a core section. `--request-timeout` keeps
# the probe bounded if kubeconfig points at an unreachable VIP.
if kubectl --request-timeout=5s get nodes >/dev/null 2>&1; then
    K3S_API_OK=true
fi

# Additional health probes that mirror the --json branch's signals.
# Each probe tolerates failure (kubectl unreachable, ssh unreachable,
# malformed output) and falls back to "0" so an operator-side issue
# never falsely promotes a regular-mode run from PARTIAL to OK.
# Defaults pre-set so set -u doesn't trip if probes are skipped.
FLUX_NOT_READY_REG=0
ZFS_DEGRADED_REG=0
WARNING_EVENTS_REG=0
# Catastrophic-state probes — mirror the --json branch's `pve_up` and
# `k3s_ready` so both modes use the SAME inputs to decide FAILED.
# `PVE_REACHABLE_REG` counts only Proxmox hosts that returned rc=0
# from a quick reachability probe (narrower than HOSTS_OK, which
# spans Proxmox + DNS + k3s VMs + GitLab + HAOS).
# `K3S_NODES_READY_REG` counts k3s nodes Ready=True via the API
# (stricter than K3S_API_OK, which only requires the API to respond).
PVE_REACHABLE_REG=0
PVE_TOTAL_REG=0
K3S_NODES_READY_REG=0
K3S_NODES_TOTAL_REG=0

# Proxmox reachability — mirrors --json mode's `pve_up` loop.
for host in "${PVE_HOSTS[@]}"; do
    PVE_TOTAL_REG=$((PVE_TOTAL_REG + 1))
    if ssh -o ConnectTimeout=3 -o BatchMode=yes "eric@${host}" "true" 2>/dev/null; then
        PVE_REACHABLE_REG=$((PVE_REACHABLE_REG + 1))
    fi
done

# K3s nodes Ready=True count — mirrors --json mode's `k3s_ready`.
# Tolerates kubectl unreachable / malformed JSON → 0.
if K3S_NODES_JSON_REG=$(kubectl --request-timeout=5s get nodes -o json 2>/dev/null) && [ -n "$K3S_NODES_JSON_REG" ]; then
    K3S_NODES_TOTAL_REG=$(echo "$K3S_NODES_JSON_REG" | jq '.items | length' 2>/dev/null || echo 0)
    K3S_NODES_READY_REG=$(echo "$K3S_NODES_JSON_REG" | jq '[.items[] | select(any(.status.conditions[]?; .type=="Ready" and .status=="True"))] | length' 2>/dev/null || echo 0)
fi

# Flux readiness — count Kustomizations and HelmReleases NOT Ready=True.
if FLUX_JSON_REG=$(kubectl --request-timeout=5s get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io -A -o json 2>/dev/null); then
    FLUX_NOT_READY_REG=$(echo "$FLUX_JSON_REG" | jq '[.items[] | select(any(.status.conditions[]?; .type=="Ready" and .status!="True"))] | length' 2>/dev/null || echo 0)
fi

# ZFS pool health — aggregate non-ONLINE pools across ALL reachable
# Proxmox hosts. Mirrors the --json branch's loop.
for host in "${PVE_HOSTS[@]}"; do
    if POOLS_REG=$(ssh -o ConnectTimeout=3 -o BatchMode=yes "eric@${host}" "zpool list -H -o name,health 2>/dev/null" 2>/dev/null); then
        HOST_DEGRADED_REG=$(echo "$POOLS_REG" | awk -F'\t' 'NF>=2 && $2 != "ONLINE" {c++} END{print c+0}')
        ZFS_DEGRADED_REG=$((ZFS_DEGRADED_REG + HOST_DEGRADED_REG))
    fi
done

# Recent warning events (last hour). Same kubectl invocation as --json.
if EVENTS_JSON_REG=$(kubectl --request-timeout=5s get events -A --field-selector type=Warning -o json 2>/dev/null); then
    WARNING_EVENTS_REG=$(echo "$EVENTS_JSON_REG" | jq --arg cutoff "$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" '[.items[] | select(.lastTimestamp >= $cutoff)] | length' 2>/dev/null || echo 0)
fi

# GitLab application health — mirrors the --json probe (full chain through
# the internal Traefik VIP, TLS verified). Unhealthy GitLab downgrades OK
# to PARTIAL.
GITLAB_OK_REG=0
GITLAB_HTTP_REG=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 https://git.esweiss.com/-/health 2>/dev/null || true)
[ "$GITLAB_HTTP_REG" = "200" ] && GITLAB_OK_REG=1

{
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

    collect_alloy_status

    # Home Assistant
    collect_home_assistant "$HOME_ASSISTANT_HOST"
    echo ""

    # GitLab
    collect_host "$GITLAB_HOST"
    collect_gitlab "$GITLAB_HOST"
    echo ""

} > "$TEMP_DIR/raw.txt"

# ---------------------------------------------------------------------
# Run-quality classification
# Status is one of OK / PARTIAL / FAILED. FAILED means we don't trust
# the report enough to overwrite CLUSTER_STATUS.txt and the script
# exits non-zero so callers (CI, cron, the operator) see the failure
# loudly. PARTIAL still produces a useful artifact with a banner; OK
# is the all-green path.
# ---------------------------------------------------------------------
if [ "$HOSTS_TOTAL" -gt 0 ]; then
    HOST_COVERAGE_PCT=$((HOSTS_OK * 100 / HOSTS_TOTAL))
else
    HOST_COVERAGE_PCT=0
fi

# Mirrors --json mode's healthy/degraded/catastrophic traffic-light states.
# The legacy names OK/PARTIAL/FAILED are preserved for backward compatibility
# but the *conditions* now match the --json branch on the catastrophic
# signal specifically: FAILED ⇔ no Proxmox host reachable OR (the K3s
# API was reachable AND no node is Ready). The host-coverage-floor band
# stays as a stricter coverage gate.
#   FAILED  (red)    catastrophic — no Proxmox host reachable, OR K3s API
#                    reachable but zero nodes Ready, OR coverage below floor;
#                    CLUSTER_STATUS.txt is NOT overwritten.
#   OK      (green)  full host coverage, zero Flux not-ready, zero non-ONLINE
#                    ZFS pools, GitLab healthy. Recent Warning events are
#                    reported in the header but advisory only — they do not
#                    block OK (transient warnings are common).
#   PARTIAL (yellow) anything else with core infra still up — e.g. one host
#                    unreachable, a Flux release stuck, OR the local kubectl
#                    can't reach the cluster while SSH collection succeeded
#                    (collector-side problem, not catastrophic cluster
#                    failure).
# Note: PVE_REACHABLE_REG and K3S_NODES_READY_REG mirror --json's `pve_up`
# and `k3s_ready` exactly. The K3s catastrophic check is gated on
# $K3S_API_OK so a missing/misconfigured local kubeconfig produces PARTIAL
# (visibly degraded) rather than a false FAILED that hides the per-host
# SSH collection — the JSON branch distinguishes the same case via
# `collector_context`.
if [ "$PVE_REACHABLE_REG" -eq 0 ] \
   || { [ "$K3S_API_OK" = true ] && [ "$K3S_NODES_READY_REG" -eq 0 ]; } \
   || [ "$HOST_COVERAGE_PCT" -lt "$COVERAGE_FLOOR_PCT" ]; then
    STATUS="FAILED"
elif [ "$HOSTS_OK" -eq "$HOSTS_TOTAL" ] \
     && [ "$K3S_API_OK" = true ] \
     && [ "$K3S_NODES_TOTAL_REG" -gt 0 ] \
     && [ "$K3S_NODES_READY_REG" -eq "$K3S_NODES_TOTAL_REG" ] \
     && [ "$FLUX_NOT_READY_REG" -eq 0 ] \
     && [ "$ZFS_DEGRADED_REG" -eq 0 ] \
     && [ "$GITLAB_OK_REG" -eq 1 ]; then
    STATUS="OK"
else
    STATUS="PARTIAL"
fi

# Render final output: status header + raw collection, redaction
# applied to the combined stream.
{
    echo "# Cluster State Collection"
    echo "# Generated: $(date -Iseconds)"
    echo "# Status: $STATUS"
    echo "# Proxmox reachable: $PVE_REACHABLE_REG / $PVE_TOTAL_REG"
    echo "# K3s nodes ready: $K3S_NODES_READY_REG / $K3S_NODES_TOTAL_REG"
    echo "# Hosts reachable: $HOSTS_OK / $HOSTS_TOTAL ($HOST_COVERAGE_PCT%)"
    echo "# K3s API reachable: $K3S_API_OK"
    echo "# Flux not-ready: $FLUX_NOT_READY_REG"
    echo "# ZFS degraded pools: $ZFS_DEGRADED_REG"
    echo "# GitLab health (https://git.esweiss.com/-/health): HTTP ${GITLAB_HTTP_REG:-unreachable}"
    echo "# Warning events (last hour): $WARNING_EVENTS_REG"
    echo "# Coverage floor: $COVERAGE_FLOOR_PCT% (run is FAILED below this)"
    echo "# Redacted: Yes"
    echo ""
    cat "$TEMP_DIR/raw.txt"
} > "$TEMP_DIR/with_header.txt"

# Apply redaction
redact_file "$TEMP_DIR/with_header.txt" "$OUTPUT_FILE"

echo ""
echo "State collection complete: $OUTPUT_FILE"
echo "Status: $STATUS — $HOSTS_OK/$HOSTS_TOTAL hosts reachable ($HOST_COVERAGE_PCT%), K3s API: $K3S_API_OK"
echo "File size: $(wc -c < "$OUTPUT_FILE") bytes"

if [ "$STATUS" = "FAILED" ]; then
    # The artifact stays on disk for inspection so the operator can see
    # which hosts failed, but we deliberately don't overwrite
    # CLUSTER_STATUS.txt with sparse/misleading data — better to leave
    # the previous good snapshot in place. Caller sees rc=2 and decides.
    echo "ERROR: FAILED status — no Proxmox host reachable, K3s API reachable with zero nodes Ready, or coverage below ${COVERAGE_FLOOR_PCT}%." >&2
    echo "       Proxmox reachable: $PVE_REACHABLE_REG/$PVE_TOTAL_REG; K3s API: $K3S_API_OK; K3s nodes ready: $K3S_NODES_READY_REG/$K3S_NODES_TOTAL_REG." >&2
    echo "       CLUSTER_STATUS.txt left untouched; raw artifact at $OUTPUT_FILE for inspection." >&2
    exit 2
fi

# Update CLUSTER_STATUS.txt with latest state (OK or PARTIAL only)
if cp "$OUTPUT_FILE" CLUSTER_STATUS.txt 2>/dev/null; then
    echo "Updated CLUSTER_STATUS.txt"
else
    echo "Warning: could not update CLUSTER_STATUS.txt (directory not writable?)"
fi

# Cleanup old state files (keep newest 5 by mtime)
# Use find for safe handling under set -euo pipefail, safe for filenames
# with special characters, and no-match safe. Scoped to output directory
# (not CWD) to prevent accidental deletion of unrelated files.
# Retention sorts by modification time (`ls -t`), not filename, so it stays
# correct even if OUTPUT_FILE uses a non-timestamp naming scheme that still
# matches the cluster-state-*.txt glob. Portable across macOS (bash 3.2,
# BSD ls) and Linux (bash 4+, GNU ls).
STATE_DIR="$(cd "$(dirname "$OUTPUT_FILE")" 2>/dev/null && pwd)"
STATE_DIR="${STATE_DIR:-.}"
# Resolve current output file to absolute path to prevent accidental self-deletion
# when a custom OUTPUT_FILE matches the cluster-state-*.txt pattern
CURRENT_OUTPUT="$STATE_DIR/$(basename "$OUTPUT_FILE")"
COUNT=0
while IFS= read -r file; do
    [ -z "$file" ] && continue
    # Never delete the file we just created
    [ "$file" = "$CURRENT_OUTPUT" ] && continue
    rm -f -- "$file"
    COUNT=$((COUNT + 1))
done < <(
    # find -> NUL-safe xargs -> `ls -td` (newest-first by mtime) -> drop the
    # 5 newest. `-d` keeps ls from descending into matches. `|| true` keeps
    # an empty match (or ls non-zero on no args) from aborting under set -e.
    find "$STATE_DIR" -maxdepth 1 -type f -name 'cluster-state-*.txt' -print0 2>/dev/null \
        | xargs -0 ls -td 2>/dev/null \
        | tail -n +6 \
        || true
)
if [ "$COUNT" -gt 0 ]; then
    echo "Cleaned up $COUNT old state files (keeping newest 5)"
fi
