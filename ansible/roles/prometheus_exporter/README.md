# Role: prometheus_exporter

Shared install pipeline for download-based Prometheus exporters. It owns the
boilerplate that drifts across exporter roles: probe the installed version,
conditionally download the release artifact, install it, then enable + start
the service and health-check it.

Two artifact types are supported:

- `tarball` — `get_url` the `.tar.gz`, `unarchive` it, copy the binary out.
- `deb` — `get_url` the `.deb`, install it with `apt: deb:`.

Backs **`zfs_exporter`** (tarball) and **`unbound_exporter`** (deb). Both are
thin wrappers that pass their specifics as `vars` and keep their own
`templates/<name>.service.j2` unit file.

## Why `node_exporter_host` is NOT built on this role

`node_exporter_host` does not fit this abstraction and is intentionally left
standalone:

- It installs from the **Debian apt repo** (`prometheus-node-exporter`), not a
  release download — none of the download/checksum/extract/version-probe
  pipeline applies.
- It writes a systemd **drop-in override** (pinning `:9101` and the collector
  flag set), not a full unit file.
- Most of its tasks are bespoke (drivetemp module, two textfile collectors and
  their timers, proxmox-group gating) and have nothing to do with the other
  exporters.

Folding it in would mean an `install_method == 'apt_repo'` branch only one
caller ever takes plus a passthrough for arbitrary post-install tasks — a worse
abstraction than an honest standalone role.

## How wrappers invoke it

The wrapper does not include the whole role. It runs the two halves around its
own unit template so a unit change is deployed before the service is restarted:

1. `include_role: { name: prometheus_exporter, tasks_from: install }` with the
   param block in `vars:` — probe + conditional download + install.
2. `template:` the wrapper's own `<name>.service.j2`, notifying the shared
   `Restart prometheus exporter` handler.
3. `include_role: { name: prometheus_exporter, tasks_from: service }` with the
   same `vars:` — enable + start + `flush_handlers` + health check.

`tasks/main.yml` chains install + service for standalone runs (the molecule
scenario), and the shared handler restarts `prometheus_exporter_service_name`.

## Parameters

| Variable | Meaning | zfs_exporter | unbound_exporter |
|---|---|---|---|
| `prometheus_exporter_name` | Title-only logical name | `zfs_exporter` | `unbound_exporter` |
| `prometheus_exporter_version` | Pinned upstream version | `{{ zfs_exporter_version }}` | `{{ unbound_exporter_version }}` |
| `prometheus_exporter_artifact_type` | `tarball` or `deb` | `tarball` | `deb` |
| `prometheus_exporter_download_url` | Release artifact URL | GitHub tarball | GitHub `.deb` |
| `prometheus_exporter_checksum` | `get_url` checksum; empty = none | `{{ zfs_exporter_checksum }}` | `""` |
| `prometheus_exporter_binary_path` | Installed binary (tarball) | `/usr/local/bin/zfs_exporter` | `/usr/bin/unbound_exporter` (deb-managed) |
| `prometheus_exporter_archive_member` | Binary path inside the tarball | `zfs_exporter-<ver>.linux-amd64/zfs_exporter` | _(unused)_ |
| `prometheus_exporter_version_check_cmd` | Shell printing installed version | `--version` + awk | `dpkg-query` + sed |
| `prometheus_exporter_service_name` | systemd unit (no `.service`) | `zfs-exporter` | `unbound-exporter` |
| `prometheus_exporter_port` | Health-check port | `{{ zfs_exporter_port }}` | `{{ unbound_exporter_port }}` |
| `prometheus_exporter_tmp_dir` | Scratch dir for download/extract | `/tmp` | `/tmp` |

The version-check command is the source of truth for idempotence: empty stdout
or a non-zero rc means "(re)install"; a stdout matching
`prometheus_exporter_version` (after `trim`) means "skip".

## See also

- `docs/31-observability.md` — observability stack overview
- `ansible/roles/zfs_exporter/`, `ansible/roles/unbound_exporter/` — the wrappers
