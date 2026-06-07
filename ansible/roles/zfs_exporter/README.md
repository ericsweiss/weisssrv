# Role: zfs_exporter

Installs the [pdf/zfs_exporter](https://github.com/pdf/zfs_exporter) on
pve-nas-01 to expose ZFS pool/dataset metrics (health, usage, scrub status,
fragmentation) to Prometheus.

Runs as root because ZFS introspection requires root on Linux.

## Deployment

The role is included by `ansible/playbooks/storage.yml`, which has no
`tags:` declarations — the storage play only targets pve-nas-01:

```bash
task storage:deploy
```

## Configuration

```yaml
zfs_exporter_port: 9134
```

Version pinning lives in `ansible/inventories/prod/group_vars/all.yml`
(`zfs_exporter_version`). The exporter discovers ZFS pools automatically;
there is no pool allowlist to configure.

## Alerts driven from this exporter

`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`:

- `ZFSPoolDegraded` — `zfs_pool_state` non-zero (critical)
- `ZFSPoolSpaceWarning/Critical` — pool usage > 80% / 90%

## See also

- `docs/06-zfs.md` — ZFS pool layout
- `docs/31-observability.md` — observability stack overview
