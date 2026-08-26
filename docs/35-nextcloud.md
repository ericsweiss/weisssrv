# Nextcloud Deployment

Nextcloud runs on a dedicated, NAS-pinned Debian VM (`10.0.10.156`, vmid 156)
as a Docker Compose stack. It is reachable at **cloud.ericsweiss.com** (external
+ internal) and **cloud.esweiss.com** (internal only), SSO-only via Authentik
OIDC. All state rides ZFS zvol passthrough disks — there is **no NFS** anywhere.

- **Ansible role**: `weisssrv.infra.nextcloud` (weisssrv-lib)
- **Playbook**: `ansible/playbooks/nextcloud.yml` (`task nextcloud:deploy`)
- **Ingress**: `kubernetes/apps/vm-ingress/{services-nextcloud,nextcloud}.yaml`
- **Metrics**: `kubernetes/infrastructure/observability/service-monitors/nextcloud.yaml`

## Architecture

```
Client ──HTTPS──▶ Traefik (MetalLB .100 public / .101 internal)
                    │  IngressRoute (default ns), scheme https,
                    │  serversTransport vm-tls-wildcard (serverName vm.esweiss.com)
                    ▼
                host nginx on VM 156 :443  (distributed *.esweiss.com wildcard cert)
                    │  proxy_pass http://127.0.0.1:8080
                    ▼
          docker compose project "nextcloud" (bridge 172.28.0.0/16)
            ├─ nextcloud:<ver>-apache      (127.0.0.1:8080:80)
            ├─ nextcloud-cron  (/cron.sh, every 15 min)
            ├─ nextcloud-db    (postgres, PGDATA on /mnt/nextcloud-postgres)
            ├─ nextcloud-redis (cache + transactional file locking)
            └─ nextcloud-exporter  (0.0.0.0:9205  ← Prometheus scrapes VM:9205)
```

- The VM is **pinned to pve-nas-01** (`proxmox_vm_cpu_type: host`, `onboot=0`), started by
  `pve-start-encrypted-guests.service` after the encrypted ssd/tank pools unlock
  (vmid 156 is in `zfs_encryption_guest_vmids`, host_vars/pve-nas-01.yml).
- Nextcloud accepts **both** hostnames (`trusted_domains`); only the protocol
  (`OVERWRITEPROTOCOL=https`) and CLI URL are overwritten, so the served host
  tracks the incoming `Host` header.
- **Real client IP / trusted-proxy chain.** The trust chain is
  `client → Traefik pod → (k3s/flannel SNATs the pod source to the k3s NODE IP,
  since .156 is a VM outside the pod network) → VM nginx → nextcloud container`.
  The VM **nginx** resolves the real client IP with its `real_ip` module,
  trusting **only the k3s node IPs** (`10.0.10.202-207`, `.222/.223/.227`) as
  `set_real_ip_from` sources and walking Traefik's `X-Forwarded-For` back to the
  client (`real_ip_recursive on`); it then **replaces** `X-Forwarded-For` with
  that single resolved value (not `$proxy_add_x_forwarded_for`). Nextcloud's
  `TRUSTED_PROXIES` is therefore only its immediate hop — `127.0.0.1` plus the
  compose bridge subnet `172.28.0.0/16` (the gateway docker-proxy presents as
  the client of the loopback-published port). The firewall also permits direct
  `:443` from admin/LAN for debugging; because such a directly connected client
  is **not** in `set_real_ip_from`, nginx ignores its inbound `X-Forwarded-For`
  and `$remote_addr` stays its true address — so it **cannot** spoof the client
  IP used for audit logs, brute-force protection, and rate limiting. (Trusting
  the LAN `/24` at either layer would reopen that spoof, which is why it is
  deliberately excluded.)
- Container logs use the **journald** Docker log driver, so `alloy_host` ships
  them to Loki on the same journald path as the host's own units.

## Storage, encryption, and backup

Three ZFS zvol passthrough disks (created by `proxmox_vm`, mounted by
`zvol_mount`; see `vm_additional_disks` in `hosts.yml`):

