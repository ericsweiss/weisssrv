# Immich (Photo Management)

[Immich](https://immich.app) self-hosted photo and video management, deployed as
a Docker Compose stack on a dedicated NAS-pinned Debian VM and fronted by a host
nginx that terminates the wildcard TLS. SSO-only via Authentik OIDC.

- **Web**: `https://photos.esweiss.com` (internal) / `https://photos.ericsweiss.com` (external)
- **VM**: `immich`, `10.0.10.157`, vmid 157 on `pve-nas-01`
- **GPU ML LXC**: `immich-ml`, `10.0.10.158`, vmid 158 on `pve-nas-01` (Intel Arc B580, OpenVINO)
- **Ansible**: roles `weisssrv.infra.immich` + `weisssrv.infra.immich_ml` (weisssrv-lib), playbooks `ansible/playbooks/{immich,immich-ml}.yml`, `task immich:*` / `task immich-ml:*`
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
                                ├── immich-machine-learning (CPU — ML failover)
                                ├── database  (Postgres + vectorchord/pgvectors)
                                └── redis     (Valkey)
                                                           │
                              machineLearning.urls (tried in order):
                                1▶ http://10.0.10.158:3003 ── immich-ml LXC
                                   (Arc B580 /dev/dri, OpenVINO — PRIMARY)
                                2▶ http://immich-machine-learning:3003
                                   (in-VM CPU container — FAILOVER)
```

The compose stack runs the four upstream services. `immich-server` publishes its
app port on loopback only (`127.0.0.1:2283`) — the host nginx is the sole TLS
terminator — plus its native Prometheus metrics ports (`8081` API, `8082`
microservices) on the LAN, firewall-gated to the k3s nodes.

**GPU ML**: machine-learning inference (face detection, smart search, duplicate
detection, OCR) runs primarily on the Intel Arc B580 via the `immich-ml` LXC
(next section); the in-VM CPU ML container is kept as the automatic failover,
which is why the VM stays sized 6 vCPU / 12 GB.

## GPU machine learning (immich-ml LXC)

A dedicated LXC (`immich-ml`, vmid 158, `10.0.10.158`, 4 cores / 8 GB / 64 GB
rootfs on `local-lvm`) runs the `immich-machine-learning:<immich_version>-openvino`
container under docker compose (`immich_ml` role, `task immich-ml:*`).

### Why an LXC, not the VM

The Arc B580 already serves the Plex LXC, and **VM (VFIO) passthrough is
exclusive** — attaching the card to the Immich VM would take it away from Plex.
**LXC `/dev/dri` sharing is non-exclusive**: the container bind-mounts the host
device nodes and the kernel `xe` driver arbitrates between consumers, so Plex
transcode and ML inference coexist on the one card. The `-openvino` image
bundles its own Intel compute-runtime; the host only supplies the kernel
driver, already proven by Plex.

Mechanics (all via `proxmox_lxc`, create-time): `/dev/dri` bind +
`c 226:* rwm` cgroup allow + the video/render GID idmap (applied for GPU
guests **with or without** bind mounts). Inside the LXC the compose service
carries `group_add` with the passed-through video/render GIDs — an
unprivileged container's root is not host root, so the group match is the only
way to open the 0660 device nodes.

### Endpoint, failover, security

- `immich-server` reads `machineLearning.urls` (an **array**, tried
  sequentially) from the rendered `immich-config.json`: the GPU LXC first, the
  in-VM CPU container second. A GPU-LXC outage degrades to CPU ML
  automatically; nothing breaks.
- The ML API is **authless** by upstream design, so the guest firewall is the
  security boundary: `sg-immich-ml` admits **only** the Immich VM (.157) on
  3003. Photo bytes transit the LAN as plain HTTP between .157 and .158 —
  same trust level as the other intra-LAN service flows.
- No state: the multi-GB model cache is a named docker volume in the rootfs —
  re-downloadable, holds models only (never photos), so the unencrypted
  `local-lvm` placement and the lack of backup enrollment are deliberate.
  Unlike the NAS app guests (plex/gitlab/nextcloud/immich, all `onboot=0` +
  `pve-start-encrypted-guests`), this LXC is `onboot=1`: nothing waits on the
  ZFS unlock, so it boots unattended via plain `pve-guests`.

### Version lockstep

`immich_machine_learning_image` derives from the **same `immich_version` pin**
(`group_vars/all.yml`) as the VM's containers, so any version-bump MR
redeploys both sides with matching tags (`deploy-immich` + `deploy-immich-ml`
both trigger on `all.yml`). Never pin the ML LXC independently.

### Expectations & watch-items

- ML jobs (smart search embeddings, face detection, OCR) should complete
  several-fold faster than on CPU; **video transcoding is unaffected** (it
  lives in `immich-server` on the VM and stays CPU).
- **xe VRAM-leak watch-item** ([openvinotoolkit/openvino#32665](https://github.com/openvinotoolkit/openvino/issues/32665)):
  if OCR batches leak device memory on the Arc card, cap the batch size via
  `MACHINE_LEARNING_MAX_BATCH_SIZE__OCR=3` — a commented-out example sits in
  the compose template; the default stays upstream.
- Switching endpoints does not invalidate existing embeddings — both sides run
  the same release's models.

### Deploy

```bash
task immich-ml:deploy        # provision the LXC (GPU passthrough) + stack
task immich-ml:status        # compose ps + /dev/dri + /ping
task immich:restart          # only if the VM should re-probe the ML urls now
```

The role health-waits on `/ping` (answers before the first-boot model download
finishes — models load lazily on the first job).

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
  ssh eric@10.0.10.157
  cd /mnt/immich-app/compose
  sudo docker compose stop immich-server immich-machine-learning
  gunzip -c /mnt/immich-app/backups/immich-<ts>.sql.gz \
    | sudo docker compose exec -T database psql -U postgres -d postgres
  sudo docker compose up -d
  ```
  The pinned Postgres image already carries the vectorchord/pgvectors extensions,
  so a logical restore reconstructs the vector indexes.

## DNS & ingress

- **Internal** (`photos.esweiss.com`): AdGuard rewrite → `10.0.10.101`
  (Traefik-internal VIP). A direct `immich.esweiss.com` → `10.0.10.157` rewrite
  (+ PTR) exists for admin/SSH convenience. `group_vars/dns.yml`.
- **External** (`photos.ericsweiss.com`): a **DNS-only** Cloudflare CNAME →
  `direct.ericsweiss.com` (`terraform/cloudflare/dns.tf`, `local.dns_records["photos"]`,
  which the library module renders as `module.zone.cloudflare_record.protected["photos"]`).
  It intentionally **bypasses the Cloudflare proxy** because the proxy caps
  request bodies at 100 MB and mobile video uploads exceed that. Traffic still
  lands on MetalLB `.100` via the router `:443` forward. Because the record is
  Terraform-managed, the IngressRoute carries **no** external-dns annotation.
- **IngressRoutes** (`kubernetes/apps/vm-ingress/immich.yaml`, `default` ns):
  external route → `hsts-header` + `ericsweiss-com-tls`; internal route →
  `lan-tailscale-only` + `hsts-header` + `esweiss-com-tls`. Both `scheme: https`
  + `serversTransport: vm-tls-wildcard` to the `immich-backend` Service
  (EndpointSlice → `10.0.10.157:443`).
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

### Authentik objects (Terraform)

The Immich OAuth2 provider, application and the `immich-users` group are declared
in `terraform/authentik/` and applied under supervision
([docs/40](40-authentik-terraform.md)) — **not** in the Authentik admin UI, where
an edit becomes drift the next apply reverts. The values Terraform sets:

| Setting | Value |
|---|---|
| Provider / application name | `Immich` |
| Application slug | `photos` — drives the issuer `https://auth.ericsweiss.com/application/o/photos/` |
| Client type | Confidential |
| Authorization flow | `default-provider-authorization-implicit-consent` |
| Launch URL | `https://photos.ericsweiss.com` |
| Signing key | Authentik signing certificate (RS256) |
| Scopes | `openid`, `email`, `profile` |
| Access gate | `immich-users` group binding |

Redirect URIs (Strict), one per line:

```
https://photos.ericsweiss.com/auth/login
https://photos.ericsweiss.com/user-settings
https://photos.esweiss.com/auth/login
https://photos.esweiss.com/user-settings
app.immich:///oauth-callback
```

The last entry is the mobile app's deep link. Because access is group-gated,
non-members never reach Immich's OAuth callback, which is what makes
`oauth.autoRegister` safe.

Client id/secret live on the **Immich SSO** 1Password item (`client-id`,
`client-secret`); the role renders the OIDC config from them via `op run`
(`no_log`). `issuerUrl` is always the **external** host (`auth.ericsweiss.com`).

Per-user storage quotas are optional: the config maps
`storageQuotaClaim: immich_quota` and `storageLabelClaim: preferred_username`,
which needs a matching scope mapping declared in Terraform.


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
   ssh-keyscan -t ed25519 10.0.10.157        # or: task certs:show-host-keys
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
5. `task immich-ml:deploy` — the GPU LXC's `-openvino` tag rides the same
   `immich_version` pin (lockstep; in CI both `deploy-immich` and
   `deploy-immich-ml` fire on the `all.yml` bump).

Docker Engine itself is pinned (`docker_engine_ce_version` etc. in `all.yml`) and held
(`apt-mark hold`), so the maintenance apt-upgrade won't bump it. The role's
install task carries `allow_change_held_packages: true`, so bumping a Docker pin
in `all.yml` and re-deploying reconciles through the hold in place (then re-holds
at the new version) — no manual unhold. A deliberate *downgrade* to a lower exact
pin still needs a one-off `apt install --allow-downgrades docker-ce=<ver> …`.

## Observability

- **Logs**: `alloy_host` ships the VM's journald (including docker container logs
  — the daemon uses the `journald` log driver) to Loki. The `immich-ml` LXC is
  wired the same way (same role, same journald log driver).
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
  `openssl s_client -connect 10.0.10.157:443 -servername vm.esweiss.com`.
