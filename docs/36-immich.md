# 36 - Immich (Photo Management)

[Immich](https://immich.app) self-hosted photo and video management, deployed as
a Docker Compose stack on a dedicated NAS-pinned Debian VM and fronted by a host
nginx that terminates the wildcard TLS. SSO-only via Authentik OIDC.

- **Web**: `https://photos.esweiss.com` (internal) / `https://photos.ericsweiss.com` (external)
- **VM**: `immich`, `192.168.0.157`, vmid 157 on `pve-nas-01`
- **Ansible**: role `ansible/roles/immich/`, playbook `ansible/playbooks/immich.yml`, `task immich:*`
- **Ingress**: `kubernetes/apps/vm-ingress/{services-immich,immich}.yaml`

## Architecture

```
Client ──(photos.ericsweiss.com, DNS-only CNAME → origin)──┐
Client ──(photos.esweiss.com → AdGuard → .101)─────────────┤
                                                           ▼
                              MetalLB VIP (.100 public / .101 internal)
                                                           ▼
                              Traefik (websecure) ── IngressRoute (scheme https,
                                                      serversTransport vm-tls-wildcard)
                                                           ▼
                              Immich VM .157: host nginx :443 (wildcard cert)
                                                           ▼
                              docker compose: immich-server :2283 (loopback)
                                ├── immich-machine-learning (CPU)
                                ├── database  (Postgres + vectorchord/pgvectors)
                                └── redis     (Valkey)
```

The compose stack runs the four upstream services. `immich-server` publishes its
app port on loopback only (`127.0.0.1:2283`) — the host nginx is the sole TLS
terminator — plus its native Prometheus metrics ports (`8081` API, `8082`
microservices) on the LAN, firewall-gated to the k3s nodes.

**No GPU**: machine learning runs on CPU (the NAS iGPU is allocated to the Plex
LXC). The VM is sized 6 vCPU / 12 GB for the CPU-hungry ML jobs (face detection,
smart search, duplicate detection).

## Storage & encryption

All data lives on encrypted ZFS passthrough zvols on the NAS — **no NFS** (the VM
is pinned to the NAS host, so block passthrough is both faster and simpler).

| Mount | zvol | Size | Pool / encryption | Contents |
|---|---|---|---|---|
| `/mnt/immich-app` | `ssd/appdata/immich/app` | 20 GB | `ssd/appdata` (aes-256-gcm) | compose dir, rendered config + `.env`, ML model cache, pg_dump backups |
| `/mnt/immich-postgres` | `ssd/appdata/immich/postgres` | 24 GB | `ssd/appdata` (aes-256-gcm) | Postgres data directory |
| `/mnt/immich-data` | `tank/immich-data/disk` | 2 TB **sparse** | `tank/immich-data` (aes-256-gcm) | the photo library (`UPLOAD_LOCATION=/mnt/immich-data/library`) |

- The app + postgres zvols are children of `ssd/appdata` (an encryption root),
  so they inherit encryption and ride the `ssd/appdata` → `archive` raw send.
- The library zvol is a child of the pre-existing `tank/immich-data` encryption
  root (created empty during storage bootstrap), so it inherits aes-256-gcm and
  is already covered by the `tank/immich-data` archive `SRC_LIST` entry — **zero
  backup-config change**. It is created **sparse** (`zfs create -s -V`, via the
  `proxmox_vm` per-disk `sparse: true` option) so the 2 TB provision grows on
  demand.
- All three zvols carry `vzdump_backup: false` — they are backed up via the
  archive tier, not double-stored in the nightly vzdump.
- The VM **root disk** (40 GB on `ssd/pve`) is captured by the nightly vzdump of
  every guest; that pool is encrypted, so the root is encrypted at rest too.
- Boot: because every disk lives on an encrypted pool, the guest is `onboot=0`
  and started by `pve-start-encrypted-guests.service` after the pools unlock —
  vmid 157 is in `zfs_encryption_guest_vmids` (`host_vars/pve-nas-01.yml`). See
  [docs/32-zfs-encryption.md](32-zfs-encryption.md).

Encryption posture summary: [docs/06-zfs.md](06-zfs.md) (At Rest table).

## Backups

Two tiers plus an application-level DB dump:

1. **vzdump** (nightly, `all: true`) — the VM **root disk** → `tank/proxmox`
   (encrypted). The data zvols are excluded (`backup=0`).
2. **archive** (`archive-backupctl`, raw-encrypted `zfs send -w`) — `ssd/appdata`
   (app + postgres zvols) → `archive/appdata`, and `tank/immich-data` (the photo
   library) → `archive/immich-data`. Both already in `SRC_LIST` — no edit.
3. **pg_dumpall** (nightly `immich-backup.timer`, `02:30`) — a logical database
   dump gzipped onto the app zvol at `/mnt/immich-app/backups/immich-*.sql.gz`
   (rides tier 2). This is the point-in-time DB recovery path and is uniform with
   the GitLab backup plumbing. Immich's own built-in DB dump is disabled
   (`backup.database.enabled: false` in the config file) to avoid duplication.
   `immich-backup-run.sh` emits node_exporter textfile metrics
   (`immich_backup_last_run_success` / `_last_success_timestamp_seconds` / …)
   read by the `ImmichBackupFailed` / `ImmichBackupStale` alerts.

Retention: `immich_backup_keep_days` (default 7) local dumps; the archive tier
keeps its own `archsync` grandfather set.

### Restore

- **Photo library / whole zvol**: standard app-data-zvol restore from the archive
  (stop the VM to release the passthrough zvol, `zfs recv`, re-attach). See
  [docs/17-disaster-recovery.md](17-disaster-recovery.md) (App-data zvols).
- **Database only** (roll back a corruption): stop the stack, restore the
  Postgres data zvol from archive, **or** replay a pg_dumpall:
  ```bash
  ssh eric@192.168.0.157
  cd /mnt/immich-app/compose
  sudo docker compose stop immich-server immich-machine-learning
  gunzip -c /mnt/immich-app/backups/immich-<ts>.sql.gz \
    | sudo docker compose exec -T database psql -U postgres -d postgres
  sudo docker compose up -d
  ```
  The pinned Postgres image already carries the vectorchord/pgvectors extensions,
  so a logical restore reconstructs the vector indexes.

## DNS & ingress

- **Internal** (`photos.esweiss.com`): AdGuard rewrite → `192.168.0.101`
  (Traefik-internal VIP). A direct `immich.esweiss.com` → `192.168.0.157` rewrite
  (+ PTR) exists for admin/SSH convenience. `group_vars/dns.yml`.
- **External** (`photos.ericsweiss.com`): a **DNS-only** Cloudflare CNAME →
  `direct.ericsweiss.com` (`terraform/cloudflare/dns.tf`, `cloudflare_record.photos`).
  It intentionally **bypasses the Cloudflare proxy** because the proxy caps
  request bodies at 100 MB and mobile video uploads exceed that. Traffic still
  lands on MetalLB `.100` via the router `:443` forward. Because the record is
  Terraform-managed, the IngressRoute carries **no** external-dns annotation.
- **IngressRoutes** (`kubernetes/apps/vm-ingress/immich.yaml`, `default` ns):
  external route → `hsts-header` + `ericsweiss-com-tls`; internal route →
  `lan-tailscale-only` + `hsts-header` + `esweiss-com-tls`. Both `scheme: https`
  + `serversTransport: vm-tls-wildcard` to the `immich-backend` Service
  (EndpointSlice → `192.168.0.157:443`).
- **TLS on the VM**: the host nginx serves the `*.esweiss.com` wildcard cert
  distributed by `acme_certs` (see Deploy). Traefik validates the backend against
  `serverName: vm.esweiss.com` (covered by the wildcard) — no `insecureSkipVerify`.
- **Real client IP / trusted proxy**: trust chain is `client → Traefik pod →
  (k3s SNAT to the node IP) → VM nginx → immich-server`. The VM nginx trusts
  **only the nine k3s node IPs** as `real_ip` sources (`immich_nginx_real_ip_from`)
  and **replaces** `X-Forwarded-For` with that single resolved client, so a
  directly connected LAN/admin client (`:443` is open for debug) cannot forge the
  header — its inbound XFF is ignored and its real address stands. immich-server
  in turn trusts only its immediate hop (`IMMICH_TRUSTED_PROXIES` = the pinned
  compose bridge gateway `172.28.0.1`), so a spoofed header can never be walked
  back past nginx's resolved address.
- **Firewall** (`sg-immich`): 443 from k3s nodes + admin LAN/Tailscale; 8081/8082
  (native metrics) from k3s nodes only. `guest_security_groups: [sg-vm-admin,
  sg-immich, sg-metrics]`.

## SSO (Authentik OIDC)

Immich is **SSO-only**: after the one-time admin bootstrap, password login is
disabled and the web login page auto-redirects to Authentik. System config
(OIDC, telemetry, backup) is declared in `IMMICH_CONFIG_FILE`
(`/mnt/immich-app/config/immich-config.json`, rendered by the role); when a
config file is present, the Immich admin UI settings become read-only.

### Authentik provider + application (create manually in the admin UI)

1. **Providers → Create → OAuth2/OpenID Provider**
   - Name: `Immich`
   - Authorization flow: `default-provider-authorization-implicit-consent`
     (or explicit-consent)
   - Client type: **Confidential**
   - Client ID / Client Secret: generated — store them in 1Password **Immich SSO**
     (`client-id`, `client-secret`).
   - Redirect URIs (Strict), one per line:
     - `https://photos.ericsweiss.com/auth/login`
     - `https://photos.ericsweiss.com/user-settings`
     - `https://photos.esweiss.com/auth/login`
     - `https://photos.esweiss.com/user-settings`
     - `app.immich:///oauth-callback` (the mobile app deep link)
   - Signing Key: your Authentik signing certificate (RS256).
   - Scopes: `openid`, `email`, `profile`.
2. **Applications → Create**
   - Name: `Immich`, Slug: **`photos`** (this drives the issuer URL
     `https://auth.ericsweiss.com/application/o/photos/`).
   - Provider: `Immich`.
   - Launch URL: `https://photos.ericsweiss.com`.
3. **Group-gated access**: create/choose an Authentik group `immich-users`, then
   bind a **policy** on the Immich application that permits only that group
   (Application → *Policy / Group / User Bindings* → bind the group). Non-members
   never reach Immich's OAuth callback, so `oauth.autoRegister` is safe.
4. (Optional) Storage quota / role via claims: the config maps
   `storageQuotaClaim: immich_quota` and `storageLabelClaim: preferred_username`
   — add a scope mapping in Authentik if you want per-user quotas.

The role renders the OIDC config from these values (client id/secret injected via
`op run`, `no_log`). `issuerUrl` is always the **external** host
(`auth.ericsweiss.com`).

### One-time admin bootstrap

The **first** Immich account (the admin/owner) must be created through the
password Admin-Registration page — OIDC auto-register only ever mints *regular*
users, so with SSO-only from the start no admin would ever exist.

1. Deploy once with `immich_bootstrap_mode: true` (set it as an extra var, e.g.
   `task immich:deploy -- -e immich_bootstrap_mode=true`). This enables password
   login and turns OIDC auto-launch off.
2. Open the **direct-to-VM** host `https://immich.esweiss.com` (LAN/Tailscale
   only) — do this **before** the public `photos.ericsweiss.com` record exists.
   Immich makes the first registered account the owner/admin, so it must be
   claimed while the instance is reachable only on the LAN; otherwise a stranger
   who reaches the public URL during bootstrap could register it first. Complete
   **Admin Registration** using the **same email as your Authentik account** and
   the password stored in 1Password **Immich Secrets**/`admin-bootstrap-password`.
3. Redeploy with the default `immich_bootstrap_mode: false` (SSO-only). OIDC then
   links your Authentik login to the existing admin user **by email**.
4. Manage additional admins/roles in the Immich UI (Administration → Users).

**Escape hatch**: if OIDC ever breaks while SSO-only, reach the login form
directly at `https://photos.esweiss.com/auth/login?autoLaunch=0`, or redeploy in
bootstrap mode.

## Deploy

Ansible provisions the VM + stack; Flux reconciles the ingress. **First deploy is
a defined sequence** because the wildcard cert can only be pushed after the VM
exists:

1. **Create the 1Password items** in the Homelab vault (see
   [docs/15-credential-rotation.md](15-credential-rotation.md)): **Immich
   Secrets** (postgres-password, admin-bootstrap-password) and **Immich SSO**
   (client-id, client-secret from the Authentik provider above).
2. **Provision + install** the VM and stack (in bootstrap mode for the very first
   run — see SSO bootstrap):
   ```bash
   task immich:deploy -- -e immich_bootstrap_mode=true
   ```
   The role seeds a self-signed TLS placeholder so nginx starts before the real
   cert arrives; `photos.*` will 502 through Traefik until step 3.
3. **Capture the VM host key and push the wildcard cert**. Get the VM's SSH host
   key and paste it into the (currently commented) `immich` entry in
   `ansible/inventories/prod/host_vars/dns-01.yml`:
   ```bash
   ssh-keyscan -t ed25519 192.168.0.157        # or: task certs:show-host-keys
   ```
   Uncomment the block, set the real `host_key`, then:
   ```bash
   task dns:deploy      # AdGuard rewrites + first cert push to the VM
   ```
   nginx now serves the real `*.esweiss.com` wildcard and Traefik validates it.
4. **Claim the admin before any public exposure.** Complete the admin bootstrap
   (SSO bootstrap steps 2–3) over the direct-to-VM host `https://immich.esweiss.com`
   (LAN/Tailscale only — the public `photos.ericsweiss.com` record and Traefik
   ingress do not exist yet), then redeploy SSO-only: `task immich:deploy`. This
   claims the first-registered-account owner/admin while the instance is reachable
   only on the LAN, closing the takeover window before the public record goes live.
5. **Apply external DNS**: `task terraform:apply` (creates the `photos` DNS-only
   CNAME). Trigger a DDNS run if the origin IP is stale.
6. **Flux** reconciles `kubernetes/apps/vm-ingress` (Service/EndpointSlice/
   IngressRoutes) and `service-monitors` on merge to `main`.
7. **Verify**: `task immich:verify` (web UI both hosts, `/api/server/ping`,
   native metrics, DB health, zvol mounts).

Day-2: `task immich:status`, `immich:logs`, `immich:restart`, `immich:backup`,
`immich:console`. The `deploy-immich` CI job re-runs the playbook on merges that
touch the role / playbook / pins.

## Upgrades

Immich couples its Postgres image to each release (the vectorchord/pgvectors
build), and major DB upgrades are release-coupled. **Always** take the
`immich_version`, `immich_postgres_version` (+`_digest`), and
`immich_valkey_version` (+`_digest`) pins for a given release from **that
release's own `docker/docker-compose.yml`** — never bump the DB independently.

1. Read the target release's compose (`github.com/immich-app/immich`, tag
   `docker/docker-compose.yml`) for the exact image tags + digests.
