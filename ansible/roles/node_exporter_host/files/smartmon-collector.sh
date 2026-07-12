#!/bin/sh
# Managed by Ansible node_exporter_host role.
#
# Writes per-device SMART health metrics to the node_exporter textfile
# collector so drive degradation is visible in Prometheus/Grafana and a
# ZFS error event can be attributed to a specific disk. Complements —
# does not replace — smartd on pve-nas-01 (attribute-level email
# alerting stays the out-of-band path).
#
# Standby safety: every probe uses `smartctl -n standby` so a sleeping
# drive is never spun up and a long self-test is never aborted (the
# documented reason DEVICESCAN was removed from smartd.conf). A drive
# found in standby emits only smartmon_device_active=0 for that cycle;
# its attribute series are absent until it wakes, so alerts should use
# max_over_time()/absent-tolerant expressions.
#
# Metrics emitted per device (ATA/SCSI and NVMe where applicable):
#   smartmon_device_info{device,model,serial,interface} 1
#   smartmon_device_active{device}            1=probed 0=in standby
#   smartmon_device_smart_healthy{device}     1=PASSED/OK 0=failing
#   smartmon_temperature_celsius{device}
#   smartmon_reallocated_sector_count{device}      ATA attr 5 raw
#   smartmon_current_pending_sector_count{device}  ATA attr 197 raw
#   smartmon_offline_uncorrectable_count{device}   ATA attr 198 raw
#   smartmon_media_errors_count{device}            NVMe media errors
# Plus the collector sentinel:
#   smartmon_collector_last_success_seconds
#
# Hosts without smartctl or without SMART-capable devices emit only the
# sentinel, mirroring the zpool collector's no-ZFS behavior.
#
# Atomicity: writes to .tmp then renames so node_exporter never reads a
# half-written file.

set -eu
export LC_ALL=C

OUT_DIR="${1:-/var/lib/node_exporter}"
OUT="$OUT_DIR/smartmon.prom"
TMP="$OUT.tmp"
# The role creates the dir, but an OUT_DIR override pointing elsewhere
# would otherwise kill the script before the sentinel is written.
mkdir -p "$OUT_DIR"

# Strip characters that would break a Prometheus label value.
sanitize() {
    printf '%s' "$1" | tr -d '\\"' | tr -s ' '
}