| Mount | zvol | Size | scsi | Contents | Encrypted (root) | Backup |
|---|---|---|---|---|---|---|
| `/mnt/nextcloud-app` | `ssd/appdata/nextcloud/app` | 20G | 1 | compose dir, html/config, nightly pg_dump | Yes (`ssd/appdata`) | archive (raw) + pg_dump; vzdump-excluded |
| `/mnt/nextcloud-postgres` | `ssd/appdata/nextcloud/postgres` | 16G | 2 | PostgreSQL PGDATA | Yes (`ssd/appdata`) | archive (raw); vzdump-excluded |
| `/mnt/nextcloud-data` | `tank/nextcloud-data/disk` | 2T **sparse** | 3 | Nextcloud user data (`/data`) | Yes (`tank/nextcloud-data`) | archive (raw); vzdump-excluded |
| VM root (scsi0) | `ssd/pve` | 40G | 0 | OS + Docker | Yes (`ssd/pve`) | vzdump (`all: true`) |

- `tank/nextcloud-data` is a **pre-existing** encryption root (created manually,
  like `ssd/k3s-etcd`) and is already in the `archive-backupctl` `SRC_LIST`, so
  the 2T data zvol inherits `aes-256-gcm` encryption and rides the nightly
  raw-encrypted `zfs send -w` to `archive/nextcloud-data` — **zero backup-config
  change**. The app/postgres zvols are children of `ssd/appdata`, likewise
  already covered by `ssd/appdata` → `archive/appdata`.
- The data zvols carry `vzdump_backup: false` (`backup=0`) so the nightly vzdump
  captures only the OS root disk and doesn't double-store the app data.
- A **nightly `pg_dump`** (`nextcloud-backup.timer`, 02:30) writes a
  gzip'd logical dump to `/mnt/nextcloud-app/backups` (which rides the app zvol →
  archive), keeping `nextcloud_backup_keep_days` (7) locally, and emits
  `nextcloud_backup_*` node_exporter textfile metrics.
- **Data ownership**: the container runs as `www-data` (uid/gid 33); the role
  chowns `/mnt/nextcloud-app/html` and `/mnt/nextcloud-data/data` to `33:33`.

### Growing the data volume

The bulk data zvol is thin-provisioned at a 2T ceiling. To raise it:

```bash
ssh eric@10.0.10.102 "sudo zfs set volsize=4T tank/nextcloud-data/disk"
ssh eric@10.0.10.156 "sudo resize2fs /dev/disk/by-id/... "   # the ext4 fs on the zvol
```

(Increase `size:` in `hosts.yml` to keep inventory truthful; `proxmox_vm` never
shrinks or reprovisions an existing zvol.)

## SSO (Authentik OIDC, SSO-only)

Login is **SSO-only**: the local login form is hidden and users are auto-redirected
to Authentik. The break-glass local admin is reachable at
`https://cloud.ericsweiss.com/login?direct=1`.

The role installs + configures the `user_oidc` app idempotently via `occ`
(provider id `authentik`, discovery
`https://auth.ericsweiss.com/application/o/cloud/.well-known/openid-configuration`,
scopes `openid email profile groups`, `preferred_username`→uid, `groups`→groups,
group provisioning on, `allow_multiple_user_backends=0`).

### Authentik objects (Terraform)

The Nextcloud OAuth2 provider, application and the `nextcloud-users` group are
declared in `terraform/authentik/` and applied under supervision
([docs/40](40-authentik-terraform.md)) — **not** in the Authentik admin UI. The
values Terraform sets, which the `user_oidc` config above must agree with:

| Setting | Value |
|---|---|
| Provider / application name | `Nextcloud` |
| Application slug | `cloud` — this is what the discovery URI path contains |
| Client type | Confidential |
| Authorization flow | `default-provider-authorization-implicit-consent` |
| Redirect URIs (Strict) | `https://cloud.ericsweiss.com/apps/user_oidc/code`<br>`https://cloud.esweiss.com/apps/user_oidc/code` |
| Scopes | `openid`, `email`, `profile`, plus a mapping that emits the `groups` claim |
| Signing key | the Authentik self-signed certificate (enables RS256 / a JWKS) |
| Access gate | `nextcloud-users` group binding |

The client id/secret live on the **Nextcloud SSO** 1Password item (`client-id`,
`client-secret`) and are read by both ESO and Terraform.

> There is deliberately **no** `nextcloud-admins` group. Nextcloud admin
> membership is managed inside Nextcloud (Settings → Users), not mapped from an
> Authentik group — the first admin logs in via SSO and is promoted, or you use
> the break-glass local admin.


### SSRF toggle (`allow_local_remote_servers`) — accepted risk