2. Update the four `immich_*` pins in `group_vars/all.yml`, run
   `task flux:sync-versions`, commit both files.
3. Review the release notes for **breaking DB migrations**; take a `task
   immich:backup` (pg_dumpall) first.
4. `task immich:deploy` (recreates the containers via `docker compose up -d`).

Docker Engine itself is pinned (`docker_ce_version` etc. in `all.yml`) and held
(`apt-mark hold`), so the maintenance apt-upgrade won't bump it. The role's
install task carries `allow_change_held_packages: true`, so bumping a Docker pin
in `all.yml` and re-deploying reconciles through the hold in place (then re-holds
at the new version) — no manual unhold. A deliberate *downgrade* to a lower exact
pin still needs a one-off `apt install --allow-downgrades docker-ce=<ver> …`.

## Observability

- **Logs**: `alloy_host` ships the VM's journald (including docker container logs
  — the daemon uses the `journald` log driver) to Loki.
- **Metrics**: `node_exporter_host` (host, `:9101`) + Immich's native Prometheus
  metrics (`IMMICH_TELEMETRY_INCLUDE=all`, `:8081`/`:8082`) scraped by the
  `immich` ServiceMonitor (`kubernetes/infrastructure/observability/service-monitors/immich.yaml`).
- **Alerts** (`kube-prometheus-stack` `release.yaml`):
  - `ImmichDown` (`homelab.monitoring`) — native metrics unscrapeable 5 min.
  - `ImmichBackupFailed` / `ImmichBackupStale` (`homelab.scripts`) — the nightly
    pg_dumpall.
  - `EndpointDown` — the `photos.esweiss.com` blackbox probe (added to the
    blackbox target list) covers the ingress path.
