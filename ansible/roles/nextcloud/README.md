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
   (pinned + dpkg-held; via the shared `docker_engine` role, included through
   the shared `compose_app` role, which uses `apt_signed_repo` for the repo).
   Container logs go to journald so `alloy_host` ships them to Loki.
3. Runs the compose stack (`nextcloud-compose.service`, the shared `compose_app`
   compose systemd unit): `nextcloud:*-apache`, `postgres`, `redis` (cache/lock),
   a `cron.sh` sidecar, and `nextcloud-exporter` (`:9205`).
4. Terminates TLS with a host-level nginx (shared `compose_app` nginx task flow)
   serving the distributed `*.esweiss.com` wildcard cert (`acme_certs`, **TLS 1.3
   only**), proxying to the loopback-bound web container. A self-signed
   placeholder is generated on a fresh VM until the real cert is pushed.
5. Configures Authentik OIDC SSO via the `user_oidc` app (idempotent `occ`),
   SSO-only with group provisioning; sets the serverinfo token the exporter uses.
6. Installs a nightly `pg_dump` timer that emits `nextcloud_backup_*`
   node_exporter textfile metrics (the shared `compose_app` `write_prom_metrics`
   helper). Setting `nextcloud_backup_nfs_enabled` (opt-in, in group_vars)
   relocates the dump onto the offsite NFS landing zone
   (`tank/backups/apps/nextcloud`, plaintext NFSv4 over the host-internal bridge,
   like the gitlab role) so it rides the archsync file walk into B2; otherwise it
   lands on the app zvol → archive + vzdump.

## Secrets (via `op run`, `task nextcloud:deploy`)

- `NEXTCLOUD_ADMIN_PASSWORD` — break-glass admin (SSO is the normal path)
- `NEXTCLOUD_POSTGRES_PASSWORD` — DB role password
- `NEXTCLOUD_SERVERINFO_TOKEN` — serverinfo app token (exporter auth)
- `NEXTCLOUD_OIDC_CLIENT_ID` / `NEXTCLOUD_OIDC_CLIENT_SECRET` — Authentik OIDC

## Notes

- The shared `compose_app` role provides the Docker install, the compose systemd
  unit, the host-nginx task flow, and the `write_prom_metrics` backup helper;
  this role passes its nextcloud-specific vars and keeps the compose file, `.env`,
  nginx site, `occ`/SSO, and backup wrapper.
- `skip_nextcloud_deploy: true` skips everything needing a live Docker daemon /
  Nextcloud (apt install, compose up, `occ`) — used by the molecule scenario,
  which asserts the template + config contract (and runs the backup wrapper
  end-to-end against a mocked docker).
- Version pins live in `ansible/inventories/prod/group_vars/all.yml`
  (`nextcloud_version`, `nextcloud_postgres_version`,
  `nextcloud_exporter_version`). Redis reuses the shared `redis_version` pin.
  The Docker engine is installed and pinned by the shared `docker_engine` role
  from the single `docker_ce_version` / `containerd_version` /
  `docker_compose_plugin_version` pins.
