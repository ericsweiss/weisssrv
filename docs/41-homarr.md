# 41 — Homarr Dashboard

[Homarr](https://github.com/homarr-labs/homarr) is a self-hosted homelab
dashboard / launcher: a board of live-status widgets (integrations that poll
each service's API) plus bookmark tiles. This deployment runs Homarr as a k3s
workload in the `homarr` namespace and exposes it at
**`dashboard.ericsweiss.com`** (external) / **`dashboard.esweiss.com`**
(internal), gated by Authentik OIDC.

- Namespace: `homarr`
- Manifests: [`kubernetes/apps/homarr/`](../kubernetes/apps/homarr/)
- Image: `ghcr.io/homarr-labs/homarr` (public; v-prefixed docker tag, e.g.
  `v1.71.0`), pinned via `homarr_version` in
  `ansible/inventories/prod/group_vars/all.yml`
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
NAS-avoiding + modern-CPU (state is on NFS, not a node-local zvol). Runs fully
non-root (`runAsUser: 1000` / `runAsGroup: 2000`) — it is a plain Next.js
standalone image with no s6 root-drop init, so the restricted-style
securityContext (drop-ALL, seccomp RuntimeDefault, no-privilege-escalation)
applies cleanly; the namespace PSS enforces `baseline` (warn/audit `restricted`)
because the upstream image is not guaranteed to start under a strict
`restricted` enforce.

## Storage — NFS SQLite (single-writer)

All state lives under a single writable mount at `/appdata` (the SQLite DB is
`/appdata/db/db.sqlite`), backed by NFS `/appdata/homarr` on the encrypted
`ssd/appdata` dataset — captured by the nightly `ssd/appdata -> archive`
raw-ZFS replication (docs/06). The NFS export `all_squash`es every write to
`1000:2000`; the `/appdata/homarr` subdir is created + owned `1000:2000` by the
`nas_storage` role (`nas_appdata_dirs`).

SQLite tolerates exactly **one writer**. `replicas: 1` + `strategy: Recreate`
guarantee no two pods ever dual-mount the RWO PV, which is what makes
SQLite-over-NFS safe here (the same pattern the Hindsight app runs on this
export). The volume is hard-mounted NFSv4.2 with `xprtsec=tls` by **hostname**
(`pve-nas-01.esweiss.com`) — an IP mount fails the `*.esweiss.com` cert
handshake (docs/07). **Fallback** if WAL/locking issues ever appear: move state
to a dedicated ext4-on-zvol PV (NAS-pinned + nodeAffinity, like the Postgres
apps). Not implemented now.

## SSO — Authentik OIDC (+ break-glass local admin)

Homarr runs its **own** Authentik OIDC login — there is **no** Traefik
forward-auth middleware on the IngressRoutes (same posture as Hermes). Traefik
passes straight to Homarr, which 302s an unauthenticated browser into the
Authentik flow; `AUTH_TRUST_HOST=true` lets NextAuth build the correct
per-hostname callback so one login serves both hostnames. The default-deny
NetworkPolicy admits only the Traefik namespace on :7575.

Key env (see `deployment.yaml`):

- `AUTH_PROVIDERS=oidc,credentials` — OIDC (Authentik) **plus** a local
  credentials provider. The credentials provider is deliberate: it creates a
  local admin at first-run onboarding (break-glass access when Authentik is
  down) and sidesteps Homarr's OIDC-only admin-assignment sharp edge
  (homarr-labs#2108 — a pure OIDC-only first login does not auto-grant admin).
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
| AdGuard Home ×2 | AdGuard Home | `http://192.168.0.150:3000`, `http://192.168.0.160:3000` | user/pass |
| Proxmox | Proxmox | `https://192.168.0.102:8006` (any node) | read-only API token |
| Plex | Plex | `http://192.168.0.152:32400` | token |
| Home Assistant | Home Assistant | `http://192.168.0.154:8123` | long-lived token |
| Nextcloud | Nextcloud | `https://cloud.esweiss.com` | user + app password |
| Immich | Immich | `http://192.168.0.157:2283` | API key |

Reaching the `downloads` namespace also requires the reciprocal ingress rule
`allow-homarr-ingress` in
[`kubernetes/apps/download-clients/networkpolicy.yaml`](../kubernetes/apps/download-clients/networkpolicy.yaml).

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
the Homarr Proxmox widget, use `https://192.168.0.102:8006` (any node), the
token-id (e.g. `homarr@pve!homarr`) and the token secret; enable "ignore
TLS"/self-signed if the widget requires it for the PVE cert.

## Secrets / 1Password prerequisites

Create these **before** the Flux reconcile (ESO) and the Terraform apply:

- **`Homarr SSO`** (new): `client-id` (literal `homarr`), `client-secret`
  (`openssl rand -hex 32`), `secret-encryption-key` (`openssl rand -hex 32`),
  and `admin-username` + `admin-password` for the break-glass local admin
  created at onboarding (operator-set, not injected). ESO consumes
  `client-secret` + `secret-encryption-key`; terraform/authentik consumes
  `client-secret` (the same field, so the two sides can't drift). Both ESO
  fields must exist before `externalsecret.yaml` merges — a missing field fails
  the whole Secret sync and the pod waits.
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

1. **Onboarding / admin** — browse `https://dashboard.esweiss.com`. Complete
   onboarding → create the local admin (store its username/password in 1P
   `Homarr SSO` `admin-username`/`admin-password`). This local admin is the
   break-glass path if Authentik is down.
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
   - AdGuard Home ×2 → `http://192.168.0.150:3000` / `:160:3000` + 1P
     `AdGuard Home` user/pass.
   - Proxmox → `https://192.168.0.102:8006` + 1P `Homarr Proxmox Token`
     token-id/secret (enable "ignore TLS" if the widget needs it for the PVE
     cert).
   - Plex → `http://192.168.0.152:32400` + 1P `Plex Token`.
   - Home Assistant → `http://192.168.0.154:8123` + 1P `Home Assistant API
     Token`.
   - Nextcloud → `https://cloud.esweiss.com` + your Nextcloud username + a new
     app password (Nextcloud → Settings → Security → Create new app password) →
     record in 1P `Homarr Integrations`.
   - Immich → `http://192.168.0.157:2283` + a new API key (Immich → Account
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
