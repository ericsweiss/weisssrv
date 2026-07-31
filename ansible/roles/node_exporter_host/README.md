# Role: node_exporter_host

Installs Prometheus node_exporter on bare-metal Proxmox hosts for hardware
metrics (thermals via hwmon/thermal_zone, disk I/O, NIC counters). Listens on
**port 9101** to avoid conflicting with the k3s in-cluster node-exporter
DaemonSet on 9100. `smartmontools` is installed on the bare-metal Proxmox
hosts only (the `smartctl` binary feeds the smartmon collector below plus
out-of-band use: smartd / `nas_storage`, collect-state.sh, postflight); SATA
temperatures also surface via the drivetemp/hwmon path.

Also runs on the DNS LXCs (dns-01/dns-02), the smtp-relay LXC, and the GitLab
VM (`gitlab_servers`) for their textfile collectors (cert renewal on DNS;
backup freshness on GitLab). The Proxmox-only pieces below — smartmontools and
the collectors — are gated on `groups['proxmox']`, so those hosts install only
the node-exporter package + 9101 override + textfile collector directory.

## Liveness gate

`node-exporter-healthcheck.timer` fires
`/usr/local/sbin/node-exporter-healthcheck.sh` every
`node_exporter_host_healthcheck_interval` (default 5min). It GETs
`http://127.0.0.1:9101/metrics` twice (20s timeout, 5s apart) and, if both fail
while systemd still reports the unit active, restarts
`prometheus-node-exporter` and writes
`node_exporter_healthcheck_last_restart_timestamp_seconds` to the textfile dir.

It exists because on 2026-07-26 the exporter on pve-nas-01 went **zombie**
(`/proc/<pid>/status` `State: Z`, PPid 1) with its listening socket still bound
and nothing accepting. systemd saw a live main PID, reported
`active (running)` forever, and no `Restart=` policy could fire — the NAS went
unmonitored for hours and `NFSServerDown` (which alerts on the metric being
*absent*) paged falsely while NFS was healthy. `WatchdogSec` cannot cover this:
the Debian unit is `Type=simple` and node_exporter never `sd_notify`s, so an
HTTP probe is the only trustworthy liveness signal.

A deliberately stopped unit is left alone (`systemctl is-active` guard), so the
gate never fights an operator. Note this is the runtime counterpart to the
role's deploy-time `uri` check, which only proves the exporter was alive at the
end of the play — this role is deliberately NOT a `prometheus_exporter` wrapper
and so inherits none of that role's health handling.

## Textfile collector

Reads `/var/lib/node_exporter/*.prom` files for custom metrics. Currently
populated by:

- `nas_storage` role: media-mover and archive-backupctl write run-status metrics
- `acme_certs` role: cert renewal/distribution writes `cert_renewal_*` metrics
  (consumed by `CertRenewalFailed` PrometheusRule)
- `gitlab` role: the backup wrapper writes `gitlab_backup_*` metrics on the
  GitLab VM (consumed by `GitLabBackupFailed`/`GitLabBackupStale` PrometheusRules)
- This role's own corosync + pmxcfs health collector (see below)
- This role's own zpool-status collector (see below)
- This role's own smartmon collector (see below)

## zpool-status collector (hosts with ZFS pools)

