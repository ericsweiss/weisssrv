# Authentik

SSO/OIDC identity provider for the homelab, running in the `authentik`
namespace. Every user-facing app sits behind it.

> **Ground rule**: applications, providers, groups and policy bindings are
> **Terraform-managed** in `terraform/authentik` and applied under supervision
> ([docs/40](../../../docs/40-authentik-terraform.md)). A change made in the
> Authentik UI is drift and gets reverted on the next apply. This README covers
> the Kubernetes deployment; docs/40 owns the object layer.

## Overview

- **Hostnames**: `auth.ericsweiss.com` (external) / `auth.esweiss.com`
  (internal). Admin UI: `https://auth.ericsweiss.com/if/admin/`.
- **OIDC issuer is always the external host** (`auth.ericsweiss.com`), even for
  internal-only apps, so a token issued on one hostname validates on both.
- **The origin is pinned to the external host.** `release.yaml` sets
  `AUTHENTIK_WEB__BASE_URL` to `https://auth.ericsweiss.com` on both the server
  and the worker. Every absolute URL Authentik builds carries that origin, so
  the internal name serves the UI but redirects, e-mail links and OIDC
  endpoints all come back on the external one. Use the external hostname for
  first login and when debugging a redirect; changing `BASE_URL` to the
  internal name would move the OIDC issuer and break every registered app.
- **Chart**: [goauthentik/authentik](https://github.com/goauthentik/helm),
  pinned by `authentik_version` in the cluster-versions ConfigMap.

## Architecture

```
+----------+     +----------+     +-------------------+     +-----------------+
|  Traefik |---->| Authentik|---->|  Authentik Server |---->|   PostgreSQL    |
| Ingress  |     | Outpost  |     |  (Web UI + API)   |     |  (Helm subchart)|
+----------+     +----------+     +-------------------+     +-----------------+
                                             |                       ^
                                             |                       |
                                  +----------v----------+            |
                                  |  Authentik Worker   |------------+
                                  |  (Background tasks) |
                                  +---------------------+
```

Redis is not required as of Authentik 2025.10 - all state is in PostgreSQL.

| Component | Purpose | Replicas | Storage |
|---|---|---|---|
| Authentik server | Web UI, API, embedded outpost | 2-4 (HPA, anti-affinity) | stateless |
| Authentik worker | Background tasks (email, sync) | 1 | stateless |
| Bundled PostgreSQL | Primary database, all state | 1 | hostPath PV on `k3s-agt-nas-01` -> ZFS zvol `ssd/appdata/authentik/postgres` (10 GB, ext4) |

The bundled PostgreSQL is the SSO single point of failure: while
`k3s-agt-nas-01` is unreachable, every app behind Authentik is locked out.
Server and worker are stateless and reschedule freely. HA options
(CloudNativePG, external Postgres on a NAS LXC) are tracked in docs/16.

## Manifests

- `release.yaml` - HelmRelease, values inlined, image tags substituted from
  `cluster-versions`.
- `storage.yaml` - the postgres hostPath PV/PVC and the pg-dump NFS PV/PVC.
- `externalsecret.yaml` - `authentik-secrets` from the 1Password items below.
- `ingress-route.yaml`, `certificate.yaml`, `middleware.yaml` - ingress and the
  two forward-auth middlewares.
- `pg-dump.yaml` - nightly database dump CronJob.
- `networkpolicy.yaml`, `vpa.yaml`, `namespace.yaml`.

`middleware.yaml` ships the pair other apps consume: `authentik-auth` for
ordinary forward auth, and `authentik-auth-basic` for the few upstreams that
want authentik-injected basic credentials. Read that file's header before
attaching either - the wrong one strips client credentials.

## 1Password items

This app reads the **Authentik Secrets** and **SMTP Relay Auth** items from the
Homelab vault. The exact field list and the rotation procedure are in
[docs/15-credential-rotation.md](../../../docs/15-credential-rotation.md)
§ Required 1Password Items (canonical). `externalsecret.yaml` is the second
source of truth: every `remoteRef` in it must exist in the item, or the whole
Secret fails to sync.

The database was bootstrapped before the chart managed it, with `authentik` as
its only superuser. The `postgres` role the exporter sidecar connects as is
therefore created by hand — `CREATE ROLE postgres SUPERUSER LOGIN PASSWORD …`,
run as `authentik`, using the `postgresql-admin-password` the ExternalSecret
declares. Rotating that field in 1Password needs a matching `ALTER ROLE postgres
PASSWORD …` in the same pass, or the exporter stops reporting `pg_up 1`.

## DNS

Both sides are codified; never edit AdGuard by hand. `auth.esweiss.com` and
`auth.ericsweiss.com` both resolve internally to the internal Traefik VIP
(`ansible/inventories/prod/group_vars/dns.yml` carries the entries and the
reason the external name is rewritten inside). The public record is created by
external-dns from `ingress-route.yaml`. See
[docs/08-dns.md](../../../docs/08-dns.md).

## Backup and Restore

`pg-dump.yaml` runs the `authentik-pg-dump` CronJob nightly at 02:30 local time.
It writes a gzipped `--clean --if-exists` dump to the NFS export
`/backups-apps/authentik` (`tank/backups/apps/authentik`), keeps the newest 7,
and that landing zone rides both the nightly archive replication and the restic
B2 offsite walk ([docs/42](../../../docs/42-offsite-backup.md)). Staleness is
alerted by `AuthentikBackupStale`.

```bash
# Ad-hoc dump outside the schedule
kubectl exec -n authentik authentik-postgresql-0 -- \
  pg_dump -U authentik authentik > authentik-backup-$(date +%Y%m%d).sql

# Restore
kubectl exec -i -n authentik authentik-postgresql-0 -- \
  psql -U authentik authentik < authentik-backup-YYYYMMDD.sql
```

## Security notes

- The `akadmin` break-glass password lives in 1Password (`Authentik Secrets`);
  nothing in-cluster reads it. Never log or echo `secret_key`.
- MFA on the admin authentication flow is configured in the Authentik UI
  (Stages -> MFA validation). Flows and stages are outside the Terraform module
  (docs/40) and are not restored by a rebuild.
- `networkpolicy.yaml` default-denies ingress and scopes egress to the
  destinations Authentik actually needs.

## Links

- [docs/40](../../../docs/40-authentik-terraform.md) - Terraform object layer,
  adding an application, supervised apply.
- [docs/15](../../../docs/15-credential-rotation.md) - 1Password items and
  rotation.
- [docs/29](../../../docs/29-flux-operations.md) - Flux day-2: shipping a
  change, forcing a reconcile, rollback, secret rotation.
- [docs/12](../../../docs/12-runbooks.md) - version-bump workflow.
- [Authentik docs](https://docs.goauthentik.io/) ·
  [Helm chart](https://github.com/goauthentik/helm) ·
  [Traefik integration](https://docs.goauthentik.io/integrations/services/traefik/)

## Disable

Drop `- authentik` from `kubernetes/apps/kustomization.yaml`; Flux prunes the
namespace. Then un-wire every consumer, or they break: Grafana's OIDC block in
`kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml`,
every `authentik-auth` middleware reference in the apps' IngressRoutes, and the
Terraform object layer in `terraform/authentik`. The PVs are `Retain`, so the
Postgres zvol and the dump dataset survive.
