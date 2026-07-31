#!/usr/bin/env bash
# Pure-logic helpers shared by collect-state.sh, extracted so the secret
# redaction and the tri-state health classification that gate CLUSTER_STATUS.txt
# can be unit-tested without a live cluster (scripts/test_collect_state_lib.py,
# same pattern as maintenance-lib.sh).
#
# This file defines patterns and functions only (no top-level side effects) so
# it is safe to `source` from both collect-state.sh and the pytest harness.
# None of the functions call ssh/kubectl/curl themselves.

# secret redaction

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
    's/authorization:[[:space:]]*basic[[:space:]]+[A-Za-z0-9+\/=]+/authorization: Basic <REDACTED>/gi'
    's/(^|[^[:alnum:]])secret[[:space:]]*[:=][[:space:]]*[^[:space:]]+/\1secret: <REDACTED>/gi'
    's/(^|[^[:alnum:]])api_?key[[:space:]]*[:=][[:space:]]*[^[:space:]]+/\1api_key: <REDACTED>/gi'
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
    's/WIREGUARD_(PRIVATE_KEY|PRESHARED_KEY)=[^[:space:]]+/WIREGUARD_\1=<REDACTED>/g'
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

# remote section emitters
# collect-state.sh's remote bodies used to write `producer | head -N || echo
# "none"`. Both halves of that are broken: a pipeline exits with head's status
# so the `|| echo` fallback is dead (a failed probe renders as an EMPTY section,
# indistinguishable from "nothing to report"), and the cap is applied silently
# so a clipped section is indistinguishable from a complete one. These two
# helpers replace the idiom; collect-state.sh injects them into every remote
# body via `declare -f` so the same unit-tested code runs on the host.
#
# cs_capped <cap> <fallback> — read a producer's output on stdin, print at most
# <cap> lines (cap 0 = uncapped), print <fallback> when the producer emitted
# nothing, and append an explicit truncation marker when the cap clipped output.
# The whole stream is consumed (rather than closing the pipe like `head`) so the
# marker can report the real total and the producer never takes SIGPIPE.
cs_capped() {
    local cap=$1 fallback=$2 n=0 line
    # `|| [ -n "$line" ]` keeps a final unterminated line (kubectl -o jsonpath
    # emits one) from being silently dropped by read's non-zero exit.
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        if [ "$cap" -eq 0 ] || [ "$n" -le "$cap" ]; then
            printf '%s\n' "$line"
        fi
    done
    if [ "$n" -eq 0 ]; then
        printf '%s\n' "$fallback"
    elif [ "$cap" -ne 0 ] && [ "$n" -gt "$cap" ]; then
        printf '  ... (truncated: showing %s of %s lines)\n' "$cap" "$n"
    fi
}

# cs_emit <fallback> — uncapped cs_capped, for sections that must never be
# clipped (ZFS datasets, df: the DR-critical inventories) or are short anyway.
cs_emit() {
    cs_capped 0 "$1"
}

# tri-state health classification
# Both collect-state modes feed these classifiers from the shared probes
# (see the SH-3 block in collect-state.sh). The regular/--json differences
# (host-coverage floor, all-collected-hosts strictness) are deliberate and
# documented in the collect-state.sh header; when adding a signal to one
# classifier, mirror it in the other.

# classify_regular <pve_reachable> <k3s_api_ok true|false> <k3s_ready> <k3s_total> \
#                  <hosts_ok> <hosts_total> <coverage_pct> <coverage_floor> \
#                  <flux_not_ready> <zfs_degraded> <gitlab_ok 0|1> \
#                  <sections_ok> <sections_total> <alerts_firing>
# Prints the regular-mode verdict:
#   FAILED  (red)    catastrophic — no Proxmox host reachable, OR K3s API
#                    reachable but zero nodes Ready, OR coverage below floor.
#   OK      (green)  full host coverage, every specialised collector section
#                    collected, all k3s nodes Ready, zero Flux not-ready, zero
#                    non-ONLINE ZFS pools, GitLab healthy, no firing alerts.
#   PARTIAL (yellow) anything else with core infra still up.
# The K3s catastrophic check is gated on k3s_api_ok so a missing/misconfigured
# local kubeconfig produces PARTIAL (visibly degraded) rather than a false
# FAILED that hides the per-host SSH collection.
# sections_ok/sections_total are regular-only (the --json branch runs no
# specialised collectors): a Proxmox/DNS/k3s/GitLab/compose block that failed
# its own SSH used to leave "Failed (rc=N)" in the artifact while the header
# still read OK, because only collect_host fed the host counters.
# alerts_firing counts non-Watchdog Alertmanager alerts; it degrades but never
# fails a run (the artifact is still worth keeping when the cluster is noisy).
classify_regular() {
    local pve_reachable="$1" k3s_api_ok="$2" k3s_ready="$3" k3s_total="$4"
    local hosts_ok="$5" hosts_total="$6" coverage_pct="$7" coverage_floor="$8"
    local flux_not_ready="$9" zfs_degraded="${10}" gitlab_ok="${11}"
    local sections_ok="${12}" sections_total="${13}" alerts_firing="${14}"
    if [ "$pve_reachable" -eq 0 ] \
       || { [ "$k3s_api_ok" = true ] && [ "$k3s_ready" -eq 0 ]; } \
       || [ "$coverage_pct" -lt "$coverage_floor" ]; then
        echo "FAILED"
    elif [ "$hosts_ok" -eq "$hosts_total" ] \
         && [ "$sections_ok" -eq "$sections_total" ] \
         && [ "$k3s_api_ok" = true ] \
         && [ "$k3s_total" -gt 0 ] \
         && [ "$k3s_ready" -eq "$k3s_total" ] \
         && [ "$flux_not_ready" -eq 0 ] \
         && [ "$zfs_degraded" -eq 0 ] \
         && [ "$gitlab_ok" -eq 1 ] \
         && [ "$alerts_firing" -eq 0 ]; then
        echo "OK"
    else
        echo "PARTIAL"
    fi
}