{
    printf '# HELP smartmon_device_info Static SMART device identity (value is always 1).\n'
    printf '# TYPE smartmon_device_info gauge\n'
    printf '# HELP smartmon_device_active Whether the device answered the SMART probe (0 = in standby; probes never wake drives).\n'
    printf '# TYPE smartmon_device_active gauge\n'
    printf '# HELP smartmon_device_smart_healthy SMART overall-health self-assessment (1=PASSED/OK, 0=failing).\n'
    printf '# TYPE smartmon_device_smart_healthy gauge\n'
    printf '# HELP smartmon_temperature_celsius Device temperature reported by SMART.\n'
    printf '# TYPE smartmon_temperature_celsius gauge\n'
    printf '# HELP smartmon_reallocated_sector_count Raw value of ATA attribute 5 (Reallocated_Sector_Ct).\n'
    printf '# TYPE smartmon_reallocated_sector_count gauge\n'
    printf '# HELP smartmon_current_pending_sector_count Raw value of ATA attribute 197 (Current_Pending_Sector).\n'
    printf '# TYPE smartmon_current_pending_sector_count gauge\n'
    printf '# HELP smartmon_offline_uncorrectable_count Raw value of ATA attribute 198 (Offline_Uncorrectable).\n'
    printf '# TYPE smartmon_offline_uncorrectable_count gauge\n'
    printf '# HELP smartmon_media_errors_count NVMe Media and Data Integrity Errors.\n'
    printf '# TYPE smartmon_media_errors_count gauge\n'

    if command -v smartctl >/dev/null 2>&1; then
        # --scan-open (not --scan): types SATA drives behind libata as
        # `-d sat` (ATA-dialect output the parsers below expect, and re-arms
        # the ATA-only `-n standby` no-wake guarantee) and omits unopenable
        # loop/USB-bridge devices.
        smartctl --scan-open 2>/dev/null | while read -r dev _dash dtype _rest; do
            case "$dev" in /dev/*) ;; *) continue ;; esac
            [ -n "$dtype" ] || dtype=auto

            # `set +e` per device: an unreadable/vanished device must not
            # kill the whole collector run. Standby drives return rc=2
            # with a "STANDBY mode" notice.
            set +e
            out=$(smartctl -n standby -i -H -A -d "$dtype" "$dev" 2>/dev/null)
            rc=$?
            set -e

            if [ $rc -ne 0 ] && printf '%s\n' "$out" | grep -q 'STANDBY mode'; then
                printf 'smartmon_device_active{device="%s"} 0\n' "$dev"
                continue
            fi
            # No SMART support / open failure: skip entirely (loop or USB
            # bridge devices in odd environments). Dialect-tolerant so genuine
            # SAS/SCSI drives (which print "Product:") aren't skipped either.
            printf '%s\n' "$out" | grep -Eqi 'serial number|model|product' || continue

            model=$(printf '%s\n' "$out" \
                | sed -n -e 's/^Device Model: *//p' -e 's/^Model Number: *//p' -e 's/^Product: *//p' \
                | head -1)
            serial=$(printf '%s\n' "$out" | sed -n 's/^Serial [Nn]umber: *//p' | head -1)
            printf 'smartmon_device_info{device="%s",model="%s",serial="%s",interface="%s"} 1\n' \
                "$dev" "$(sanitize "${model:-unknown}")" "$(sanitize "${serial:-unknown}")" "$dtype"
            printf 'smartmon_device_active{device="%s"} 1\n' "$dev"

            # Overall health: ATA/NVMe print "...test result: PASSED",
            # SCSI prints "SMART Health Status: OK". Omit the metric when
            # neither line is present (health query unsupported).
            if printf '%s\n' "$out" | grep -q 'self-assessment test result\|SMART Health Status'; then
                if printf '%s\n' "$out" | grep -q 'test result: PASSED\|SMART Health Status: OK'; then
                    healthy=1
                else
                    healthy=0
                fi
                printf 'smartmon_device_smart_healthy{device="%s"} %d\n' "$dev" "$healthy"
            fi

            # ATA attribute table raw values (first integer of RAW_VALUE).
            attr_raw() {
                printf '%s\n' "$out" | awk -v id="$1" \
                    '$1 == id && NF >= 10 { v=$10; sub(/[^0-9].*$/, "", v); if (v != "") print v; exit }'
            }
            # if/fi rather than `[ ] && printf`: a false test as the last
            # command of an iteration would propagate a non-zero status
            # out of the while pipeline and kill the run under set -e.
            realloc=$(attr_raw 5)
            pending=$(attr_raw 197)
            uncorr=$(attr_raw 198)
            if [ -n "$realloc" ]; then
                printf 'smartmon_reallocated_sector_count{device="%s"} %s\n' "$dev" "$realloc"
            fi
            if [ -n "$pending" ]; then
                printf 'smartmon_current_pending_sector_count{device="%s"} %s\n' "$dev" "$pending"
            fi
            if [ -n "$uncorr" ]; then
                printf 'smartmon_offline_uncorrectable_count{device="%s"} %s\n' "$dev" "$uncorr"
            fi

            # Temperature: ATA attr 194 raw first, then the NVMe/SCSI
            # "Temperature:  NN Celsius" form.
            temp=$(attr_raw 194)
            if [ -z "$temp" ]; then
                temp=$(printf '%s\n' "$out" \
                    | sed -n -e 's/^Temperature: *\([0-9][0-9]*\) *Cel.*/\1/p' \
                          -e 's/^Current Drive Temperature: *\([0-9][0-9]*\) *Cel.*/\1/p' \
                    | head -1)
            fi
            if [ -n "$temp" ]; then
                printf 'smartmon_temperature_celsius{device="%s"} %s\n' "$dev" "$temp"
            fi

            # NVMe media errors.
            media_err=$(printf '%s\n' "$out" | sed -n 's/^Media and Data Integrity Errors: *\([0-9,]*\).*/\1/p' | head -1 | tr -d ,)
            if [ -n "$media_err" ]; then
                printf 'smartmon_media_errors_count{device="%s"} %s\n' "$dev" "$media_err"
            fi
        done
    fi

    printf '# HELP smartmon_collector_last_success_seconds Unix time the SMART textfile collector last completed. Staleness means the collector itself is broken — treat as a meta-failure.\n'
    printf '# TYPE smartmon_collector_last_success_seconds gauge\n'
    printf 'smartmon_collector_last_success_seconds %s\n' "$(date +%s)"
} > "$TMP"

mv "$TMP" "$OUT"
