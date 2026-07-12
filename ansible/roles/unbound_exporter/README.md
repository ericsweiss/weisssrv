# Role: unbound_exporter

Installs the [letsencrypt/unbound_exporter](https://github.com/letsencrypt/unbound_exporter)
on dns-01 / dns-02 to expose Unbound stats (cache hit rate, query counts,
DNSSEC validations) to Prometheus.

Talks to Unbound via `unbound-control` over its local Unix control socket
(`/run/unbound.ctl`, `control-use-cert: no`). The `unbound` role provisions
the socket; the exporter unit runs as a DynamicUser with
`SupplementaryGroups=unbound` for socket access.

## Deployment

The role is included by `ansible/playbooks/dns.yml`, which has no `tags:`
declarations — scope by host instead:

```bash
task dns:deploy -- --limit dns-01
# or the full DNS group
task dns:deploy
```

## Configuration

```yaml
unbound_exporter_port: 9167
```

Version pinning lives in `ansible/inventories/prod/group_vars/all.yml`
(`unbound_exporter_version`).

## See also

- `docs/31-observability.md`
- `kubernetes/infrastructure/observability/exporters/unbound-exporter.yaml`
  — ServiceMonitor + per-host Endpoints
- `ansible/roles/unbound/` — the resolver itself
