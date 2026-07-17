# nextcloud

Deploys [Nextcloud](https://nextcloud.com/) on a NAS-pinned Debian VM
(`192.168.0.156`, vmid 156) as a Docker Compose stack. Reachable at
`cloud.ericsweiss.com` (external + internal) and `cloud.esweiss.com` (internal),
SSO-only via Authentik OIDC. Full architecture + runbooks: `docs/35-nextcloud.md`.

## What it does

1. Mounts the three ZFS zvol passthrough disks (via `zvol_mount`):
   - `ssd/appdata/nextcloud/app` → `/mnt/nextcloud-app` (compose dir, html/config, backups)
   - `ssd/appdata/nextcloud/postgres` → `/mnt/nextcloud-postgres` (PGDATA)
   - `tank/nextcloud-data/disk` → `/mnt/nextcloud-data` (bulk user data, 2T sparse)
   There is **no NFS** — the VM is pinned to the NAS host, so all state rides
   zvol passthrough disks.
2. Installs Docker Engine + the compose plugin from `download.docker.com`
   (pinned; via the shared `apt_signed_repo` helper). Container logs go to
   journald so `alloy_host` ships them to Loki.
3. Runs the compose stack (`nextcloud-compose.service`): `nextcloud:*-apache`,
   `postgres`, `redis` (cache/lock), a `cron.sh` sidecar, and
   `nextcloud-exporter` (`:9205`).
4. Terminates TLS with a host-level nginx serving the distributed
   `*.esweiss.com` wildcard cert (`acme_certs`), proxying to the loopback-bound
   web container. A self-signed placeholder is generated on a fresh VM until the
   real cert is pushed.
5. Configures Authentik OIDC SSO via the `user_oidc` app (idempotent `occ`),
   SSO-only with group provisioning; sets the serverinfo token the exporter uses.
6. Installs a nightly `pg_dump` timer that emits `nextcloud_backup_*`
   node_exporter textfile metrics (backup rides the app zvol → archive + vzdump).

## Secrets (via `op run`, `task nextcloud:deploy`)

- `NEXTCLOUD_ADMIN_PASSWORD` — break-glass admin (SSO is the normal path)
- `NEXTCLOUD_POSTGRES_PASSWORD` — DB role password
- `NEXTCLOUD_SERVERINFO_TOKEN` — serverinfo app token (exporter auth)
- `NEXTCLOUD_OIDC_CLIENT_ID` / `NEXTCLOUD_OIDC_CLIENT_SECRET` — Authentik OIDC

## Notes

- `skip_nextcloud_deploy: true` skips everything needing a live Docker daemon /
  Nextcloud (apt install, compose up, `occ`) — used by the molecule scenario,
  which asserts the template + config contract.
- Version pins live in `ansible/inventories/prod/group_vars/all.yml`
  (`nextcloud_version`, `nextcloud_postgres_version`,
  `nextcloud_exporter_version`, `nextcloud_docker_version` +
  `nextcloud_containerd_version` / `nextcloud_docker_compose_version`). Redis
  reuses the shared `redis_version` pin.
