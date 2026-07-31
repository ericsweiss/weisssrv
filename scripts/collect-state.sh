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
#     VMs/GitLab/Nextcloud/Immich/Immich-ML — HAOS is probed separately
#     and not in the HOST counters, though it is a tracked section)
#     while --json healthy only counts the 6 Proxmox hosts — regular is
#     strictly stricter, never the reverse;
#   - the specialised-collector section counters (SECTIONS_OK/TOTAL) and the
#     firing-alert gate are regular-only: --json runs no specialised
#     collectors, and both signals can only demote, never promote.
# When adding a signal to one mode, mirror it in the other. The verdict
# logic itself (classify_regular / classify_json) and the redaction
# patterns live in collect-state-lib.sh so they can be unit-tested
# (scripts/test_collect_state_lib.py).

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/collect-state-lib.sh
. "$_SCRIPT_DIR/collect-state-lib.sh"
# Host/IP roster, generated from ansible/inventories/prod/hosts.yml.
# shellcheck source=scripts/hosts.env
. "$_SCRIPT_DIR/hosts.env"

# Proxmox hosts by Tailscale/LAN hostname (PVE_HOSTS in hosts.env); split the
# sourced space-joined scalar into the array this script uses.
read -ra PVE_HOSTS <<< "$PVE_HOSTS"

# SSH option sets, defined once (DUP-12). Distinct ConnectTimeout values are
# deliberately kept separate: a short timeout for quick LAN reachability
# probes, a longer one for the full per-host collection sessions.
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)        # full collection sessions
SSH_OPTS_PROBE=(-o ConnectTimeout=3 -o BatchMode=yes)   # quick reachability probes

# Shared health probes (SH-3). Each probe is implemented ONCE here and
# called from BOTH the --json branch and regular mode so the two modes
# feed their (separate) classifiers from identical signals. Per-call-site
# differences that must be preserved are passed as arguments:
#   - the kubectl probes take extra kubectl args via "$@" (regular mode
#     passes --request-timeout=5s; the --json branch passes none);
#   - probe_zfs_degraded takes "detail" to additionally build the pool JSON
#     the --json output needs (regular mode omits it, fetching only
#     name,health).
# Probes tolerate failure (unreachable kubectl/ssh, malformed output) and
# fall back to 0 / false so an operator-side issue never falsely promotes a
# run's health verdict.

# Proxmox reachability. Echoes "<reachable> <total>".
probe_pve_reachable() {
    local up=0 total=0 host
    for host in "${PVE_HOSTS[@]}"; do
        total=$((total + 1))
        if ssh "${SSH_OPTS_PROBE[@]}" "eric@${host}" "true" 2>/dev/null; then
            up=$((up + 1))
        fi
    done
    echo "$up $total"
}

# ZFS pool health aggregated across ALL reachable Proxmox hosts (a degraded
# local-ssd on a compute node must not hide behind a healthy NAS). Sets:
#   ZFS_DEGRADED_RESULT  count of non-ONLINE pools
#   ZFS_POOLS_RESULT     JSON array of the first reachable host's pools, built
#                        only when called with "detail" (otherwise stays "[]")
# The degraded count inspects only column 2 (health), so the name,health and
# name,health,size,alloc,free column sets yield identical counts.
probe_zfs_degraded() {
    local want_detail="${1:-}"
    local cols="name,health"
    [ "$want_detail" = "detail" ] && cols="name,health,size,alloc,free"
    ZFS_DEGRADED_RESULT=0
    ZFS_POOLS_RESULT="[]"
    local host pools host_degraded
    for host in "${PVE_HOSTS[@]}"; do
        # shellcheck disable=SC2029 # $cols is a trusted constant; expanding it
        # client-side is intended (the remote gets the same literal column list).
        if pools=$(ssh "${SSH_OPTS_PROBE[@]}" "eric@${host}" "zpool list -H -o ${cols} 2>/dev/null" 2>/dev/null); then
            host_degraded=$(echo "$pools" | awk -F'\t' 'NF>=2 && $2 != "ONLINE" {c++} END{print c+0}')
            ZFS_DEGRADED_RESULT=$((ZFS_DEGRADED_RESULT + host_degraded))
            if [ "$want_detail" = "detail" ] && [ "$ZFS_POOLS_RESULT" = "[]" ]; then
                ZFS_POOLS_RESULT=$(echo "$pools" | jq -R -s '[split("\n")[] | select(length>0) | split("\t") | {name:.[0], health:.[1], size:.[2], alloc:.[3], free:.[4]}]' 2>/dev/null || echo "[]")
            fi
        fi
    done
}

# Flux readiness — count Kustomizations and HelmReleases that are NOT
# reconciling: Ready!=True OR spec.suspend=true. Suspended resources report
# their last (stale) Ready=True condition forever, so counting readiness alone
# let a cluster frozen weeks ago classify as OK. Extra kubectl args (e.g.
# --request-timeout=5s) pass through via "$@". Echoes the count (0 on failure).
probe_flux_not_ready() {
    local out=0 json
    if json=$(kubectl "$@" get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io -A -o json 2>/dev/null); then
        out=$(echo "$json" | jq '[.items[] | select((.spec.suspend == true) or any(.status.conditions[]?; .type=="Ready" and .status!="True"))] | length' 2>/dev/null || echo 0)
    fi
    echo "$out"
}

