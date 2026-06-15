#!/bin/sh
# Managed by Ansible node_exporter_host role.
#
# Writes per-pool ZFS health metrics to the node_exporter textfile
# collector. Exists because pool *health* alone misses the failure mode
# that corrupted pve-laptop-01's local-ssd (2026-06-12): a single-vdev
# pool accumulating checksum errors stays ONLINE — `zpool status` health
# never leaves ONLINE, zfs_exporter's health gauge stays green, SMART
# stays PASSED, and the first loud symptom is vzdump aborting with EIO.
# A May 10 scrub had already counted 4 errors; nothing surfaced it.
#
# Metrics emitted per pool:
#   zfs_pool_status_health_code{pool}    0=ONLINE 1=DEGRADED 2=other/FAULTED
#   zfs_pool_status_errors_total{pool,type=read|write|cksum}  summed vdev counters
#   zfs_pool_status_data_errors{pool}    count of entries in the permanent-error list
#   zfs_pool_status_last_scrub_seconds{pool}  unix time scrub last completed (0 = never)
# Plus the collector sentinel:
#   zfs_pool_status_collector_last_success_seconds
#
# Companion alerts live in kubernetes/.../kube-prometheus-stack/release.yaml
# (homelab.storage group): ZFSPoolDeviceErrors, ZFSPoolNotOnline,
# ZFSPoolScrubStale, ZFSPoolCollectorStale.
#
# Hosts without ZFS (no zpool binary or no imported pools) emit only the
# sentinel — the per-pool alerts key off pool-labelled series, so absent
# pools alert nothing, while the sentinel still proves the collector runs.
#
# Atomicity: writes to .tmp then renames so node_exporter never reads a
# half-written file. Parsing failures of one pool's counters degrade to 0
# for that counter rather than poisoning the whole file (a malformed line
# would take down every metric in the file, including the sentinel).

set -eu
export LC_ALL=C

OUT_DIR="${1:-/var/lib/node_exporter}"
OUT="$OUT_DIR/zfs_pool_status.prom"
TMP="$OUT.tmp"
# The role creates the dir, but an OUT_DIR override pointing elsewhere
# would otherwise kill the script before the sentinel is written.
mkdir -p "$OUT_DIR"