The `nextcloud` role sets `allow_local_remote_servers=true` (`tasks/main.yml`).
Nextcloud's SSRF guard (`LocalAddressChecker`) otherwise refuses any server-side
fetch to an RFC1918 address, and split-horizon DNS resolves `auth.ericsweiss.com`
to the **internal** Traefik VIP `10.0.10.101` — so the `user_oidc` discovery
fetch is itself a "local" request and SSO login fails without it. It is
**required** for OIDC to work here.

The caveat is that the toggle is **global**: Nextcloud has no per-URL allowlist
and no per-host `user_oidc` discovery override that would bypass the global
`LocalAddressChecker`, so enabling it widens **every** server-side fetch surface,
not just OIDC discovery. The surfaces actually reachable today are federation
("add remote share") and the `text` app's reference/link previews — both
authenticated-user-triggerable arbitrary-host fetches. SSRF only *relays* the
VM's existing LAN reach; it grants no new network position.

**Risk = LOW under the current posture**, bounded by two facts: login is
SSO-only with single-operator provisioning (no self-registration app), and
`files_external` is **DISABLED** (the highest-value SSRF vector). It escalates to
**MEDIUM** the moment untrusted or family accounts are added — at which point the
mitigation is a **default-deny egress firewall on the nextcloud VM** (allowlisting
only DNS, the OIDC VIP, the SMTP relay, apt, NFS, NTP). That is future/supervised
work and is **not** implemented today.

## Outgoing mail (SMTP relay)

Nextcloud sends notifications, share invites, and password-reset mail through the
homelab SMTP relay, configured as **`smtp-relay.esweiss.com:25` without SASL**
(`nextcloud_smtp_*` in `group_vars/nextcloud_servers.yml`); the relay adds TLS on
the Gmail hop.

> **Known gap.** The relay's `mynetworks` is loopback-only and its
> `smtpd_relay_restrictions` is `permit_sasl_authenticated,
> reject_unauth_destination` (docs/10) — the LAN `permit_mynetworks` path the
> port-25 config assumed no longer exists, so unauthenticated relay from `.156`
> is refused. The fix is to move Nextcloud onto submission with the null-client
> SASL credentials, the way `gitlab_servers.yml` already does; tracked in
> `docs/16-next-steps.md`.