# Firing Alertmanager alerts, excluding the always-on Watchdog. Firing alerts
# are a far stronger health signal than the (advisory) Warning-event count and
# used to be collected into the artifact without reaching the verdict — an
# artifact headed "Status: OK" with a TargetDown alert active is exactly the
# lie this collector exists to prevent. The pod is resolved by label rather
# than the hard-coded StatefulSet ordinal so a rename degrades to "unknown"
# instead of silently reporting zero. Extra kubectl args pass through via "$@".
# Echoes the count, or "unknown" when the query could not run (unknown never
# gates the verdict — an operator-side kubectl problem must not promote or
# demote a run on its own).
probe_firing_alerts() {
    local pod out
    pod=$(kubectl "$@" -n observability get pods -l app.kubernetes.io/name=alertmanager \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || true
    if [ -z "$pod" ]; then
        echo "unknown"
        return
    fi
    if out=$(kubectl "$@" -n observability exec "$pod" -c alertmanager -- \
        amtool --alertmanager.url=http://localhost:9093 alert query -o json 2>/dev/null); then
        # `.[]?` tolerates amtool emitting `null` (rather than `[]`) for an
        # empty alert set, which would otherwise abort jq and read as unknown.
        echo "$out" | jq '[.[]? | select(.labels.alertname != "Watchdog")] | length' 2>/dev/null || echo "unknown"
    else
        echo "unknown"
    fi
}

# Recent Warning events (last hour). Extra kubectl args pass through via "$@".
# Echoes the count (0 on failure). Excludes ONLY the CI runner's designed
# capacity overflow: FailedScheduling in gitlab-runner* namespaces whose message
# cites "Insufficient cpu/memory" (a job burst queues pods the shared agent cores
# can't fit yet; poll_timeout waits for a slot rather than fail), so it recurs on
# every pipeline and is not a health signal. Guards keep real problems visible: a
# message that ALSO cites a genuinely-anomalous blocker (PVC / exceeded quota /
# volume node affinity) is NOT excluded, and any non-capacity or non-runner
# FailedScheduling still counts. We deliberately do NOT disqualify on taint/
# affinity mentions — every normal overflow message lists the known tainted nodes
# (NAS + servers), so keying off those would defeat the exclusion entirely.
probe_warning_events() {
    local out=0 json
    # Timestamp is coalesced: events emitted via the newer Events API often
    # carry only eventTime (lastTimestamp null), and jq's `null >= $cutoff` is
    # false — filtering on lastTimestamp alone silently drops them (e.g.
    # scheduler FailedScheduling).
    if json=$(kubectl "$@" get events -A --field-selector type=Warning -o json 2>/dev/null); then
        out=$(echo "$json" | jq --arg cutoff "$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" '[.items[] | select(((.lastTimestamp // .eventTime // .metadata.creationTimestamp) // "") >= $cutoff) | select((.reason == "FailedScheduling" and ((.metadata.namespace // "") | test("^gitlab-runner")) and ((.message // "") | test("Insufficient (cpu|memory)"; "i")) and (((.message // "") | test("persistentvolumeclaim|exceeded quota|volume node affinity conflict"; "i")) | not)) | not)] | length' 2>/dev/null || echo 0)
    fi
    echo "$out"
}

# K3s nodes: fetch node JSON and compute readiness. Extra kubectl args pass
# through via "$@". Sets:
#   K3S_API_OK_RESULT   true/false (API responded with a non-empty node list)
#   K3S_TOTAL_RESULT    node count
#   K3S_READY_RESULT    nodes with Ready=True
#   K3S_VERSION_RESULT  first node's kubelet version ("unknown" on failure)
# All jq parses use fallbacks so malformed/partial JSON won't abort under set -e.
probe_k3s_ready() {
    K3S_API_OK_RESULT=false
    K3S_TOTAL_RESULT=0
    K3S_READY_RESULT=0
    K3S_VERSION_RESULT=""
    local json
    if json=$(kubectl "$@" get nodes -o json 2>/dev/null) && [ -n "$json" ]; then
        K3S_API_OK_RESULT=true
        K3S_TOTAL_RESULT=$(echo "$json" | jq '.items | length' 2>/dev/null || echo 0)
        # any() with ? for null safety when the conditions array is missing
        K3S_READY_RESULT=$(echo "$json" | jq '[.items[] | select(any(.status.conditions[]?; .type=="Ready" and .status=="True"))] | length' 2>/dev/null || echo 0)
        K3S_VERSION_RESULT=$(echo "$json" | jq -r '.items[0].status.nodeInfo.kubeletVersion // "unknown"' 2>/dev/null || echo "unknown")
    fi
}

# GitLab application health, TLS verified (no -k); callers treat 200 as healthy.
# Try the internal chain (DNS -> Traefik VIP -> GitLab nginx) first, then fall
# back to the external hostname. The internal Traefik->VM leg can stall past the
# timeout even when GitLab is healthy (connection + TLS succeed, the HTTP response
# hangs), which returned a false 000 and gated an otherwise-green run to PARTIAL.
# The external route is a second opinion so only a genuinely-unhealthy GitLab
# reports != 200. Echoes the HTTP status code ("000"/empty on total failure).
probe_gitlab_http() {
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://git.esweiss.com/-/health 2>/dev/null || true)
    # Fall back to the external hostname ONLY on a connection-level failure
    # ("000"/empty) — the transient internal Traefik/ingress blip this guards
    # against. A real HTTP status (incl 4xx/5xx) means GitLab answered, so trust
    # it rather than let an external 200 mask an internal error.
    if [ -z "$code" ] || [ "$code" = "000" ]; then
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://git.ericsweiss.com/-/health 2>/dev/null || true)
    fi
    echo "$code"
}

if [ "${1:-}" = "--json" ]; then
    # Quick health check mode - outputs JSON summary to stdout (built via jq -n at end)

    # Proxmox nodes reachability
    read -r PVE_UP PVE_TOTAL <<< "$(probe_pve_reachable)"

    # K3s nodes. K3S_API_OK distinguishes "local kubectl can't reach the
    # cluster" (collector-side) from "cluster has zero Ready nodes"
    # (catastrophic) in the verdict below — mirroring regular mode's
    # $K3S_API_OK gate. The pods temp file (mktemp to avoid /tmp
    # collision/symlink issues) is consumed by the pod probe just below.
    K3S_PODS_JSON=$(mktemp)
    trap 'rm -f "$K3S_PODS_JSON"' EXIT
    probe_k3s_ready
    K3S_API_OK=$K3S_API_OK_RESULT
    K3S_TOTAL=$K3S_TOTAL_RESULT
    K3S_READY=$K3S_READY_RESULT
    K3S_VERSION=$K3S_VERSION_RESULT

    # K3s pods
    POD_TOTAL=0; POD_RUNNING=0
    if kubectl get pods -A -o json 2>/dev/null > "$K3S_PODS_JSON" && [ -s "$K3S_PODS_JSON" ]; then
        POD_TOTAL=$(jq '.items | length' "$K3S_PODS_JSON" 2>/dev/null || echo 0)
        POD_RUNNING=$(jq '[.items[] | select(.status.phase=="Running" or .status.phase=="Succeeded")] | length' "$K3S_PODS_JSON" 2>/dev/null || echo 0)
    fi

    # ZFS pool health — aggregate across ALL reachable Proxmox hosts (a
    # degraded local-ssd on a compute node must not hide behind a healthy
    # NAS). "detail" also builds the pool list (first reachable host's pools).
    probe_zfs_degraded detail
    ZFS_DEGRADED=$ZFS_DEGRADED_RESULT
    ZFS_POOLS=$ZFS_POOLS_RESULT

    # Flux readiness — count Kustomizations and HelmReleases that are NOT Ready=True.
    FLUX_NOT_READY=$(probe_flux_not_ready)

    # Recent warning events (last hour). Spikes here often surface Flux/HelmRelease
    # / scheduling issues before the explicit Ready=False alerts trip.
    WARNING_EVENTS=$(probe_warning_events)

    # GitLab application health through the full delivery chain (internal
    # DNS -> Traefik VIP -> GitLab nginx). GitLab is the GitOps source of
    # truth, so its health gates green (degrades; never catastrophic).
    # TLS is verified (no -k): a broken cert chain or failed rotation is
    # a real degradation this probe should surface, not mask.
    GITLAB_OK=0
    GITLAB_HTTP=$(probe_gitlab_http)
    [ "$GITLAB_HTTP" = "200" ] && GITLAB_OK=1

    # Collector context — distinguishes "cluster genuinely unhealthy" (e.g.
    # nodes truly unreachable) from "collector misconfigured" (e.g. wrong
    # kube_context, no ssh-agent keys, run from a host without LAN access).
    # A `healthy: false` with `ssh_agent_keys: 0` is operator-side, not
    # cluster-side. Every value below tolerates the "unset / not available"
    # case so the script never aborts because (e.g.) ssh-agent isn't running.
    CTX_HOST=$(hostname -s 2>/dev/null || echo "unknown")
    CTX_USER=$(id -un 2>/dev/null || echo "unknown")
    CTX_KUBECONFIG="${KUBECONFIG:-}"
    CTX_KUBE_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "none")
    # Count loaded identities (lines starting with a bit count); `|| true`
    # absorbs ssh-add's non-zero exit when no agent/identities exist.
    CTX_SSH_AGENT_KEYS=$({ ssh-add -l 2>/dev/null || true; } | awk '/^[0-9]+ /{c++} END{print c+0}')
    # `|| true` (no pipeline) so neither a git failure nor pipefail aborts the
    # --json run before the ${CTX_GIT_SHA:-unknown} fallback below — e.g. when
    # the script is copied onto a host that isn't a git checkout.
    CTX_GIT_SHA=$(git -C "$(dirname "$0")/.." rev-parse --short=12 HEAD 2>/dev/null || true)
    CTX_GIT_SHA="${CTX_GIT_SHA:-unknown}"

    # Tri-state (mutually exclusive): healthy / degraded / catastrophic,
    # decided by classify_json (collect-state-lib.sh, unit-tested); Warning
    # events are advisory and do not gate green.
    JSON_VERDICT=$(classify_json "$PVE_UP" "$PVE_TOTAL" "$K3S_API_OK" \
        "$K3S_READY" "$K3S_TOTAL" "$FLUX_NOT_READY" "$ZFS_DEGRADED" "$GITLAB_OK")
    jq -n \
        --arg verdict "$JSON_VERDICT" \
        --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson pve_up "$PVE_UP" \
        --argjson pve_total "$PVE_TOTAL" \
        --argjson k3s_ready "$K3S_READY" \
        --argjson k3s_total "$K3S_TOTAL" \
        --argjson k3s_api_ok "$K3S_API_OK" \
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
            healthy: ($verdict == "healthy"),
            degraded: ($verdict == "degraded"),
            proxmox: { reachable: $pve_up, total: $pve_total },
            k3s: { nodes_ready: $k3s_ready, nodes_total: $k3s_total, api_reachable: $k3s_api_ok, version: $k3s_version, pods_running: $pod_running, pods_total: $pod_total },
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
# Non-Proxmox hosts are addressed by IP, not bare hostname: only the 6 Proxmox
# hosts are on Tailscale (MagicDNS), so bare k3s/smtp/gitlab names resolve only
# on-LAN. Using IPs lets a remote/--json run reach them over the tailnet subnet
# route instead of false-failing the coverage gate on "could not resolve host".
# All rosters below are sourced from hosts.env (generated from hosts.yml).
read -ra DNS_HOSTS <<< "$DNS_IPS"   # dns-01, dns-02
read -ra MAIL_HOSTS <<< "$MAIL_IPS"  # smtp-relay
GITLAB_HOST="$GITLAB_IP"  # gitlab VM on pve-nas-01
PLEX_HOST="$PLEX_IP"  # plex LXC (addressed by IP; short name hits the Traefik VIP)
NEXTCLOUD_HOST="$NEXTCLOUD_IP"  # nextcloud VM (NAS-pinned, docker-compose)
IMMICH_HOST="$IMMICH_IP"  # immich VM (NAS-pinned, docker-compose)
IMMICH_ML_HOST="$IMMICH_ML_IP"  # immich-ml GPU LXC (NAS-pinned, docker-compose)
# 9-node k3s cluster (3 servers + 6 agents), servers first then agents.
read -ra K3S_HOSTS <<< "$K3S_SERVERS $K3S_AGENTS"
HOME_ASSISTANT_HOST="$HOME_ASSISTANT_IP"  # home (HAOS VM)

# Flag to avoid collecting cluster-wide k3s data multiple times (runs on first server node only)
K3S_CLUSTER_COLLECTED=false

# Run-quality tracking
# Counters are incremented during collection; a status header is
# rendered at the end of the run and the script exits non-zero when
# coverage falls below the floor (see status logic at end of script).
HOSTS_TOTAL=0     # number of host SSHes attempted across all sections
HOSTS_OK=0        # number of host SSHes that returned rc=0
K3S_API_OK=false  # set true if `kubectl get nodes` succeeds locally
COVERAGE_FLOOR_PCT=50  # below this, the run is FAILED and CLUSTER_STATUS.txt is NOT overwritten
# Specialised collectors (Proxmox/DNS/mail/k3s/GitLab/compose/HAOS) open a
# SECOND ssh session per host that the host counters above never saw. Their
# failures used to land in the artifact as a bare "Failed (rc=N)" line while the
# header still read "Status: OK / Hosts reachable: 22/22" — a whole-estate loss
# of the ZFS, firewall and HA sections with a green verdict. run_section wraps
# every one of them so the verdict can see them.
SECTIONS_TOTAL=0
SECTIONS_OK=0

# Redaction (REDACT_PATTERNS + redact_file) is sourced from
# collect-state-lib.sh so the secret-leak guard has unit tests.

# Remote-body prelude: the pure section emitters from collect-state-lib.sh,
# shipped to the remote shell via `declare -f` (same mechanism as
# firewall_guest_fw_list). Every remote heredoc is piped through this so
# `cs_emit` / `cs_capped` are defined host-side.
remote_prelude() {
    declare -f cs_capped cs_emit
}

# run_section <label> <command...> — run a specialised collector, record
# whether it succeeded, and annotate the artifact when it did not. The command
# is expected to write its own section body to stdout.
run_section() {
    local label=$1
    shift
    local rc
    SECTIONS_TOTAL=$((SECTIONS_TOTAL + 1))
    set +e
    "$@"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        SECTIONS_OK=$((SECTIONS_OK + 1))
    else
        echo "!!! SECTION INCOMPLETE: ${label} (rc=${rc}) — this run's verdict is downgraded"
    fi
    return 0
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
    ssh_output=$( { remote_prelude; cat << 'REMOTE_EOF'
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
systemctl list-units --type=service --state=running --no-pager 2>/dev/null | cs_capped 60 "none"
echo ""
echo "--- Failed Units ---"
systemctl --failed --no-legend --no-pager 2>/dev/null | cs_emit "none"
echo ""
echo "--- Timers ---"
# All scheduled units, not just the few named ones the per-host collectors
# report — surfaces a disabled/failed/drifted timer (e.g. the daily 07:00
# swap-clean on the NAS) that would otherwise be invisible in the snapshot.
# Uncapped: the previous head -30 sat at 29/30 on the NAS, one new unit away
# from silently dropping restic-offsite-verify.timer off the bottom.
systemctl list-timers --all --no-pager 2>/dev/null | cs_emit "none"
echo ""
echo "--- Recent Error Sources (journalctl -p err, last 1 day; counts only) ---"
# Identifier + count ONLY — never message bodies: raw journal errors can carry
# tokens/URLs/PII the generic redaction doesn't key on (same policy as the
# GitLab/HA log exclusions). Enough to see WHICH service is erroring and how
# loudly; read the messages on the host when needed.
_err_sources=$(journalctl -p err -b --since '-1 day' --no-pager -o short 2>/dev/null \
    | grep -v '^--' | awk '{print $5}' | sed 's/\[[0-9]*\]:*$//;s/:$//' \
    | sort | uniq -c | sort -rn | head -15 || true)
[ -n "$_err_sources" ] && printf '%s\n' "$_err_sources" || echo "none"
echo ""
echo "--- Disk Usage ---"
# Include all mounted filesystems, not just those starting with /
# LXC containers use rootfs/overlay paths that don't start with /
# Uncapped: the previous head -20 was clipping pve-nas-01's filesystem
# inventory at exactly 20 rows — the list a DR rebuild reads.
df -h 2>/dev/null | grep -vE '^(tmpfs|devtmpfs|udev|overlay$)' | cs_emit "no filesystems reported"
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
    } | ssh "${SSH_OPTS[@]}" "${user}@${host}" bash 2>&1 )
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

    # Inject the pure firewall-enumeration helper (collect-state-lib.sh, unit-
    # tested) ahead of the remote body via `declare -f`, then stream the quoted
    # body (remote vars intact) so the host runs the same tested code.
    local rc=0
    { remote_prelude; declare -f firewall_guest_fw_list; cat << 'EOF'
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
sudo cat /etc/pve/firewall/cluster.fw 2>/dev/null | grep --no-group-separator -A 20 '\[IPSET' | cs_capped 200 "No firewall config"
echo ""
echo "--- Firewall Guest Rules ---"
# Enumerate every per-guest firewall config present on this host rather than a
# hand-maintained VMID list (which silently dropped new guests like nextcloud/
# immich/windows). cluster.fw is dumped above; sudo find because the
# /etc/pve/firewall/*.fw files are root:www-data 0640.
while IFS= read -r fw; do
    [ -n "$fw" ] || continue
    vmid=$(basename "$fw" .fw)
    echo "Guest ${vmid}:"
    sudo cat "$fw" 2>/dev/null || echo "  Cannot read"
done < <(sudo find /etc/pve/firewall -maxdepth 1 -name '*.fw' 2>/dev/null | firewall_guest_fw_list)
echo ""
echo "--- Bond Interfaces ---"
# active-backup bond MAC-flap guard: all_slaves_active must stay 0
# (nic_tuning_bond_asa_guard, docs/34). Surfaced so a regression back to 1 is
# visible in the snapshot.
found_bond=0
for b in /sys/class/net/bond*; do
    [ -d "$b" ] || continue
    found_bond=1
    bn=$(basename "$b")
    echo "  ${bn}: mode=$(awk '{print $1}' "$b/bonding/mode" 2>/dev/null) all_slaves_active=$(cat "$b/bonding/all_slaves_active" 2>/dev/null) active_slave=$(cat "$b/bonding/active_slave" 2>/dev/null)"
done
[ "$found_bond" -eq 0 ] && echo "  no bonds configured on this host"
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
# Uncapped: the previous head -50 clipped pve-nas-01's dataset list at exactly
# 50 rows, dropping the entire tank pool (media, backups, proxmox, immich-data)
# from the snapshot with no marker — the inventory a DR rebuild reads.
zfs list -o name,mountpoint,used,avail 2>/dev/null | cs_emit "No ZFS"
echo ""
echo "--- ZFS Snapshot Recency (newest per dataset) ---"
# The local half of the backup chain: archsync/autosnapshot recency per dataset.
# Without it a stalled snapshot regime is invisible in the artifact.
zfs list -t snapshot -H -o name,creation -s creation 2>/dev/null \
    | awk -F'\t' '{split($1,a,"@"); newest[a[1]]=$2} END {for (d in newest) print "  " d ": " newest[d]}' \
    | sort | cs_emit "  No ZFS snapshots"
echo ""
echo "--- ZFS Encryption Keystatus (encryption roots) ---"
# Report only encryption roots — the boot-safety signal zfs-mount-encrypted.sh
# gates on. A raw dataset dump drowns the tank/ssd rows in snapshot noise.
zfs list -H -o name,encryptionroot,keystatus -t filesystem,volume 2>/dev/null \
    | awk -F'\t' '$2 != "-" && $2 != "" && $1 == $2 {print "  " $1 ": " $3}' | head -20
echo "  (archive/* keystatus=unavailable is expected: raw zfs send -w backup targets — docs/32)"
echo ""
echo "--- SMART Status ---"
sudo systemctl is-active smartd 2>/dev/null || echo "smartd not active"
echo ""
echo "--- SMART Health + Pending/Reallocated Sectors (SATA) ---"
for d in /dev/sd?; do
    [ -b "$d" ] || continue
    h=$(sudo smartctl -H "$d" 2>/dev/null | grep -Eo 'PASSED|FAILED' | head -1)
    v=$(sudo smartctl -A "$d" 2>/dev/null | awk '/Reallocated_Sector_Ct|Current_Pending_Sector/ {printf "%s=%s ", $2, $10}')
    [ -n "$h$v" ] && echo "  $d: ${h:-no-verdict} $v"
done
echo ""
echo "--- SMART Health (NVMe) ---"
# The ATA attribute awk above matches nothing in NVMe output; pull the
# NVMe health-log fields instead.
for d in /dev/nvme[0-9]n1; do
    [ -b "$d" ] || continue
    h=$(sudo smartctl -H "$d" 2>/dev/null | grep -Eo 'PASSED|FAILED' | head -1)
    v=$(sudo smartctl -A "$d" 2>/dev/null | awk -F': *' '/Critical Warning|Percentage Used|Media and Data Integrity Errors/ {printf "%s=%s ", $1, $2}')
    [ -n "$h$v" ] && echo "  $d: ${h:-no-verdict} $v"
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
# stderr redirected on journalctl itself (it was previously attached to `tail`,
# leaking journalctl's errors into the captured stream).
journalctl -u media-mover.service --since "1 day ago" --no-pager 2>/dev/null \
    | tail -10 | cs_emit "No recent media-mover logs"
echo ""
echo "--- LXC Containers ---"
sudo pct list 2>/dev/null | cs_emit "No LXC containers"
echo ""
echo "--- VM List ---"
sudo qm list 2>/dev/null | cs_emit 'Cannot list VMs'
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
# Query cluster to find which node hosts VM 154 (same || true guard as the Plex lookup above)
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
    grep --no-group-separator -A 50 '^plugins=(' ~/.zshrc 2>/dev/null | sed -n '/^plugins=(/,/)/p' | cs_capped 20 "Not found"
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
echo "--- Backup Freshness (NAS only: vzdump + archive replication + offsite) ---"
if [ -d /mnt/tank/proxmox/dump ]; then
    echo "Newest vzdump archives:"
    # Glob must expand under root (the dump dir is not eric-readable).
    sudo sh -c 'ls -lt /mnt/tank/proxmox/dump/*.zst 2>/dev/null' | cs_capped 5 "  No vzdump archives found"
    echo "archive-backup timer:"
    # Cap 5, not 3: `systemctl list-timers <unit> --all` emits header + timer +
    # blank + "N timers listed." = 4 lines, so a cap of 3 clipped the summary and
    # printed a "truncated" marker on a COMPLETE section. The marker is this
    # artifact's trust signal — a clipped section must be distinguishable from a
    # complete one — so a false positive on the backup-freshness block is exactly
    # the wrong place for it. 5 matches the restic-offsite line below, which
    # renders cleanly.
    systemctl list-timers archive-backup.timer --all --no-pager 2>/dev/null | cs_capped 5 "  No archive-backup timer"
    echo "archive-backup metrics:"
    cat /var/lib/node_exporter/archive_backup.prom 2>/dev/null | cs_emit "  No archive-backup metrics"
    # restic -> Backblaze B2 is the last line of defence (docs/42) and was
    # absent from this artifact entirely: the offsite chain appeared only as a
    # timer schedule in the generic list, so a chain that had not produced a
    # snapshot in weeks still read as fine here.
    echo "restic-offsite timers:"
    systemctl list-timers 'restic-offsite*' --all --no-pager 2>/dev/null | cs_capped 5 "  No restic-offsite timers"
    echo "restic-offsite metrics:"
    cat /var/lib/node_exporter/restic_offsite.prom 2>/dev/null | cs_emit "  No restic-offsite metrics"
    echo "restic-offsite verify metrics:"
    cat /var/lib/node_exporter/restic_offsite_verify.prom 2>/dev/null | cs_emit "  No restic-offsite verify metrics"
    echo "backup artifact freshness (per-app landing zone):"
    cat /var/lib/node_exporter/backup_artifact_mtime.prom 2>/dev/null | cs_emit "  No backup-artifact metrics"
    echo "newest artifact per app (names, not just mtimes — a config-file copy"
    echo "  satisfies the mtime collector but is not a restorable dump):"
    sudo sh -c 'for d in /mnt/tank/backups/apps/*/; do [ -d "$d" ] || continue; n=$(ls -t "$d" 2>/dev/null | head -1); echo "  $(basename "$d"): ${n:-<empty>}"; done' 2>/dev/null \
        | cs_emit "  No per-app backup landing dirs"
else
    echo "Not the NAS (no /mnt/tank/proxmox/dump); skipped"
fi
echo ""
EOF
    } | ssh "${SSH_OPTS[@]}" "eric@${host}" bash 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || echo "Failed (rc=$rc)"
    return "$rc"
}

collect_dns() {
    local host=$1
    echo "=== DNS-specific: $host ==="

    local rc=0
    { remote_prelude; cat << 'EOF'
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
user_rules_count=$(sudo grep -E -c '^[[:space:]]*-.*dnsrewrite|^[[:space:]]*-.*@@' /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null) || user_rules_count=0
echo "User rules: $user_rules_count"
echo ""
echo "--- AdGuard Rewrites ---"
# Rewrites are stored under dns.rewrites in the YAML, count entries with '- domain:' key
rewrites_count=$(sudo grep -E -c '^[[:space:]]*- domain:' /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null) || rewrites_count=0
echo "DNS rewrites: $rewrites_count"
# The table itself, not just the count: this IS the split-horizon mapping half
# the estate resolves through, and a count alone cannot show a wrong or missing
# entry. domain/answer pairs only (no credentials in this block).
sudo grep -E '^[[:space:]]*(- domain|[[:space:]]+answer):' /opt/AdGuardHome/AdGuardHome.yaml 2>/dev/null \
    | sed 's/^[[:space:]]*/  /' | cs_emit "  No rewrites configured"
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
sudo openssl x509 -enddate -noout -in /opt/AdGuardHome/certs/fullchain.pem 2>/dev/null || echo "Cannot read cert notAfter"
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
systemctl list-timers 'adguardhome-sync*' --all --no-pager 2>/dev/null | cs_emit 'No sync timer found'
echo ""
EOF
    } | ssh "${SSH_OPTS[@]}" "eric@${host}" bash 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || echo "Failed (rc=$rc)"
    return "$rc"
}

collect_mail() {
    local host=$1
    echo "=== Mail-specific: $host ==="

    local rc=0
    { remote_prelude; cat << 'EOF'
echo "--- Postfix Status ---"
systemctl is-active postfix
postconf myhostname mynetworks relayhost 2>/dev/null
echo ""
echo "--- Listening Ports ---"
ss -lntp | grep -E ':25|:587'
echo ""
echo "--- TLS Certs ---"
ls -la /etc/postfix/tls/ 2>/dev/null || echo "No TLS dir"
sudo openssl x509 -enddate -noout -in /etc/postfix/tls/fullchain.pem 2>/dev/null || echo "Cannot read cert notAfter"
echo ""
echo "--- Mail Queue ---"
sudo postqueue -p 2>/dev/null | tail -1 | cs_emit 'Cannot check mail queue'
echo ""
EOF
    } | ssh "${SSH_OPTS[@]}" "eric@${host}" bash 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || echo "Failed (rc=$rc)"
    return "$rc"
}

collect_k3s() {
    local host=$1
    echo "=== K3s-specific: $host ==="

    # Per-node data (always collected)
    local rc=0
    { remote_prelude; cat << 'EOF'
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
    } | ssh "${SSH_OPTS[@]}" "eric@${host}" bash 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || echo "Failed (rc=$rc)"

    # Cluster-wide data (collected once from the first server node)
    # Uses exit codes: 0 = collected, 2 = not a server (try next), other = SSH/remote failure
    local cluster_rc
    if [ "$K3S_CLUSTER_COLLECTED" = "false" ]; then
        # Temporarily disable set -e so we can check the exit code
        set +e
        { remote_prelude; cat << 'EOF'
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
    # Uncapped: a head cap silently cut namespaces (reloader, vpa-system)
    # that appear in no later per-namespace section.
    sudo k3s kubectl get pods -A 2>/dev/null || echo "Cannot get pods"
    echo ""
    echo "--- Pods not Running/Completed ---"
    unhealthy_pods=$(sudo k3s kubectl get pods -A --no-headers 2>/dev/null | awk '$4 != "Running" && $4 != "Completed"')
    if [ -n "$unhealthy_pods" ]; then
        echo "$unhealthy_pods"
    else
        echo "  none"
    fi
    echo ""
    echo "--- Services ---"
    sudo k3s kubectl get svc -A 2>/dev/null || echo "Cannot get services"
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
    sudo k3s kubectl get certificate -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,NOTAFTER:.status.notAfter' 2>/dev/null || echo "Cannot get certificates"
    echo ""
    echo "--- MetalLB Configuration ---"
    sudo k3s kubectl get ipaddresspool -n metallb-system 2>/dev/null || echo "Cannot get IP pools"
    sudo k3s kubectl get l2advertisement -n metallb-system 2>/dev/null || echo "Cannot get L2 advertisements"
    echo ""
    echo "--- Flux Kustomizations ---"
    # SUSPENDED is not in the default columns: a suspended Kustomization keeps
    # reporting its last Ready=True forever, so a cluster frozen weeks ago read
    # as healthy here. Custom columns make the freeze visible in the artifact
    # (probe_flux_not_ready counts it toward the verdict).
    sudo k3s kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A \
        -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,SUSPENDED:.spec.suspend,READY:.status.conditions[?(@.type=="Ready")].status,MESSAGE:.status.conditions[?(@.type=="Ready")].message' \
        2>/dev/null || echo "Cannot get Flux Kustomizations"
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
    sudo k3s kubectl get helmreleases -A \
        -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,SUSPENDED:.spec.suspend,READY:.status.conditions[?(@.type=="Ready")].status' \
        2>/dev/null || echo "Cannot get HelmReleases"
    echo ""
    echo "--- Pinned versions (cluster-versions ConfigMap) ---"
    # The substitution source Flux resolves ${x_version} placeholders from.
    # Without it the artifact shows running images but nothing to compare them
    # against, so a failed/stale sync-versions is invisible in a DR snapshot.
    sudo k3s kubectl get configmap cluster-versions -n flux-system -o jsonpath='{.data}' 2>/dev/null \
        | tr ',' '\n' | cs_emit "Cannot get cluster-versions ConfigMap"
    echo ""
    echo "--- Running images (all namespaces, deduped) ---"
    sudo k3s kubectl get pods -A -o jsonpath='{range .items[*].spec.containers[*]}{.image}{"\n"}{end}' 2>/dev/null \
        | sort -u | cs_emit "Cannot list running images"
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
    # Capture-and-test rather than `... | wc -l | xargs echo`: a kubectl failure
    # in that pipeline printed "Dashboard ConfigMaps: 0", indistinguishable from
    # a genuine zero.
    dashboards=$(sudo k3s kubectl get configmap -n observability -l grafana_dashboard --no-headers 2>/dev/null)
    if [ -n "$dashboards" ]; then
        echo "Dashboard ConfigMaps: $(printf '%s\n' "$dashboards" | wc -l | tr -d ' ')"
    else
        echo "Dashboard ConfigMaps: cannot query (kubectl failed or none labelled)"
    fi
    echo ""
    echo "--- Alertmanager Active Alerts ---"
    # Pod resolved by label, not the StatefulSet ordinal: a rename used to make
    # this section silently blank instead of saying it could not query.
    am_pod=$(sudo k3s kubectl -n observability get pods -l app.kubernetes.io/name=alertmanager \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -n "$am_pod" ]; then
        sudo k3s kubectl -n observability exec "$am_pod" -c alertmanager -- \
            amtool --alertmanager.url=http://localhost:9093 alert query 2>/dev/null \
            | cs_capped 50 "No active alerts (or amtool query failed)"
    else
        echo "Cannot query alertmanager (no pod matched app.kubernetes.io/name=alertmanager)"
    fi
    echo ""
    echo "--- Cluster Warning Events (last 20) ---"
    sudo k3s kubectl get events -A --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null \
        | tail -20 | cs_emit "Cannot get events"
else
    echo "(Not a k3s server node, skipping cluster-wide data)"
    exit 2
fi
echo ""
EOF
        } | ssh "${SSH_OPTS[@]}" "eric@${host}" bash 2>/dev/null
        cluster_rc=$?
        set -e
        if [ "$cluster_rc" -eq 0 ]; then
            K3S_CLUSTER_COLLECTED=true
        elif [ "$cluster_rc" -eq 2 ]; then
            echo "No cluster-wide data on $host (not a server), will try next server node"
        elif [ "$cluster_rc" -eq 3 ]; then
            echo "kubectl API unresponsive on $host, will retry on next server node"
        else
            echo "Failed to collect cluster-wide data from $host (rc=$cluster_rc), will retry on next server node"
        fi
    fi
    # Only the per-node block's result gates this section: "not a k3s server"
    # is the normal path on 6 of 9 nodes. Whether the cluster-wide block landed
    # anywhere is checked once, after the loop (K3S_CLUSTER_COLLECTED).
    return "$rc"
}

# Alloy log-shipper status across all hosts, checked from the collector
# machine (the k3s nodes have no SSH keys to other hosts, so this cannot
# run nested inside collect_k3s).
collect_alloy_status() {
    echo "=== Alloy host log shippers ==="
    # plex by IP: the short name resolves through the AdGuard rewrite to the
    # Traefik VIP, not the LXC (same trap as DNS_HOSTS).
    for host in "${PVE_HOSTS[@]}" "${DNS_HOSTS[@]}" "${MAIL_HOSTS[@]}" "$GITLAB_HOST" "$NEXTCLOUD_HOST" "$IMMICH_HOST" "$IMMICH_ML_HOST" "$PLEX_HOST" "${K3S_HOSTS[@]}"; do
        local status
        status=$(ssh "${SSH_OPTS_PROBE[@]}" "eric@${host}" 'systemctl is-active alloy 2>/dev/null' 2>/dev/null) || true
        echo "  $host: ${status:-unreachable}"
    done
    echo ""
}

collect_home_assistant() {
    local host=$1
    echo "=== Home Assistant (HAOS VM): $host ==="

    # Note: Home Assistant OS VM status collected from Proxmox host
    # Configuration files collected via SSH if SSH add-on is configured

    # Check if SSH is accessible (requires SSH add-on on port 22222).
    # The reachability probe keeps its own shorter ConnectTimeout=5 (distinct
    # from SSH_OPTS_PROBE's 3s and SSH_OPTS's 10s); the collection session
    # reuses SSH_OPTS plus the non-standard HAOS SSH add-on port.
    local rc=0
    if ssh -o ConnectTimeout=5 -o BatchMode=yes -p 22222 "root@${host}" "echo test" &>/dev/null; then
        { remote_prelude; cat << 'EOF'
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
ls -la /config/*.yaml 2>/dev/null | cs_capped 20 "Config directory inaccessible"
echo ""
echo "--- Configuration Check ---"
ha core check 2>/dev/null || echo "Config check unavailable"
echo ""
echo "--- Custom Components ---"
ls -la /config/custom_components/ 2>/dev/null || echo "No custom components or directory inaccessible"
echo ""
echo "--- Recent Logs ---"
# HA core logs are NOT collected: integration tracebacks/warnings can carry
# API keys, OAuth tokens, and entity/PII data that the generic redaction
# patterns don't key on — same policy as the GitLab log exclusion (collect_gitlab).
# To view manually, run on the HAOS host: tail -100 /config/home-assistant.log
echo "HA core logs excluded from state collection (may contain tokens/PII)"
echo ""
echo "--- Network Info ---"
ha network info 2>/dev/null || echo "Network info unavailable"
echo ""
echo "--- HA Native Backups (the artifacts that ride into B2) ---"
# The HA tars are the only backup of the HAOS VM's state; without this the
# artifact showed HA config but never whether a backup exists or how old it is.
ha backups 2>/dev/null | cs_capped 30 "Backup list unavailable"
echo ""
EOF
        } | ssh "${SSH_OPTS[@]}" -p 22222 "root@${host}" bash 2>/dev/null || rc=$?
        [ "$rc" -eq 0 ] || echo "SSH command failed (rc=$rc)"
    else
        echo "SSH not accessible (port 22222)"
        echo "Requires SSH add-on to be installed and configured"
        echo "Collecting VM status from Proxmox instead..."
        rc=1
    fi
    echo ""
    return "$rc"
}

collect_gitlab() {
    local host=$1
    echo "=== GitLab-specific: $host ==="

    local rc=0
    { remote_prelude; cat << 'EOF'
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
    sudo /opt/gitlab/bin/gitlab-ctl status 2>/dev/null | cs_capped 30 "gitlab-ctl status failed"
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
sudo openssl x509 -enddate -noout -in /etc/gitlab/ssl/fullchain.pem 2>/dev/null || echo "Cannot read cert notAfter"
echo ""
echo "--- GitLab Disk Usage ---"
echo "Repository storage:"
sudo du -sh /mnt/gitlab-repos/git-data 2>/dev/null || echo "  External storage not configured or inaccessible"
echo "Backups:"
sudo du -sh /var/opt/gitlab/backups 2>/dev/null || echo "  Backup dir inaccessible"
# The backups dir is 0700 git:git, so both the glob expansion and the ls must
# run under root (an unprivileged glob passes the literal pattern through and
# ls fails); capture-and-test because a `|| echo` after a pipeline ending in
# head never fires (the pipeline exits with head's 0).
backup_list=$(sudo sh -c 'ls -lt /var/opt/gitlab/backups/*_gitlab_backup.tar 2>/dev/null' | head -3)
if [ -n "$backup_list" ]; then
    echo "$backup_list"
else
    echo "  No backup files found"
fi
# Configured vs EFFECTIVE backup path. gitlab.rb only takes effect after
# `gitlab-ctl reconfigure`; a missed handler left the tarballs on the VM OS
# disk for days while every backup metric stayed green. Both values in the
# artifact make that divergence visible at a glance.
echo "Backup path (gitlab.rb):"
sudo grep -E "^gitlab_rails\['backup_path'\]" /etc/gitlab/gitlab.rb 2>/dev/null | cs_emit "  not set (default /var/opt/gitlab/backups)"
echo "Backup path (effective, gitlab.yml):"
sudo grep -A1 '^  backup:' /var/opt/gitlab/gitlab-rails/etc/gitlab.yml 2>/dev/null | grep 'path:' | cs_emit "  cannot read effective config"
echo "Offsite landing zone (/mnt/backups-offsite):"
sudo sh -c 'ls -lt /mnt/backups-offsite 2>/dev/null' | cs_capped 6 "  landing zone empty or not mounted"
echo "Backup metrics (gitlab_backup.prom):"
cat /var/lib/node_exporter/gitlab_backup.prom 2>/dev/null | cs_emit "  No backup metrics"
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
    } | ssh "${SSH_OPTS[@]}" "eric@${host}" bash 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || echo "Failed (rc=$rc)"
    return "$rc"
}

# NAS-pinned single-VM docker-compose apps (Nextcloud .156, Immich .157,
# Immich-ML .158). collect_host (in the main loop) captures base host stats;
# this adds the app block — compose project status, host-nginx TLS cert expiry,
# and pg_dump/backup freshness — mirroring collect_gitlab. Values pass as env
# over ssh (simple paths, no quoting hazard); "-" skips a section an app lacks
# (Immich-ML has no nginx front end and no backup timer, but does have a health
# endpoint).
#   $1 host  $2 label  $3 compose_dir  $4 nginx_cert  $5 backup_glob
#   $6 backup_timer  $7 backup_prom  $8 health_url        (- to skip a section)
collect_compose_app() {
    local host=$1 label=$2 compose_dir=$3 nginx_cert=$4 backup_glob=$5
    local backup_timer=$6 backup_prom=$7 health_url=$8
    echo "=== ${label}-specific: $host ==="

    # Which optional sections render is decided here (compose_active_sections,
    # collect-state-lib.sh, unit-tested) from the "-" sentinels, and passed to
    # the remote body as a comma-joined membership list.
    local compose_sections
    compose_sections=$(compose_active_sections "$health_url" "$nginx_cert" "$backup_timer" "$backup_prom")

    local rc=0
    { remote_prelude; cat << 'EOF'
echo "--- ${APP_LABEL} Compose Project Status ---"
if [ -f "${COMPOSE_DIR}/docker-compose.yml" ]; then
    sudo sh -c "cd '${COMPOSE_DIR}' && docker compose ps" 2>/dev/null || echo "  docker compose ps failed"
else
    echo "  Compose project not found at ${COMPOSE_DIR}"
fi
echo ""
if [[ ",${COMPOSE_SECTIONS}," == *,health,* ]]; then
    echo "--- ${APP_LABEL} Health Check ---"
    if command -v curl >/dev/null 2>&1; then
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${HEALTH_URL}" 2>/dev/null)
        echo "Health endpoint (${HEALTH_URL}): HTTP ${code:-unreachable}"
    else
        echo "curl not available"
    fi
    echo ""
fi
if [[ ",${COMPOSE_SECTIONS}," == *,nginx,* ]]; then
    echo "--- ${APP_LABEL} Host nginx TLS ---"
    systemctl is-active nginx 2>/dev/null || echo "nginx not active"
    sudo openssl x509 -enddate -noout -in "${NGINX_CERT}" 2>/dev/null || echo "Cannot read cert notAfter (${NGINX_CERT})"
    echo ""
fi
if [[ ",${COMPOSE_SECTIONS}," == *,backup,* ]]; then
    echo "--- ${APP_LABEL} Backup Freshness ---"
    # Cap 5: list-timers for a single unit is 4 lines (header + timer + blank +
    # "N timers listed."), so 3 clipped the summary and emitted a false
    # "truncated" marker on a complete section — see the archive-backup timer
    # above.
    systemctl list-timers "${BACKUP_TIMER}" --all --no-pager 2>/dev/null | cs_capped 5 "  No ${BACKUP_TIMER}"
    # Dump dir is root-owned; the glob + ls must run under root (an unprivileged
    # glob passes the literal pattern through). Capture-and-test because a
    # `|| echo` after a head pipeline never fires (same pattern as collect_gitlab).
    backup_list=$(sudo sh -c "ls -lt ${BACKUP_GLOB} 2>/dev/null" | head -3)
    if [ -n "$backup_list" ]; then
        echo "$backup_list"
    else
        echo "  No backup files found (${BACKUP_GLOB})"
    fi
    if [[ ",${COMPOSE_SECTIONS}," == *,metrics,* ]]; then
        echo "Backup metrics ($(basename "${BACKUP_PROM}")):"
        cat "${BACKUP_PROM}" 2>/dev/null | cs_emit "  No backup metrics"
    fi
    echo ""
fi
EOF
    } | ssh "${SSH_OPTS[@]}" "eric@${host}" \
        "APP_LABEL=$label" "COMPOSE_DIR=$compose_dir" "NGINX_CERT=$nginx_cert" \
        "BACKUP_GLOB=$backup_glob" "BACKUP_TIMER=$backup_timer" \
        "BACKUP_PROM=$backup_prom" "HEALTH_URL=$health_url" \
        "COMPOSE_SECTIONS=$compose_sections" \
        bash 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || echo "Failed (rc=$rc)"
    return "$rc"
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
read -r PVE_REACHABLE_REG PVE_TOTAL_REG <<< "$(probe_pve_reachable)"

# K3s nodes Ready=True count — mirrors --json mode's `k3s_ready`.
# Tolerates kubectl unreachable / malformed JSON → 0. K3S_API_OK is set by
# its own earlier probe above and is intentionally not derived here.
probe_k3s_ready --request-timeout=5s
K3S_NODES_TOTAL_REG=$K3S_TOTAL_RESULT
K3S_NODES_READY_REG=$K3S_READY_RESULT

# Flux readiness — count Kustomizations and HelmReleases NOT Ready=True.
FLUX_NOT_READY_REG=$(probe_flux_not_ready --request-timeout=5s)

# ZFS pool health — aggregate non-ONLINE pools across ALL reachable
# Proxmox hosts. Mirrors the --json branch's loop (count only; no pool detail).
probe_zfs_degraded
ZFS_DEGRADED_REG=$ZFS_DEGRADED_RESULT

# Recent warning events (last hour). Same field-selector/jq as --json.
WARNING_EVENTS_REG=$(probe_warning_events --request-timeout=5s)

# Firing (non-Watchdog) Alertmanager alerts. Regular-mode only — unlike the
# advisory Warning-event count this DOES gate OK, because an artifact headed
# "Status: OK" while TargetDown is firing is the exact class of lie this
# collector exists to prevent. "unknown" when the query could not run.
ALERTS_FIRING_REG=$(probe_firing_alerts --request-timeout=5s)

# GitLab application health — mirrors the --json probe (full chain through
# the internal Traefik VIP, TLS verified). Unhealthy GitLab downgrades OK
# to PARTIAL.
GITLAB_OK_REG=0
GITLAB_HTTP_REG=$(probe_gitlab_http)
[ "$GITLAB_HTTP_REG" = "200" ] && GITLAB_OK_REG=1

{
    for host in "${PROXMOX_HOSTS[@]}"; do
        collect_host "$host"
        run_section "proxmox:$host" collect_proxmox "$host"
        echo ""
    done

    for host in "${DNS_HOSTS[@]}"; do
        collect_host "$host" "eric"
        run_section "dns:$host" collect_dns "$host"
        echo ""
    done

    for host in "${MAIL_HOSTS[@]}"; do
        collect_host "$host" "eric"
        run_section "mail:$host" collect_mail "$host"
        echo ""
    done

    for host in "${K3S_HOSTS[@]}"; do
        collect_host "$host"
        run_section "k3s:$host" collect_k3s "$host"
        echo ""
    done

    # The cluster-wide k3s block is attempted per server until one succeeds;
    # exhausting every server is a section failure of its own (that block
    # carries Flux/ESO/cert-manager/alert state nothing else collects).
    SECTIONS_TOTAL=$((SECTIONS_TOTAL + 1))
    if [ "$K3S_CLUSTER_COLLECTED" = "true" ]; then
        SECTIONS_OK=$((SECTIONS_OK + 1))
    else
        echo "!!! SECTION INCOMPLETE: k3s cluster-wide (no server node yielded data) — this run's verdict is downgraded"
    fi

    collect_alloy_status

    # Home Assistant
    run_section "home-assistant:$HOME_ASSISTANT_HOST" collect_home_assistant "$HOME_ASSISTANT_HOST"
    echo ""

    # GitLab
    collect_host "$GITLAB_HOST"
    run_section "gitlab:$GITLAB_HOST" collect_gitlab "$GITLAB_HOST"
    echo ""

    # Nextcloud (.156) — docker-compose app on a NAS-pinned VM
    collect_host "$NEXTCLOUD_HOST"
    run_section "nextcloud:$NEXTCLOUD_HOST" collect_compose_app "$NEXTCLOUD_HOST" nextcloud \
        /mnt/nextcloud-app/compose /etc/ssl/nextcloud/fullchain.pem \
        '/mnt/nextcloud-app/backups/nextcloud-db-*.sql.gz' nextcloud-backup.timer \
        /var/lib/node_exporter/nextcloud_backup.prom -
    echo ""

    # Immich (.157) — docker-compose app on a NAS-pinned VM
    collect_host "$IMMICH_HOST"
    run_section "immich:$IMMICH_HOST" collect_compose_app "$IMMICH_HOST" immich \
        /mnt/immich-app/compose /etc/nginx/ssl/fullchain.pem \
        '/mnt/immich-app/backups/immich-*.sql.gz' immich-backup.timer \
        /var/lib/node_exporter/immich_backup.prom -
    echo ""

    # Immich-ML (.158) — GPU LXC; internal ML service only (no nginx / no backup)
    collect_host "$IMMICH_ML_HOST"
    run_section "immich-ml:$IMMICH_ML_HOST" collect_compose_app "$IMMICH_ML_HOST" immich-ml \
        /opt/immich-ml/compose - - - - http://127.0.0.1:3003/ping
    echo ""

} > "$TEMP_DIR/raw.txt"

# Run-quality classification
# Status is one of OK / PARTIAL / FAILED. FAILED means we don't trust
# the report enough to overwrite CLUSTER_STATUS.txt and the script
# exits non-zero so callers (CI, cron, the operator) see the failure
# loudly. PARTIAL still produces a useful artifact with a banner; OK
# is the all-green path.
if [ "$HOSTS_TOTAL" -gt 0 ]; then
    HOST_COVERAGE_PCT=$((HOSTS_OK * 100 / HOSTS_TOTAL))
else
    HOST_COVERAGE_PCT=0
fi

# Mirrors --json mode's healthy/degraded/catastrophic traffic-light states.
# The legacy names OK/PARTIAL/FAILED are preserved for backward compatibility
# but the *conditions* match the --json branch on the catastrophic signal
# specifically (the host-coverage-floor band stays as a stricter,
# regular-only coverage gate). The verdict conditions live in
# classify_regular (collect-state-lib.sh, unit-tested); Warning events are
# reported in the header but advisory only — they do not block OK.
# PVE_REACHABLE_REG and K3S_NODES_READY_REG mirror --json's `pve_up` and
# `k3s_ready` exactly; the JSON branch distinguishes the collector-side
# kubectl-misconfigured case via `collector_context`.
# Firing alerts and section coverage: "unknown" (kubectl/amtool unavailable)
# maps to 0 for the verdict so a collector-side problem neither promotes nor
# demotes a run — the header still shows the raw value.
ALERTS_FIRING_NUM=$ALERTS_FIRING_REG
case "$ALERTS_FIRING_NUM" in ''|*[!0-9]*) ALERTS_FIRING_NUM=0 ;; esac

STATUS=$(classify_regular "$PVE_REACHABLE_REG" "$K3S_API_OK" \
    "$K3S_NODES_READY_REG" "$K3S_NODES_TOTAL_REG" \
    "$HOSTS_OK" "$HOSTS_TOTAL" "$HOST_COVERAGE_PCT" "$COVERAGE_FLOOR_PCT" \
    "$FLUX_NOT_READY_REG" "$ZFS_DEGRADED_REG" "$GITLAB_OK_REG" \
    "$SECTIONS_OK" "$SECTIONS_TOTAL" "$ALERTS_FIRING_NUM")

# Render final output: status header + raw collection, redaction
# applied to the combined stream.
{
    echo "# Cluster State Collection"
    echo "# Generated: $(date -Iseconds)"
    echo "# Status: $STATUS"
    echo "# Proxmox reachable: $PVE_REACHABLE_REG / $PVE_TOTAL_REG"
    echo "# K3s nodes ready: $K3S_NODES_READY_REG / $K3S_NODES_TOTAL_REG"
    echo "# Hosts reachable: $HOSTS_OK / $HOSTS_TOTAL ($HOST_COVERAGE_PCT%)"
    echo "# Sections collected: $SECTIONS_OK / $SECTIONS_TOTAL (specialised collectors; grep 'SECTION INCOMPLETE')"
    echo "# K3s API reachable: $K3S_API_OK"
    echo "# Flux not reconciling (not-Ready or suspended): $FLUX_NOT_READY_REG"
    echo "# ZFS degraded pools: $ZFS_DEGRADED_REG"
    echo "# GitLab health (/-/health, internal then external): HTTP ${GITLAB_HTTP_REG:-unreachable}"
    echo "# Firing alerts (non-Watchdog): $ALERTS_FIRING_REG"
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

# Update CLUSTER_STATUS.txt with latest state (OK or PARTIAL only).
# Anchored to the repo root, not CWD: running ./scripts/collect-state.sh from
# anywhere else silently updated a different (or no) file.
REPO_ROOT="$(cd "$_SCRIPT_DIR/.." && pwd)"
if cp "$OUTPUT_FILE" "$REPO_ROOT/CLUSTER_STATUS.txt" 2>/dev/null; then
    echo "Updated $REPO_ROOT/CLUSTER_STATUS.txt"
else
    echo "Warning: could not update $REPO_ROOT/CLUSTER_STATUS.txt (directory not writable?)"
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
    # Only remove regular files: `rm -f` on a directory (or other non-regular
    # match) returns non-zero and would abort under set -e (restores the old
    # `find -type f` type-safety the single-ls rewrite dropped).
    [ -f "$file" ] || continue
    rm -f -- "$file"
    COUNT=$((COUNT + 1))
done < <(
    # Single `ls -td` (newest-first by mtime) -> drop the 5 newest. One ls
    # invocation avoids an xargs ARG_MAX split that would sort each batch
    # independently and keep up to 5 PER batch. `-d` keeps ls from descending;
    # `|| true` keeps a no-match (ls non-zero) from aborting under set -e.
    # Filenames are timestamped (no spaces/newlines), so the glob is safe.
    # shellcheck disable=SC2012 # ls -t for mtime sort; names are safe (see above)
    ls -td -- "$STATE_DIR"/cluster-state-*.txt 2>/dev/null \
        | tail -n +6 \
        || true
)
if [ "$COUNT" -gt 0 ]; then
    echo "Cleaned up $COUNT old state files (keeping newest 5)"
fi