This role also installs a per-pool ZFS health collector that runs once a
minute. It exists because pool *health* alone misses silent-corruption: a
single-vdev pool accumulating checksum errors stays `ONLINE` while
`zpool status` quietly counts errors (the failure mode that corrupted
pve-laptop-01's `local-ssd` on 2026-06-12).

Components installed wherever a `zpool` binary is present:

- `/usr/local/sbin/zpool-status-collector.sh` — oneshot script that parses
  `zpool status -v` per pool and writes a `.prom` file atomically.
- `zpool-status-collector.service` + `.timer` — oneshot unit fired every minute.

Emitted metrics (in `/var/lib/node_exporter/zfs_pool_status.prom`):

| Metric | Meaning |
|--------|---------|
| `zfs_pool_status_health_code{pool}` | `0`=ONLINE `1`=DEGRADED `2`=other/FAULTED. |
| `zfs_pool_status_errors_total{pool,type}` | Summed per-vdev READ/WRITE/CKSUM counters; non-zero while still ONLINE is the silent-corruption signature. |
| `zfs_pool_status_data_errors{pool}` | Entries in the `zpool status -v` permanent-error list. |
| `zfs_pool_status_last_scrub_seconds{pool}` | Unix time the last scrub completed (an in-progress scrub counts as fresh; `0` = never). |
| `zfs_pool_status_allocated_bytes{pool}` | Allocated bytes (`zpool list -Hp alloc`). |
| `zfs_pool_status_size_bytes{pool}` | Total pool size in bytes (`zpool list -Hp size`) — emitted only when the pool reports a real size, so a faulted pool can't feed a `0` into the capacity ratio. |
| `zfs_pool_status_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

Hosts without ZFS emit only the sentinel. Companion alerts live in the
`homelab.monitoring` group of
`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`:
`ZFSPoolDeviceErrors`, `ZFSPoolDataErrors`, `ZFSPoolNotOnline`,
`ZFSPoolScrubStale`, `ZFSPoolCollectorStale`, and the capacity pair
`ZFSPoolSpaceWarning` (>80%) / `ZFSPoolSpaceCritical` (>90%).

## smartmon collector (Proxmox hosts only)

Exports per-device SMART health to Prometheus every 5 minutes. smartd on
pve-nas-01 keeps the attribute-level **email** path, but before this
collector no SMART data reached Prometheus at all — Grafana/Alertmanager
could not alert on failing drives, and ZFS error events (e.g. the archive
pool's device errors) could not be attributed to a disk.

- `/usr/local/sbin/smartmon-collector.sh` — oneshot script; probes every
  `smartctl --scan` device with `-n standby` so it **never wakes a sleeping
  drive or aborts a long self-test** (the documented reason DEVICESCAN was
  removed from smartd.conf). Writes `smartmon.prom` atomically.
- `smartmon-collector.service` + `.timer` — oneshot unit fired every 5 min.

Emitted metrics (in `/var/lib/node_exporter/smartmon.prom`):

| Metric | Meaning |
|--------|---------|
| `smartmon_device_info{device,model,serial,interface}` | Static identity (always `1`). |
| `smartmon_device_active{device}` | `0` = drive was in standby this cycle (attribute series absent until it wakes — alert expressions should tolerate gaps). |
| `smartmon_device_smart_healthy{device}` | Overall self-assessment: `1`=PASSED/OK, `0`=failing. |
| `smartmon_temperature_celsius{device}` | SMART-reported temperature. |
| `smartmon_reallocated_sector_count{device}` | ATA attr 5 raw. |
| `smartmon_current_pending_sector_count{device}` | ATA attr 197 raw. |
| `smartmon_offline_uncorrectable_count{device}` | ATA attr 198 raw. |
| `smartmon_media_errors_count{device}` | NVMe media/data-integrity errors. |
| `smartmon_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

Companion alerts live in the `homelab.storage` group of
`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`:
`SMARTDeviceUnhealthy`, `SMARTReallocatedSectorsGrowing`, `SMARTPendingSectors`,
`SMARTOfflineUncorrectable`, `SMARTMediaErrors`, `SMARTCollectorStale`.

## Corosync + pmxcfs health collector (Proxmox hosts only)

This role also installs a Proxmox-specific health collector that samples
corosync CPU usage and pmxcfs liveness once a minute, writing the result
to the textfile collector dir.

Components installed on every Proxmox host:

- `/usr/local/sbin/corosync-health-collector.sh` — oneshot script that
  reads `top -bn2` for corosync CPU%, stats
  `/etc/pve/ha/manager_status` for its mtime, and writes a `.prom` file
  atomically.
- `corosync-health-collector.service` — systemd unit (oneshot, `User=root`,
  `After=corosync.service`) that runs the script.
- `corosync-health-collector.timer` — fires the service every minute.

Emitted metrics (in `/var/lib/node_exporter/corosync_health.prom`):

| Metric | Meaning |
|--------|---------|
| `proxmox_corosync_cpu_percent` | CPU% of the corosync process from `top -bn2`'s second sample. Sustained values near 100% indicate a wedged corosync. |
| `proxmox_pmxcfs_manager_status_mtime_seconds` | Unix mtime of `/etc/pve/ha/manager_status` as this node sees it. Compare to `time()` to detect a pmxcfs split-brain (stale local view). `0` if the file does not exist (HA disabled). |
| `proxmox_corosync_health_collector_last_success_seconds` | Unix time the collector itself last completed. Staleness here is a meta-failure — the underlying collector is broken, not corosync/pmxcfs. |

These metrics drive three alerts in
`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`:

- `CorosyncWedged` — `proxmox_corosync_cpu_percent` pinned high for a
  sustained period (catches the failure mode where corosync is alive
  enough for `ProxmoxHostDown` to stay green but is no longer
  processing membership traffic).
- `PmxcfsStale` — `time() - proxmox_pmxcfs_manager_status_mtime_seconds`
  exceeds the staleness budget, matching the `2026-05-09` pve-nas-01
  pmxcfs split-brain pattern.
- `CorosyncHealthCollectorStale` — the collector itself hasn't
  succeeded in over five minutes; the other two alerts on this host
  are now serving stale data.

## Deployment

```bash
ansible-playbook ansible/playbooks/site.yml --tags node_exporter_host
```

## Configuration

Defaults (`defaults/main.yml`):

```yaml
node_exporter_host_port: 9101            # 9101 to avoid k3s DaemonSet on 9100
node_exporter_host_textfile_dir: /var/lib/node_exporter
```

The `prometheus-node-exporter` package is installed with `state: present`
(unpinned) and `update_cache: true` (with `cache_valid_time: 3600` to skip a
redundant apt refresh when the cache is under an hour old), so it tracks
whatever the Debian repo currently ships — there is no `node_exporter_version`
pin in all.yml.

## See also

- `docs/31-observability.md`
- `kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`
  — corresponding ServiceMonitor + per-host Endpoints
