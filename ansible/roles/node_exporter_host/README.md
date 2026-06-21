# Role: node_exporter_host

Installs Prometheus node_exporter on bare-metal Proxmox hosts for hardware
metrics (thermals via hwmon/thermal_zone, disk I/O, NIC counters). Listens on
**port 9101** to avoid conflicting with the k3s in-cluster node-exporter
DaemonSet on 9100. SMART health is **not** exported here — `smartmontools` is
installed only for the `smartctl` binary used out-of-band (smartd /
`nas_storage`); SATA temperatures surface via the drivetemp/hwmon path.

Also runs on the DNS LXCs (dns-01/dns-02) and the GitLab VM (`gitlab_servers`)
for their textfile collectors (cert renewal on DNS; backup freshness on GitLab).
The Proxmox-only collectors below are gated on `groups['proxmox']`, so those
hosts install only the package + 9101 override + textfile collector directory.

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
| `zfs_pool_status_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

Hosts without ZFS emit only the sentinel. Companion alerts live in the
`homelab.storage` group of
`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`:
`ZFSPoolDeviceErrors`, `ZFSPoolNotOnline`, `ZFSPoolScrubStale`,
`ZFSPoolCollectorStale`.

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

Version pinning lives in `ansible/inventories/prod/group_vars/all.yml`
(`node_exporter_version`); this role consumes it via the playbook vars.

## See also

- `docs/31-observability.md`
- `kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`
  — corresponding ServiceMonitor + per-host Endpoints