- **Locked out (OIDC broken)**: use `…/auth/login?autoLaunch=0`, or redeploy in
  bootstrap mode.
- **Uploads fail at ~100 MB externally**: the `photos` record got re-proxied
  (orange cloud) — it must stay DNS-only (grey). Re-apply Terraform.
- **`ImmichDown` but the web UI works**: the metrics ports (8081/8082) aren't
  reachable from the k3s nodes — check `sg-immich` and that the ports are
  published (`docker compose ps`).
- **ML jobs slow / CPU spiking on the VM**: immich-server has failed over to the
  in-VM CPU endpoint. Check `task immich-ml:status` (compose up? `/dev/dri`
  present? `/ping` answering?), the `sg-immich-ml` guest firewall (only .157 is
  admitted), and the immich-server logs for ML-endpoint connection errors.
- **Stack won't start after a NAS reboot**: the encrypted pools must unlock first;
  verify vmid 157 is in `zfs_encryption_guest_vmids` and the pools are mounted
  (`docs/32`). (The `immich-ml` LXC is exempt — no encrypted storage, `onboot=1`.)

## Related documentation

- [docs/06-zfs.md](06-zfs.md) - the zvols behind the VM's data disks
- [docs/11-firewall.md](11-firewall.md) - `sg-immich`, `sg-immich-ml`
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) / [docs/42-offsite-backup.md](42-offsite-backup.md) - backup and restore tiers
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) - why the guest starts only after unlock
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the OIDC provider as code
