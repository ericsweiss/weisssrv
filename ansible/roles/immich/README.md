# immich

Deploys the [Immich](https://immich.app) photo-management stack as a Docker
Compose project on a dedicated NAS-pinned Debian VM (`immich`, 192.168.0.157,
vmid 157 on pve-nas-01), fronted by a host nginx that terminates the distributed
`*.esweiss.com` wildcard cert. Mirrors the GitLab VM pattern: `proxmox_vm`
provisions the guest and its zvols, this role installs and configures the stack.

Full architecture, storage, backup, SSO, and runbooks: **docs/36-immich.md**.

## What it does

1. **Persistent storage** — mounts the three passthrough zvols via `zvol_mount`
   (app / postgres / photo-library — see `hosts.yml`).
2. **Docker Engine** (`tasks/docker.yml`) — installs docker-ce + the compose
   plugin from the official apt repo (signing key + repo via the shared
   `apt_signed_repo` helper), pinned in `group_vars/all.yml` and `apt-mark hold`.
   `journald` log driver so container logs ride `alloy_host` → Loki.
3. **Compose stack** (`templates/docker-compose.yml.j2`) — `immich-server`,
   `immich-machine-learning` (the **CPU failover** — the primary ML endpoint is
   the GPU OpenVINO LXC `immich-ml`, see the `immich_ml` role), `database`
   (Immich's release-pinned Postgres image with the vector extensions), `redis`
   (Valkey). App on loopback `:2283`, native Prometheus metrics published on
   `:8081`/`:8082`. Lifecycle owned by `immich-compose.service`.
4. **SSO / system config** (`templates/immich-config.json.j2`) — the
   `IMMICH_CONFIG_FILE` declaring Authentik OIDC (SSO-only: password login off,
   auto-launch on), `machineLearning.urls` (`immich_ml_urls`: the GPU LXC
   first, the in-VM CPU container as sequential failover) plus
   `backup.database.enabled: false`. The one-time admin bootstrap is
   `immich_bootstrap_mode` (see below).
5. **Host nginx** (`templates/immich.nginx.conf.j2`) — terminates 443 with the
   `acme_certs`-distributed wildcard cert, `client_max_body_size 0`, websockets,
   long upload timeouts, reverse-proxies to `127.0.0.1:2283`. Seeds a self-signed
   placeholder so a fresh VM starts before the first cert push.
6. **Backups** (`templates/immich-backup-run.sh.j2` + timer) — nightly
   `pg_dumpall` onto the app zvol (rides `ssd/appdata` → archive raw-encrypted)
   with node_exporter textfile metrics (`immich_backup_*`).

## SSO bootstrap (one-time)

The first Immich account (admin/owner) must be created through the password
Admin-Registration page — OIDC auto-register only ever creates regular users.
For the very first deploy set `immich_bootstrap_mode: true` (password login on,
OIDC auto-launch off), register the admin at the direct-to-VM host
`https://immich.esweiss.com` (LAN/Tailscale only) **before** the public
`photos.ericsweiss.com` record exists — Immich makes the first registered account
the owner, so it must be claimed while the instance is LAN-only. Use the **same
email as your Authentik account** (password from 1Password "Immich
Secrets"/admin-bootstrap-password), then flip back to `false` and redeploy for
SSO-only. OIDC then links to the admin user by email. Full steps in docs/36.

## Secrets (via `op run`, injected by `task immich:deploy`)

| Env var | 1Password |
|---|---|
| `IMMICH_DB_PASSWORD` | `Immich Secrets`/postgres-password |
| `IMMICH_OAUTH_CLIENT_ID` | `Immich SSO`/client-id |
| `IMMICH_OAUTH_CLIENT_SECRET` | `Immich SSO`/client-secret |

## Molecule

`molecule/default` is a render/contract scenario: `immich_skip_install: true`
skips the Docker/nginx install and all service management, so the compose file,
system config (both the SSO-only and bootstrap branches), `.env`, nginx site,
systemd units, and the backup wrapper (success + failure metric paths, against a
mocked `docker`) are rendered and asserted without a container runtime.

## Related

- `immich_ml` — the GPU OpenVINO ML LXC this role's `immich_ml_urls` points at.
- `apt_signed_repo` — the fingerprint-verified Docker apt repo.
- `zvol_mount` — mounts the passthrough zvols.
- `acme_certs` (on dns-01) — distributes the wildcard cert (`cert_distribution_targets`).
- `node_exporter_host` / `alloy_host` — metrics textfile + log shipping.
- `proxmox_firewall` — the `sg-immich` guest security group.