The `nextcloud` role applies this with `occ config:system:set mail_*` (the
image's `SMTP_*` env only autoconfigures a fresh install, not the live instance),
from-address `nextcloud@ericsweiss.com`. Tune via the `nextcloud_smtp_*` /
`nextcloud_mail_*` role defaults. Test after deploy from Admin settings → Basic
settings → Email server → *Send email*.

## Deploy runbook

**Prerequisites** (one-time):

1. Create the two 1Password items (Homelab vault) — see docs/15:
   - **Nextcloud Secrets**: `admin-password`, `postgres-password`,
     `serverinfo-token` (random ≥32 chars each).
   - **Nextcloud SSO**: `client-id`, `client-secret` (from the Authentik steps).
2. Complete the **Authentik setup** above.

**Deploy** (`main` merge triggers the `deploy-nextcloud` CI job; or run locally):

```bash
task nextcloud:deploy-check        # dry-run
task nextcloud:deploy              # provisions VM 156 + installs the stack
```

The role generates a **self-signed bootstrap cert** so nginx starts before the
real wildcard cert is distributed. To install the real cert (POST-PROVISION):

3. Capture the new VM's SSH host key and add the cert-distribution target:
   ```bash
   task certs:show-host-keys        # copy the nextcloud (10.0.10.156) ssh-ed25519 line
   ```
   Uncomment the `nextcloud` block in
   `ansible/inventories/prod/host_vars/dns-01.yml` (`acme_certs_distribution_targets`),
   paste the real `host_key`, commit, then:
   ```bash
   task dns:deploy                  # issues + pushes *.esweiss.com to /etc/ssl/nextcloud, reloads nginx
   ```
4. Flux reconciles `kubernetes/apps/vm-ingress` (Service/EndpointSlice +
   IngressRoutes) on push; external-dns creates the `cloud.ericsweiss.com`
   proxied CNAME.
5. Verify:
   ```bash
   task nextcloud:verify            # both hosts, occ status, exporter :9205
   task nextcloud:status            # compose ps + IngressRoutes
   ```

## Upload size

- The VM nginx and the container apache impose **no** upload cap
  (`client_max_body_size 0`, `PHP_UPLOAD_LIMIT=16G`).
- Nextcloud clients (desktop/mobile/web) **chunk** uploads (default 10 MiB), so
  the Cloudflare-proxied external path's 100 MB single-request limit is not hit
  for normal uploads.
- A **raw single-PUT WebDAV** upload over the external host
  (`cloud.ericsweiss.com`) larger than ~100 MB will be rejected by Cloudflare.
  Use `cloud.esweiss.com` (internal, non-proxied) for large raw WebDAV, or rely
  on the chunked client. Adjust chunk size with
  `occ config:app:set files max_chunk_size --value=<bytes>` if needed.

## Observability

- **Logs**: `alloy_host` (journald → Loki); Docker's journald log driver puts
  container logs on the same path.
- **Metrics**: `nextcloud-exporter` on `:9205` (auth via the serverinfo token),
  scraped by the in-cluster Prometheus through
  `service-monitors/nextcloud.yaml` (static Endpoints → 10.0.10.156:9205).
  Host metrics via `node_exporter_host` (`:9101`).
- **Blackbox**: `cloud.esweiss.com` probed (`http_sso` module — SSO redirect
  counts as up) in `exporters/blackbox-exporter.yaml`.
- **Alerts** (kube-prometheus-stack `release.yaml`):
  `NextcloudDown` (`nextcloud_up == 0` / absent, `homelab.monitoring`),
  `NextcloudBackupFailed` / `NextcloudBackupStale` (`homelab.scripts`), plus the
  generic `EndpointDown` on the blackbox probe.
- **Grafana**: no dashboard shipped yet — the upstream xperimental exporter
  dashboard uses a `${DS_LOCAL}` import-input datasource that needs adapting for
  the sidecar. Metrics are queryable in Explore; see docs/16 follow-up.

## Restore runbook

**Database (logical dump):**

```bash
ssh eric@10.0.10.156
cd /mnt/nextcloud-app/compose
sudo docker compose exec -T nextcloud php occ maintenance:mode --on
gunzip -c /mnt/nextcloud-app/backups/nextcloud-db-<ts>.sql.gz \
  | sudo docker compose exec -T nextcloud-db psql -U nextcloud -d nextcloud
sudo docker compose exec -T nextcloud php occ maintenance:mode --off
```

**Whole-VM / zvol restore**: the OS root disk is in the nightly vzdump
(`tank/proxmox` → `archive/proxmox`); the data zvols are in the raw-encrypted
archive replicas (`archive/appdata`, `archive/nextcloud-data`). Restore per
docs/17 (App-data zvols: stop VM 156 to release the passthrough zvols first;
encrypted-tree restores need the source pool key loaded before mount).

## Docker Engine version bumps

Docker Engine + the plugins are pinned in `group_vars/all.yml` as **shared**
pins consumed by the `docker_engine` role — `docker_engine_ce_version`,
`docker_engine_containerd_version`, `docker_engine_buildx_plugin_version`,
`docker_engine_compose_plugin_version` — from `download.docker.com` (Debian trixie),
and `dpkg`-held so the maintenance apt-upgrade cannot bump them under a running
stack. The same four pins back **immich, immich_ml and nextcloud**, so a bump
redeploys all three guests. Because download.docker.com prunes old versions over
time, bump them together when a pin ages out:

```bash
apt-cache policy docker-ce            # on the VM, or read the repo Packages index
# update the four docker_* pins in all.yml, then redeploy each consumer:
task nextcloud:deploy
task immich:deploy
task immich-ml:deploy
```

(`task maintenance:check-versions` tracks the four image pins; the docker apt
pins are `category: manual` there — bumped from the repo Packages index.)

## Related documentation

- [docs/06-zfs.md](06-zfs.md) - the zvols behind the VM's data disks
- [docs/10-mail.md](10-mail.md) - the SMTP relay Nextcloud sends through
- [docs/11-firewall.md](11-firewall.md) - `sg-nextcloud`
- [docs/17-disaster-recovery.md](17-disaster-recovery.md) / [docs/42-offsite-backup.md](42-offsite-backup.md) - backup and restore tiers
- [docs/32-zfs-encryption.md](32-zfs-encryption.md) - why the guest starts only after unlock
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the OIDC provider as code
