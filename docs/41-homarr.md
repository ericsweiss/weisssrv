# Homarr Dashboard

[Homarr](https://github.com/homarr-labs/homarr) is a self-hosted homelab
dashboard / launcher: a board of live-status widgets (integrations that poll
each service's API) plus bookmark tiles. This deployment runs Homarr as a k3s
workload in the `homarr` namespace and exposes it at
**`dashboard.ericsweiss.com`** (external) / **`dashboard.esweiss.com`**
(internal), gated by Authentik OIDC.

- Namespace: `homarr`
- Manifests: [`kubernetes/apps/homarr/`](../kubernetes/apps/homarr/)
- Image: `ghcr.io/homarr-labs/homarr` (public; v-prefixed docker tag), pinned
  via `homarr_version` in `ansible/inventories/prod/group_vars/all.yml`
- Authentik objects: [`terraform/authentik/`](../terraform/authentik/) (docs/40)

---

## Deploy model

Raw manifests reconciled by Flux — **not** the Helm chart. Homarr's runtime
config is env vars + the SQLite DB (populated through the UI), so a chart adds
nothing over the repo's standard app shape: `namespace` + `externalsecret` +
`storage` (NFS PV/PVC) + `deployment` + `service` + `ingress-routes` +
`certificate` + `networkpolicy` + `vpa`, with `default-deny-ingress` from the
shared `netpol-baseline` component.

One Deployment, `replicas: 1`, `strategy: Recreate`, one container on :7575.
NAS-avoiding + modern-CPU (state is on NFS, not a node-local zvol). The
nginx-fronted image runs its **own root entrypoint** (it templates `/etc/nginx`
at boot), so it does **not** run fully non-root — forcing `runAsUser: 1000`
crash-looped it on first deploy (EACCES templating `/etc/nginx`; see the
`deployment.yaml` comment). It still hardens everything it can: the
container drops **ALL** capabilities and adds back only the four load-bearing
ones — `CHOWN`, plus `SETUID`/`SETGID` (the nginx master stays root and drops
its workers to the `nginx` user) and `DAC_OVERRIDE` (nginx tmp/log dirs) — with
seccomp `RuntimeDefault`, `allowPrivilegeEscalation: false`, and `fsGroup: 2000`.
The NFS export `all_squash`es every write to `1000:2000` regardless of the
in-container UID, and the namespace PSS enforces `baseline` (a strict
`restricted` enforce would reject the root entrypoint + the added caps).

## Storage — NFS SQLite (single-writer)

All state lives under a single writable mount at `/appdata` (the SQLite DB is
`/appdata/db/db.sqlite`), backed by NFS `/appdata/homarr` on the encrypted
`ssd/appdata` dataset — captured by the nightly `ssd/appdata -> archive`
raw-ZFS replication (docs/06). The NFS export `all_squash`es every write to
`1000:2000`; the `/appdata/homarr` subdir is created + owned `1000:2000` by the
`nas_storage` role (`nas_storage_appdata_dirs`).

SQLite tolerates exactly **one writer**. `replicas: 1` + `strategy: Recreate`
guarantee no two pods ever dual-mount the RWO PV, which is what makes
SQLite-over-NFS safe here (the same pattern the Hindsight app runs on this
export). The volume is hard-mounted NFSv4.2 with `xprtsec=tls` by **hostname**
(`pve-nas-01.esweiss.com`) — an IP mount fails the `*.esweiss.com` cert
handshake (docs/07). **Fallback** if WAL/locking issues ever appear: move state
to a dedicated ext4-on-zvol PV (NAS-pinned + nodeAffinity, like the Postgres
apps). Not implemented now.

## SSO — Authentik OIDC (SSO-only)

Homarr runs its **own** Authentik OIDC login — there is **no** Traefik
forward-auth middleware on the IngressRoutes (same posture as Hermes). Traefik
passes straight to Homarr, which 302s an unauthenticated browser into the
Authentik flow; `AUTH_TRUST_HOST=true` lets NextAuth build the correct
per-hostname callback so one login serves both hostnames. The default-deny
NetworkPolicy admits only the Traefik namespace on :7575.

Key env (see `deployment.yaml`):

- `AUTH_PROVIDERS=oidc` — **SSO-only**. The onboarding-era credentials
  break-glass is retired; there is no standing local admin. Admin
  now rides the `homarr-admins` OIDC groups claim: the Terraform-managed
  Authentik `homarr-admins` group maps to the same-named Homarr admin group,
  which is what grants admin. This is also the fix for Homarr's OIDC-only
  admin-assignment sharp edge (homarr-labs#2108 — a pure OIDC-only *first* login
  does not auto-grant admin; the group mapping does). The mapping was verified
  synced before the flip to SSO-only. Break-glass DR for a total Authentik
  outage is documented below.
- `AUTH_OIDC_ISSUER=https://auth.ericsweiss.com/application/o/dashboard/` —
  **trailing slash required** for Authentik discovery. The application slug is
  `dashboard`, so the issuer path is `/application/o/dashboard/`.
- `AUTH_OIDC_SCOPE_OVERWRITE=openid email profile` — **do not** append
  `groups`: Authentik has no scope named `groups`; the groups claim rides
  inside the default `profile` scope mapping (same as Grafana's role mapping).
- `AUTH_OIDC_GROUPS_ATTRIBUTE=groups` — Homarr syncs a user's OIDC `groups`
  claim to same-named Homarr groups. The Terraform-managed Authentik group
  `homarr-admins` (with `eric` as its member) maps to the Homarr group of the
  same name, which is granted admin at onboarding (checklist step 2).

The OIDC client secret and `SECRET_ENCRYPTION_KEY` come from ESO
(`homarr-secrets`, 1Password item `Homarr SSO`). `SECRET_ENCRYPTION_KEY`
encrypts the integration credentials Homarr stores in the SQLite DB — **losing
it makes every stored integration credential unreadable**, so it lives in
1Password and must never exist only in the DB.

Authentik-side objects (OAuth2 provider `Homarr`, application slug `dashboard`,
group `homarr-admins`, policy binding) are Terraform-authored in
[`terraform/authentik/`](../terraform/authentik/) and created by a supervised
apply — see docs/40.

### DR / break-glass (total Authentik outage)

With **no standing local admin**, a total Authentik outage makes Homarr's own
UI inaccessible — acceptable for a non-critical launcher, and the integration
widgets keep polling regardless (they don't depend on the login perimeter). To
regain emergency local access before Authentik returns:

1. Re-enable the `credentials` provider: `task flux:dev-apply` a manifest that
   sets `AUTH_PROVIDERS=oidc,credentials` (reverted on the next reconcile).
2. Run Homarr's CLI admin-recovery in the pod — `kubectl exec -n homarr <pod> --
   homarr-cli recreate-admin --username <name>`. The command requires a
   `--username`, works **only when no credentials admin currently exists**, and
   **aborts if that username already exists** — so choose a `<name>` distinct
   from the OIDC login (`eric`). It provisions a temporary admin group and prints
   a one-time password. See Homarr's
   [CLI reference](https://homarr-labs-homarr.mintlify.app/configuration/cli).

Re-running onboarding is **not** an option: Homarr's onboarding completes once
and cannot be started again, so the CLI (not re-onboarding) is the recovery. The
"no credentials admin currently exists" precondition holds because the
onboarding bootstrap admin is deleted at cutover (checklist step 1). The
recovery mints its own username + one-time password, so the 1P `Homarr SSO`
`admin-username`/`admin-password` fields are **not** consumed by it (they are a
historical onboarding record — see §Secrets). Once Authentik is back, log in via
OIDC and let the reconcile revert to SSO-only.

## Integrations — direct URLs bypass forward-auth

Homarr's integration widgets poll each service's **raw API** on a DIRECT
in-cluster or LAN URL. That is the point: the direct URL bypasses the Authentik
forward-auth perimeter, which would 302 a non-browser API client to a login
page. Every direct URL/port is allowlisted in `networkpolicy.yaml` (a tight
per-host `/32` or per-namespace egress rule).

| Service | Homarr integration | Direct target | Credential |
|---|---|---|---|
| Sonarr | Sonarr | `http://sonarr.downloads.svc.cluster.local:8989` | API key |
| Radarr | Radarr | `http://radarr.downloads.svc.cluster.local:7878` | API key |
| Lidarr | Lidarr | `http://lidarr.downloads.svc.cluster.local:8686` | API key |
| Prowlarr | Prowlarr | `http://prowlarr.downloads.svc.cluster.local:9696` | API key |
| qBittorrent | qBittorrent | `http://qbittorrent.downloads.svc.cluster.local:8080` | Web UI user/pass |
| NZBGet | NZBGet | `http://nzbget.downloads.svc.cluster.local:6789` | user/pass |
| AdGuard Home ×2 | AdGuard Home | `http://10.0.10.150:3000`, `http://10.0.10.160:3000` | user/pass |
| Proxmox | Proxmox | `https://10.0.10.102:8006` (any node) | read-only API token |
| Plex | Plex | `http://10.0.10.152:32400` | token |
| Home Assistant | Home Assistant | `http://10.0.10.154:8123` | long-lived token |
| Nextcloud | Nextcloud | `https://cloud.esweiss.com` | user + app password |
| Immich | Immich | `https://photos.esweiss.com` | API key |

Immich is reached via Traefik on :443 (`https://photos.esweiss.com`), **not**
the direct API port `:2283` — the target firewall (`sg-immich`) does not expose
`:2283` to the cluster, consistent with the removed netpol rule 5e. Its API and
web UI share the one host (same shape as the Nextcloud row).

Reaching the `downloads` namespace also requires the reciprocal ingress rule
`allow-homarr-ingress` in
[`kubernetes/apps/download-clients/networkpolicy.yaml`](../kubernetes/apps/download-clients/networkpolicy.yaml).

### Blast radius / rotation

The SQLite DB aggregates roughly a dozen integration credentials behind the
SSO-bypass perimeter — including a **full-access Home Assistant long-lived
token** and **AdGuard admin credentials**, neither of which is scopeable
upstream (HA long-lived tokens inherit the creator's permissions; AdGuard has no
read-only web/API role). Proxmox is the exception: its token is already
least-privilege (read-only `PVEAuditor`, below).

Reading those credentials back out requires **two separate surfaces**, not one:
the ciphertext SQLite DB on NFS (`/appdata/homarr`) **and** the
`secret-encryption-key` — an ESO/etcd-synced Kubernetes Secret injected as an
env var (`deployment.yaml` / `externalsecret.yaml`, 1P item `Homarr SSO`) that
is **not** stored on the NFS volume. Compromising the DB file alone yields only
ciphertext; decryption needs the key too.

**Rotation** (per credential): re-mint the credential at its source service →
update its 1Password item → re-enter it in the Homarr UI (Manage →
Integrations). There is no ESO-driven rotation for these — they live in the
UI-populated SQLite DB.

> **Egress rule 4 (`0.0.0.0/0:443`) is cosmetic-only.** No integration data path
> needs it — every target is a LAN `/32` or the Traefik VIP `.101` (netpol rules
> 3 and 5); rule 4 serves only remote icon-CDN fetches + version/update checks.
> It cannot be scoped to a static CIDR allowlist (dynamic CDN IPs; this CNI's
> NetworkPolicy has no DNS-name matching), and it is bounded by the default-deny
> ingress (only Traefik → :7575) plus `:443`-only egress. Accepted as-is;
> dropping it (cost: remote icons + the update banner) is a supervised judgment
> call, not part of this behavior-preserving posture.

**Not** supported as native integrations (add as bookmark link tiles):
Mealie (food.esweiss.com), Bar Assistant (bar.esweiss.com),
Grafana (grafana.esweiss.com), GitLab (git.esweiss.com),
Pulsarr (pulsarr.esweiss.com), wg-easy (vpn.esweiss.com),
Hermes (agent.esweiss.com).

## Proxmox read-only API token

Homarr's Proxmox widget needs an API token. Mint a least-privilege **read-only**
token using the built-in `PVEAuditor` role. On any Proxmox node (e.g.
`pve-nas-01`), as root:

```sh
# Optional: a custom, even-tighter audit role. The aclmod below uses the
# built-in PVEAuditor, so this line is not required — keep it only if you
# prefer a hand-scoped role.
pveum role add PVEAuditorRO -privs "Datastore.Audit VM.Audit Sys.Audit Pool.Audit" 2>/dev/null || true

pveum user add homarr@pve
pveum aclmod / -user homarr@pve -role PVEAuditor    # built-in read-only role
# --privsep 0 => the token inherits the user's (read-only) ACL. Prints the
# token-id + secret ONCE — record both in 1Password "Homarr Proxmox Token".
pveum user token add homarr@pve homarr --privsep 0
```

This is a control-plane auth change, kept **out** of Ansible deliberately (a
documented manual step; it could later be codified in a `proxmox_*` role). In
the Homarr Proxmox widget, use `https://10.0.10.102:8006` (any node), the
token-id (e.g. `homarr@pve!homarr`) and the token secret. Leave "ignore
TLS"/self-signed **off**: the PVE cluster CA is trusted process-wide via
`NODE_EXTRA_CA_CERTS`, so `https://<node>:8006` verifies. Toggling
ignore-TLS is an un-codified verification downgrade — invisible to git review —
and must be avoided.

> **Firewall admission note:** homarr's egress to Proxmox `:8006` is admitted
> only because whichever k3s agent the pod is scheduled onto (general +
> modern-CPU, any of `.202`–`.207`) falls inside the `admin_lan` /24 (the
> documented flat-LAN accepted risk), **not** a `k3s_nodes`-scoped rule. This is
> pre-existing (not homarr-introduced); genuine source-scoping is deferred to
> the planned vmbr0 VLAN segmentation (docs/16).

## Secrets / 1Password prerequisites

Create these **before** the Flux reconcile (ESO) and the Terraform apply:

- **`Homarr SSO`** (new): `client-id` (literal `homarr`), `client-secret`
  (`openssl rand -hex 32`), `secret-encryption-key` (`openssl rand -hex 32`).
  ESO consumes `client-secret` + `secret-encryption-key`; terraform/authentik
  consumes `client-secret` (the same field, so the two sides can't drift). Both
  ESO fields must exist before `externalsecret.yaml` merges — a missing field
  fails the whole Secret sync and the pod waits. The item also carries
  `admin-username` + `admin-password` (operator-set, **not** ESO-injected —
  `externalsecret.yaml` only pulls `client-secret` + `secret-encryption-key`).
  These are a **historical record** of the onboarding bootstrap admin, which is
  deleted at the SSO-only cutover (checklist step 1); no current auth path
  consumes them — the break-glass DR mints its own username + one-time password
  via `homarr-cli recreate-admin` (§SSO).
- **`Homarr Proxmox Token`** (new): `token-id`, `token-secret` (from the pveum
  step above). Entered in the Homarr UI, not ESO.
- **`Homarr Integrations`** (new, DR convenience — not ESO-consumed): record the
  newly-minted per-integration creds so they survive a rebuild —
  `sonarr-api-key`, `radarr-api-key`, `lidarr-api-key`, `prowlarr-api-key`,
  `immich-api-key`, `nextcloud-app-password`.
- **Reused** (no change): `NZBGet`, `AdGuard Home`, `Plex Token`,
  `Home Assistant API Token`, `qBittorrent`.

Full item inventory: docs/15 "Required 1Password Items".

## Observability

A blackbox probe target for `https://dashboard.esweiss.com` (module `http_sso`
— Homarr answers an unauthenticated probe with a 302 to its OIDC login, which
`http_sso` accepts) is added to the observability exporters; the generic
`EndpointDown` alert covers it — no new rule. Homarr v1 exposes no Prometheus
`/metrics` endpoint (no ServiceMonitor). Config is env + SQLite, not a watched
ConfigMap (no Reloader annotation). Autoscaling is `vpa.yaml`
(`updateMode: Initial`; docs/33).

---

## Post-deploy UI checklist

Runs after the MR merges, Flux reconciles the app, and the supervised
`terraform/authentik` apply creates the Authentik objects. Prerequisites: the
`Homarr SSO` 1P item exists, the `Homarr Proxmox Token` was minted (pveum step
above), and the NAS `/appdata/homarr` dir + DNS rewrite are deployed.

1. **Onboarding / admin bootstrap** — a pure OIDC-only *first* login does not
   auto-grant admin (homarr-labs#2108), so the initial admin is bootstrapped
   with the `credentials` provider temporarily active: `task flux:dev-apply` a
   manifest with `AUTH_PROVIDERS=oidc,credentials`, browse
   `https://dashboard.esweiss.com`, complete onboarding to create a local admin
   (record its creds in 1P `Homarr SSO` `admin-username`/`admin-password`), then
   do steps 2–3. Once the OIDC admin is verified, **delete the bootstrap
   credentials admin** (Homarr → Manage → Users) and let the reconcile revert to
   the committed SSO-only `AUTH_PROVIDERS=oidc`. Deleting it restores the
   no-standing-local-admin state the rest of this doc assumes, and is the
   precondition the §SSO break-glass DR relies on (`homarr-cli recreate-admin`
   works only when no credentials admin exists). Thereafter, DR for a total
   Authentik outage is the §SSO procedure, not a re-run of onboarding (which
   completes once).
2. **Admin group** — Homarr → Manage → Groups → create a group named exactly
   **`homarr-admins`** and grant it admin permission. (Terraform already created
   the matching Authentik `homarr-admins` group with you as a member.)
3. **Verify SSO** — log out, click **"Sign in with Authentik"** → you should
   land back in Homarr as an admin (group synced from the `groups` claim). If
   not admin, confirm the Authentik group name matches and re-login.
4. **Add integrations** — Manage → Integrations → New; pick the type, paste the
   direct URL + credential (from the table above):
   - Sonarr / Radarr / Lidarr / Prowlarr → the `*.downloads.svc` URL + each
     app's API key (app → Settings → General → API Key). Record each key in 1P
     `Homarr Integrations` for DR.
   - qBittorrent → `http://qbittorrent.downloads.svc.cluster.local:8080` + Web
     UI user/pass.
   - NZBGet → `http://nzbget.downloads.svc.cluster.local:6789` + 1P `NZBGet`
     user/pass.
   - AdGuard Home ×2 → `http://10.0.10.150:3000` / `http://10.0.10.160:3000` + 1P
     `AdGuard Home` user/pass.
   - Proxmox → `https://10.0.10.102:8006` + 1P `Homarr Proxmox Token`
     token-id/secret. Leave "ignore TLS" **off** — the PVE cluster CA is trusted
     process-wide via `NODE_EXTRA_CA_CERTS`, so the cert verifies;
     toggling it is an un-codified verification downgrade to avoid.
   - Plex → `http://10.0.10.152:32400` + 1P `Plex Token`.
   - Home Assistant → `http://10.0.10.154:8123` + 1P `Home Assistant API
     Token`.
   - Nextcloud → `https://cloud.esweiss.com` + your Nextcloud username + a new
     app password (Nextcloud → Settings → Security → Create new app password) →
     record in 1P `Homarr Integrations`.
   - Immich → `https://photos.esweiss.com` + a new API key (Immich → Account
     Settings → API Keys) → record in 1P `Homarr Integrations`.
5. **Bookmark tiles** — add link tiles (with dashboard-icons) for the
   unsupported-as-integration apps: Mealie, Bar Assistant, Grafana, GitLab,
   Pulsarr, wg-easy, Hermes. For dual-host apps use the `ericsweiss.com` URL
   as the tile link so it works from outside the LAN too (each app's own
   Authentik gate handles auth); `esweiss.com`-only apps simply won't resolve
   externally — expected, they are internal-only by design.
6. **Confirm** each integration widget shows live data (a bad URL/cred surfaces
   as a widget error). If a LAN integration can't connect, re-check the matching
   `ipBlock` in
   [`kubernetes/apps/homarr/networkpolicy.yaml`](../kubernetes/apps/homarr/networkpolicy.yaml).

## Related documentation

- [docs/29-flux-operations.md](29-flux-operations.md) - how the manifests reconcile
- [docs/31-observability.md](31-observability.md) - metrics and alerts
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the OIDC provider and the two access groups as code
- [docs/15-credential-rotation.md](15-credential-rotation.md) - the UI-entered integration credentials of record