{
    printf '# HELP zfs_pool_status_health_code Pool health from zpool status: 0=ONLINE 1=DEGRADED 2=other (FAULTED/UNAVAIL/SUSPENDED/...).\n'
    printf '# TYPE zfs_pool_status_health_code gauge\n'
    printf '# HELP zfs_pool_status_errors_total Sum of per-vdev READ/WRITE/CKSUM error counters from zpool status. Non-zero with the pool still ONLINE is the silent-corruption signature.\n'
    printf '# TYPE zfs_pool_status_errors_total gauge\n'
    printf '# HELP zfs_pool_status_data_errors Number of entries in the zpool status -v permanent-error list.\n'
    printf '# TYPE zfs_pool_status_data_errors gauge\n'
    printf '# HELP zfs_pool_status_last_scrub_seconds Unix time the last scrub completed (0 = no completed scrub recorded).\n'
    printf '# TYPE zfs_pool_status_last_scrub_seconds gauge\n'

    if command -v zpool >/dev/null 2>&1; then
        for pool in $(zpool list -H -o name 2>/dev/null); do
            # `set +e` per pool: a pool that disappears mid-loop (export,
            # device yank) must not kill the whole collector run.
            set +e
            status=$(zpool status -v "$pool" 2>/dev/null)
            rc=$?
            set -e
            [ $rc -ne 0 ] && continue

            health=$(printf '%s\n' "$status" | awk -F': *' '/^ *state:/{print $2; exit}')
            case "$health" in
                ONLINE)   code=0 ;;
                DEGRADED) code=1 ;;
                *)        code=2 ;;
            esac

            # Sum vdev error columns. Vdev table rows are indented lines
            # whose last three fields are READ WRITE CKSUM; the header row
            # and the pool-name row are filtered by requiring numeric
            # error fields. Counters can be suffixed (e.g. 1.2K) on huge
            # counts — strip the suffix and keep the integer part; alerts
            # only care about zero vs non-zero.
            # Positional assignment instead of eval: the awk output is
            # already constrained to three integers, but eval on generated
            # text is a habit worth not having. $1 (OUT_DIR) was consumed
            # at the top, so clobbering the positional params is safe.
            totals=$(printf '%s\n' "$status" | awk '
                /^config:/ { in_cfg=1; next }
                in_cfg && /^errors:/ { in_cfg=0 }
                in_cfg && /^[[:space:]]+/ && NF >= 5 {
                    r=$(NF-2); w=$(NF-1); c=$NF
                    if (r ~ /^[0-9]/ && w ~ /^[0-9]/ && c ~ /^[0-9]/) {
                        sub(/[^0-9].*$/, "", r); sub(/[^0-9].*$/, "", w); sub(/[^0-9].*$/, "", c)
                        rs += r; ws += w; cs += c
                    }
                }
                END { printf "%d %d %d\n", rs+0, ws+0, cs+0 }')
            # Intentional word-splitting of three integers:
            # shellcheck disable=SC2086
            set -- $totals
            read_e=${1:-0}; write_e=${2:-0}; cksum_e=${3:-0}

            # Permanent-error list length: lines between the "errors:" marker
            # and EOF that look like dataset:<object> entries.
            # Blank lines are neutral: zpool separates the marker from
            # the entries with one, so a bare not-indented test would
            # close the section before counting anything.
            data_errors=$(printf '%s\n' "$status" | awk '
                /^errors: Permanent errors/ {f=1; next}
                f && /^[[:space:]]*$/ {next}
                f && /^[[:space:]]+[^[:space:]]/ {c++; next}
                f {f=0}
                END {print c+0}')

            # Last-scan completion time. zpool reports a finished scrub as
            # "scrub repaired ... on <date>" and a finished resilver as
            # "resilvered ... on <date>"; match both so a completed resilver
            # also counts as a fresh scan (otherwise the post-resilver /
            # pre-next-scrub window would falsely trip ZFSPoolScrubStale).
            # Convert via date -d (GNU date on all Proxmox/Debian hosts).
            # A scan in progress shows no "... on <date>" line, which would
            # leave scrub_ts at 0 and falsely trip the alert mid-scan on a long
            # raidz2 pool — treat an in-progress scrub/resilver as fresh (now)
            # so the alert only fires when no scan has run for the alert window.
            scrub_ts=0
            scrub_date=$(printf '%s\n' "$status" | sed -n 's/.*\(scrub repaired\|resilvered\).*on \(.*\)$/\2/p' | head -1)
            if [ -n "$scrub_date" ]; then
                set +e
                scrub_ts=$(date -d "$scrub_date" +%s 2>/dev/null)
                set -e
                case "$scrub_ts" in ''|*[!0-9]*) scrub_ts=0 ;; esac
            elif printf '%s\n' "$status" | grep -Eq 'scrub in progress|resilver in progress'; then
                scrub_ts=$(date +%s)
            fi

            printf 'zfs_pool_status_health_code{pool="%s"} %d\n' "$pool" "$code"
            printf 'zfs_pool_status_errors_total{pool="%s",type="read"} %d\n' "$pool" "$read_e"
            printf 'zfs_pool_status_errors_total{pool="%s",type="write"} %d\n' "$pool" "$write_e"
            printf 'zfs_pool_status_errors_total{pool="%s",type="cksum"} %d\n' "$pool" "$cksum_e"
            printf 'zfs_pool_status_data_errors{pool="%s"} %d\n' "$pool" "$data_errors"
            printf 'zfs_pool_status_last_scrub_seconds{pool="%s"} %d\n' "$pool" "$scrub_ts"
        done
    fi

    printf '# HELP zfs_pool_status_collector_last_success_seconds Unix time the zpool textfile collector last completed. Staleness means the collector itself is broken — treat as a meta-failure.\n'
    printf '# TYPE zfs_pool_status_collector_last_success_seconds gauge\n'
    printf 'zfs_pool_status_collector_last_success_seconds %s\n' "$(date +%s)"
} > "$TMP"

mv "$TMP" "$OUT"