# classify_json <pve_up> <pve_total> <k3s_api_ok true|false> <k3s_ready> <k3s_total> \
#               <flux_not_ready> <zfs_degraded> <gitlab_ok 0|1>
# Prints the --json verdict (mutually exclusive):
#   healthy      green (strict: full Proxmox coverage, zero Flux/ZFS
#                imperfections, GitLab healthy; Warning events are advisory
#                and do not gate green)
#   degraded     yellow — any imperfection with core infra still up (the gate
#                keeps a fully-down cluster from reading as merely degraded)
#   catastrophic red — neither of the above
classify_json() {
    local pve_up="$1" pve_total="$2" k3s_api_ok="$3" k3s_ready="$4" k3s_total="$5"
    local flux_not_ready="$6" zfs_degraded="$7" gitlab_ok="$8"
    if [ "$pve_up" -gt 0 ] && [ "$pve_up" -eq "$pve_total" ] \
       && [ "$k3s_total" -gt 0 ] && [ "$k3s_ready" -eq "$k3s_total" ] \
       && [ "$flux_not_ready" -eq 0 ] && [ "$zfs_degraded" -eq 0 ] \
       && [ "$gitlab_ok" -eq 1 ]; then
        echo "healthy"
    elif { [ "$pve_up" -lt "$pve_total" ] || [ "$k3s_ready" -lt "$k3s_total" ] \
           || [ "$k3s_api_ok" != true ] || [ "$flux_not_ready" -gt 0 ] \
           || [ "$zfs_degraded" -gt 0 ] || [ "$gitlab_ok" -eq 0 ]; } \
         && [ "$pve_up" -gt 0 ] \
         && { [ "$k3s_api_ok" != true ] || [ "$k3s_ready" -gt 0 ]; }; then
        echo "degraded"
    else
        echo "catastrophic"
    fi
}

# collect_compose_app section dispatch
# collect-state.sh's NAS-pinned docker-compose app block (Nextcloud/Immich/
# Immich-ML) renders optional sections only for the apps that have them; a "-"
# argument means "this app lacks that section". Pulling the sentinel→sections
# decision out here lets it be unit-tested without an ssh and keeps the rule in
# one place — collect_compose_app calls this locally and passes the result to the
# remote body as a comma-joined membership list.
#
# compose_active_sections <health_url> <nginx_cert> <backup_timer> <backup_prom>
# Echoes the comma-joined optional sections that render, in output order:
#   health,nginx,backup,metrics
# `metrics` is nested under `backup` (it renders only when the backup section
# does). Any "-" arg drops its section; all "-" prints an empty string.
compose_active_sections() {
    local health_url=$1 nginx_cert=$2 backup_timer=$3 backup_prom=$4
    local out=""
    [ "$health_url" != "-" ] && out="${out:+$out,}health"
    [ "$nginx_cert" != "-" ] && out="${out:+$out,}nginx"
    if [ "$backup_timer" != "-" ]; then
        out="${out:+$out,}backup"
        [ "$backup_prom" != "-" ] && out="${out:+$out,}metrics"
    fi
    echo "$out"
}

# firewall guest .fw enumeration
# The Proxmox host report enumerates every per-guest firewall config
# (/etc/pve/firewall/<vmid>.fw) rather than a hand-maintained VMID list that
# silently dropped new guests. This is the pure filter behind it: read candidate
# *.fw paths on stdin (from `find … -name '*.fw'`), drop the cluster-wide
# cluster.fw (dumped separately as IP sets), and emit the per-guest paths sorted.
# collect-state.sh injects it into the remote host body via `declare -f` so the
# same tested code runs on the Proxmox host.
firewall_guest_fw_list() {
    grep -v -E '(^|/)cluster\.fw$' | sort
}