- **Grafana**: Immich ships **no** official Grafana dashboard for the pinned
  release — [upstream monitoring docs](https://docs.immich.app/features/monitoring/)
  point you at building your own against the metrics — so none is vendored here.
  The only community option (Grafana.com ID `22555`, "Immich Overview") is a
  single-revision, unmaintained (Dec 2024) dashboard authored for a Kubernetes
  **Helm** deployment; its panel variables and label selectors do not match this
  compose/static-endpoint VM, so importing it as-is would render mostly empty.
  The metrics are explorable in Grafana today; vendor a dashboard via the
  `grafana_dashboard` configMapGenerator (`observability/dashboards/`) once a
  good, native-metrics dashboard emerges.

## Mobile app

Install the Immich app, **Server URL** `https://photos.ericsweiss.com`, and log
in — the app uses the OAuth flow (the `app.immich:///oauth-callback` redirect
URI above). Uploads go straight to the origin (DNS-only, proxy-bypassed) so large
videos aren't capped at 100 MB. Enable background backup in the app for
automatic phone-photo sync.

## Troubleshooting

- **`photos.*` returns 502 via Traefik**: the VM nginx is serving the self-signed
  placeholder (cert not yet pushed) — run step 3 (Deploy). Confirm with
  `openssl s_client -connect 192.168.0.157:443 -servername vm.esweiss.com`.
- **Locked out (OIDC broken)**: use `…/auth/login?autoLaunch=0`, or redeploy in
  bootstrap mode.
- **Uploads fail at ~100 MB externally**: the `photos` record got re-proxied
  (orange cloud) — it must stay DNS-only (grey). Re-apply Terraform.
- **`ImmichDown` but the web UI works**: the metrics ports (8081/8082) aren't
  reachable from the k3s nodes — check `sg-immich` and that the ports are
  published (`docker compose ps`).
- **Stack won't start after a NAS reboot**: the encrypted pools must unlock first;
  verify vmid 157 is in `zfs_encryption_guest_vmids` and the pools are mounted
  (`docs/32`).
