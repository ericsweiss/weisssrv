# Uptime Kuma

[Uptime Kuma](https://github.com/louislam/uptime-kuma) is a self-hosted uptime
monitor: a set of scheduled probes (HTTP, TCP port, DNS, …) with its own
notification channels, plus publishable **status pages**. This deployment runs
Kuma as a k3s workload in the `uptime-kuma` namespace and serves the public
status page at **`status.ericsweiss.com`** (external) while the admin dashboard
lives only at **`status.esweiss.com`** (internal), behind Authentik
forward-auth.

- Namespace: `uptime-kuma`
- Manifests: [`kubernetes/apps/uptime-kuma/`](../kubernetes/apps/uptime-kuma/)
- Image: `louislam/uptime-kuma` (public, Docker Hub), the **`-rootless`**
  variant, pinned via `uptime_kuma_version` in
  `ansible/inventories/prod/group_vars/all.yml`
- Authentik objects: [`terraform/authentik/`](../terraform/authentik/) (docs/40)

**Why it exists alongside Prometheus.** The observability stack already alerts
on everything in the cluster, so Kuma is not a second Prometheus. It is here for
the two things Prometheus deliberately does not do: a **status page** that can
be handed to a household member during an outage, and an **independent** view of
endpoint reachability whose alerting path (Kuma's own notification channels)
does not run through Alertmanager. The overlap with the blackbox exporter is
intentional — a probe that agrees with blackbox costs nothing, and one that
disagrees is the interesting signal.

---

## Deploy model

Raw manifests reconciled by Flux — there is no maintained upstream Helm chart,
and the app is one container plus a SQLite DB anyway: `namespace` + `storage`
(NFS PV/PVC) + `deployment` + `service` + `ingress-routes` + `certificate` +
`networkpolicy` + `vpa`, with `default-deny-ingress` from the shared
`netpol-baseline` component. There is **no `externalsecret.yaml`** — nothing in
the pod needs a secret (see § Secrets).

One Deployment, `replicas: 1`, `strategy: Recreate`, one container on :3001.
NAS-avoiding + modern-CPU (state is on NFS, not a node-local zvol).

The image is the upstream **`-rootless`** tag: the same release built with
`USER node` (UID 1000). That is what lets the namespace enforce Pod Security
`restricted` rather than `baseline` — the pod declares `runAsNonRoot`,
`runAsUser: 1000`, drops **ALL** capabilities, `allowPrivilegeEscalation: false`
and seccomp `RuntimeDefault`. Keep the suffix when bumping the pin: the bare tag
runs as root and is rejected at admission.

## Routing split — public status page vs. admin UI

This is the load-bearing design decision and it is enforced by
[`ingress-routes.yaml`](../kubernetes/apps/uptime-kuma/ingress-routes.yaml), not
by anything inside Kuma. Kuma has **no SSO support at all** (one local account,
optional TOTP), so Traefik + the Authentik forward-auth middleware *is* its
identity gate — the same posture as the Traefik dashboard.

The split is by authentication, not by hostname:

| Surface | Paths | External `status.ericsweiss.com` | Internal `status.esweiss.com` |
|---|---|---|---|
| Public status page (`services` slug ONLY, external) | external: `/`, `/status/services[/…]`, `/api/status-page/services[/…]`, `/api/status-page/heartbeat/services`, `/api/entry-page`, `/assets/…`, `/upload/…`, `/favicon.ico`, `/icon.svg`, `/manifest.json`, `/apple-touch-icon.png`, `/robots.txt`; internal: the same shape but with `/status[/…]` + `/api/status-page[/…]` prefix-wide (every slug) — all prefixes segment-boundary-safe | served, unauthenticated, **slug-fenced** | served, `lan-tailscale-only`, every slug |
| Admin (dashboard, `/socket.io`, `/metrics`, everything else) | the catch-all | **no router — Traefik 404** | `lan-tailscale-only` + `authentik-auth` |

### Status pages

Two pages, each **domain-mapped** so `/` on its host renders it directly (no
redirect):

| Page slug | Domain-mapped host | Shows |
|---|---|---|
| `services` | `status.ericsweiss.com` | the public services (their `*.ericsweiss.com` endpoints, so the page reflects the real Cloudflare → VIP public path) |
| `internal` | `status.esweiss.com` | everything — the public group plus the private services (Grafana, the downloads stack, wg-easy admin) on their internal endpoints |

Page slugs are unauthenticated **by construction** in Kuma, so the privacy of
`internal` is enforced by the ingress, not the app: the external route's
allowlist names the `services` paths explicitly rather than a `/status/`
prefix. A new status page is therefore external-invisible until its slug is
added to the external route — the safe default. The `Entry Page` setting
(`services`) is only the fallback for a host that is not domain-mapped.

Consequences worth stating plainly:

- `https://status.ericsweiss.com/dashboard` is a 404 from Traefik, not a login
  prompt. The admin UI is never published to the internet, so the only
  internet-facing surface is a read-only page.
- Kuma logs in over **socket.io**, which lives on the admin catch-all. That is
  why no admin surface leaks externally: without `/socket.io` there is no login
  path even if the SPA shell were fetched.
- `/api/badge/<id>/*` is deliberately **not** in the public allowlist. Badges
  answer for any monitor id, including monitors that are on no status page, so
  publishing them would leak the monitor inventory. Add the prefix here (both
  routes) if badges are ever wanted, knowing that trade.
- The path list was taken from `server/routers/status-page-router.js` and
  `server/routers/api-router.js` at the pinned release. An upstream release that
  adds a status-page endpoint needs the allowlist extended, or the page renders
  with a missing panel.

The internal route pair is ordered by explicit `priority` (status 100, admin 10)
rather than relying on Traefik's implicit rule-length ordering.

### Authentik objects

Terraform-authored in [`terraform/authentik/`](../terraform/authentik/)
(docs/40), created by a supervised apply:

| Object | Value |
|---|---|
| Proxy provider | `uptime_kuma` — `forward_single`, external host `https://status.esweiss.com`, no basic-auth injection |
| Application | slug `status`, name "Uptime Kuma", library group "Software" |
| Group | `status-admins` |
| Policy binding | `status` → `status-admins` |

Only the internal host is gated, because it is the only host with an admin path
to gate — `forward_single` matches exactly one external host, and a second
provider for `status.ericsweiss.com` would exist to protect a router that does
not exist.

Kuma's own single-account login stays enabled underneath the outpost: two
independent factors on the admin surface, and the local login is what still
works if a Kuma CLI recovery is ever needed.

## Storage — NFS SQLite (single-writer)

All state lives under a single writable mount at `/app/data` (the DB is
`/app/data/kuma.db`, uploads are `/app/data/upload`), backed by NFS
`/appdata/uptime-kuma` on the encrypted `ssd/appdata` dataset — captured by the
nightly `ssd/appdata -> archive` raw-ZFS replication (docs/06). The NFS export
`all_squash`es every write to `1000:2000`; the `/appdata/uptime-kuma` subdir is
created and owned `1000:2000` by the `nas_storage` role
(`nas_storage_appdata_dirs` in `host_vars/pve-nas-01.yml`), and the container
runs as UID 1000 so it reads back what it wrote.

SQLite tolerates exactly **one writer**. `replicas: 1` + `strategy: Recreate`
guarantee no two pods ever dual-mount the RWO PV — the same pattern Homarr
(docs/41) and Hindsight run on this export. Kuma is the busiest writer of the
three: one heartbeat row per monitor per interval, so ~0.5 writes/s at 30
monitors on 60s intervals. That is well inside what NFSv4.2 locking handles, but
it is the workload most likely to expose a problem. **Fallback** if
WAL/locking issues ever appear: move state to a dedicated ext4-on-zvol PV
(NAS-pinned + nodeAffinity, like the Postgres apps). Not implemented now.

Retention is a Kuma setting (Settings → Monitor History), and it is what governs
DB growth — the 4Gi PV assumes the default keep-window, not "keep forever".

## Monitor types and what the pod may reach

Probing is this app's data path, so
[`networkpolicy.yaml`](../kubernetes/apps/uptime-kuma/networkpolicy.yaml) is
effectively the monitor inventory. **A monitor whose target is not allowlisted
fails as a timeout, not as a policy error** — extend the policy in the same
change that adds the monitor.

| Rule | Target | For |
|---|---|---|
| 1 | kube-dns :53 | every monitor resolves first |
| 2 | `0.0.0.0/0:443` minus the reserved except-list | the external endpoints, third-party dependencies, and outbound webhook notifications |
| 3 | `traefik` namespace + VIPs `.100`/`.101` on :443 | every in-cluster and VM-hosted service, monitored on its real user-facing URL |
| 4a | `.150`/`.160` :53 (UDP+TCP) | DNS-type monitors against both resolvers |
| 4b | `.151:587` | the submission listener, and the port Kuma's email notifications leave through |
| 4c | `.102`–`.107` :8006 | Proxmox API liveness (TCP-port monitors) |
| 4d | `.161:6443` | the kube-apiserver VIP (TCP-port monitor) |

Two constraints follow from the manifests rather than from Kuma:

- **No ICMP.** "Ping" monitors need `CAP_NET_RAW`, which the pod drops, and
  NetworkPolicy cannot express ICMP anyway. Use a TCP-port or HTTP monitor
  instead — for a host with no useful listener, `:22`, once added to the policy.
- **No `:8006` HTTPS monitors.** The Proxmox API presents the PVE cluster CA,
  which this pod does not trust; the honest options are a TCP-port monitor
  (chosen) or mounting the CA the way Homarr does. Toggling Kuma's "Ignore
  TLS/SSL error" is an un-codified verification downgrade — invisible to git
  review — and must be avoided, exactly as in docs/41.
- Rule 2 is `:443` only. A monitor that must check a plaintext `:80` redirect
  needs a deliberate edit.

The internal endpoints are monitored through Traefik (`https://<app>.esweiss.com`)
rather than on backend ports, deliberately: that is the path a user takes, so it
covers the router, the certificate and the backend in one probe. Anything behind
Authentik forward-auth answers `302` to a non-browser client — set those
monitors to accept `200-299, 302` (Kuma's "Accepted Status Codes"), which is the
same trade the blackbox `http_sso` module makes.

## Observability

- **Metrics.** Kuma has a built-in `/metrics` endpoint
  (`monitor_status`, `monitor_response_time`, `monitor_cert_days_remaining`,
  plus process metrics) and it is scraped —
  [`service-monitors/uptime-kuma.yaml`](../kubernetes/infrastructure/observability/service-monitors/uptime-kuma.yaml).
  The endpoint is protected by **HTTP Basic against the single admin account**:
  upstream wires it as `app.get("/metrics", apiAuth, …)`, and `apiAuth` falls
  back to the user authorizer while the "API Keys" feature is off. So the scrape
  credentials *are* the admin credentials, which is why they are operator-chosen
  and recorded in 1Password **before** deploy (§ Secrets) rather than minted in
  the UI afterwards. Two traps:
  - enabling **API Keys** in Kuma's UI switches `/metrics` to API-key-only and
    the scrape starts 401ing — rotate the 1P field to the key in the same
    window;
  - a changed admin password must be mirrored into 1P, or the target goes down
    while the app is perfectly healthy — and each failed scrape spends a token
    from the *shared* login rate limiter, the same bucket the operator's own
    login draws from, so a stale credential eventually locks the human out too.
- **Alert.** `UptimeKumaDown` (`homelab.infrastructure`, warning, 15m) on the
  Deployment losing its available replica, with the same
  `or absent(...)` shape as `WgEasyDown`/`HindsightDown` and a promtool unit
  test in `scripts/prometheus-rule-tests/availability.test.yaml`. Warning, not
  critical: Prometheus is what evaluates the rule, so the primary alerting path
  is by definition still up — what is lost is the second opinion and the status
  page.
- **Blackbox probes.** Three targets (`uptime-kuma`, `uptime-kuma-admin`,
  `uptime-kuma-external`), all `http_sso`, feeding the generic `EndpointDown`
  alert. They are three because they are three distinct Traefik routers: the
  internal public route, the internal forward-auth route, and the external
  public route. `http_sso` (not `http_2xx`) even for the public page, because
  `/` answers `302` to the entry status page.
- **Dashboard**: none. Kuma's own UI is the dashboard for this data; a Grafana
  panel would restate it.
- Config is env + SQLite, not a watched ConfigMap, so there is no Reloader
  annotation. Autoscaling is `vpa.yaml` (`updateMode: Initial`, `RequestsOnly`;
  docs/33).

## Secrets / 1Password prerequisites

Create this **before** the MR merges:

- **`Uptime Kuma`** (new): `admin-username`, `admin-password` — operator-chosen
  (e.g. `openssl rand -base64 24` for the password). They are the credentials
  entered in Kuma's first-run setup form, and the same pair ESO syncs into the
  observability namespace (`observability-exporter-secrets` →
  `uptime-kuma-username` / `uptime-kuma-password`) for the `/metrics` scrape.

Both fields must exist before the reconcile: they join the **shared**
`observability-exporter-secrets` ExternalSecret, and a missing property fails
that whole Secret sync — taking every other exporter credential with it.

Nothing else is needed. The app pod itself holds no secret (`uptime-kuma` is
deliberately absent from the `ClusterSecretStore` namespace list in
`kubernetes/infrastructure/configs/cluster-secret-store.yaml`), and notification
channel credentials are entered in the UI and live in the SQLite DB.

Full item inventory: docs/15 "Required 1Password Items".

---

## Post-deploy UI checklist

Runs after the MR merges, Flux reconciles the app, and the supervised
`terraform/authentik` apply creates the Authentik objects. Prerequisites: the
`Uptime Kuma` 1P item exists, and the NAS `/appdata/uptime-kuma` dir + the
`status.esweiss.com` DNS rewrite are deployed (`task storage:deploy`,
`task dns:deploy`).

1. **First-run setup** — browse `https://status.esweiss.com/dashboard`, pass the
   Authentik gate, and complete Kuma's setup form using **exactly** the
   `admin-username` / `admin-password` from 1P. Getting this wrong is what makes
   the `/metrics` target sit down while the app looks healthy.
2. **Settings → General** — set the timezone, enable **Trust Proxy** (Kuma then
   honours Traefik's `X-Forwarded-*`, which is what makes the entry-page
   hostname logic and the client IPs correct), and set **Monitor History**
   retention deliberately (it governs PV growth).
3. **Create the status pages** — Status Pages → New, per the § Status pages
   table: `services` (public monitors, Domain Names `status.ericsweiss.com`)
   and `internal` (all monitors, Domain Names `status.esweiss.com`). Domain
   mapping is what makes `/` render each page directly. A page with any OTHER
   slug is not reachable externally until the ingress allowlist names it.
4. **Set the entry page** — Settings → General → Entry Page → `services`
   (the fallback for non-domain-mapped hosts; the two `uptime-kuma*` blackbox
   probes stay RED until `/` lands on a 200, which domain mapping satisfies).
5. **Add monitors** — keep each one inside the egress allowlist (§ Monitor
   types). A reasonable starting set:
   - external HTTPS: `https://home.ericsweiss.com`, `git.`, `cloud.`, `photos.`,
     `agent.`, `dashboard.`, `food.`, `bar.`, `plex.`, `auth.` — the same list
     the blackbox exporter probes externally, so a disagreement between the two
     is meaningful;
   - internal HTTPS through Traefik: `https://grafana.esweiss.com`,
     `auth.esweiss.com`, `traefik.esweiss.com` (accept `302` for the
     forward-auth-fronted ones);
   - DNS: `esweiss.com` A against `192.168.0.150` and `192.168.0.160`;
   - TCP port: `192.168.0.151:587`, `192.168.0.161:6443`, and
     `192.168.0.102–107:8006`.
6. **Notifications** — Settings → Notifications. Email goes through the relay at
   `192.168.0.151:587` (rule 4b); a Discord webhook rides the public `:443`
   rule. Record any channel credential in 1P. Attach the channel to the monitors
   (Kuma does not do it retroactively).
7. **Verify the perimeter**, from off-LAN or with the tailnet down:
   - `https://status.ericsweiss.com/` renders the status page with no login;
   - `https://status.ericsweiss.com/dashboard` returns **404** (not a login
     page, not the SPA);
   - `https://status.esweiss.com/dashboard` redirects to Authentik and, after
     login, into Kuma's own login form.
8. **Verify the scrape** — the `uptime-kuma` target is UP in Prometheus and
   `monitor_status` has a series per monitor.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/metrics` target down, app healthy | 1P `admin-username`/`admin-password` do not match the account created in step 1, or API Keys were enabled in the UI (§ Observability) |
| A monitor times out while the endpoint is fine from a laptop | its target is outside the egress allowlist — add it to `networkpolicy.yaml` |
| Status page renders but a panel is empty | an upstream release added a status-page endpoint the ingress allowlist does not carry (§ Routing split) |
| `/` externally shows the login SPA instead of the status page | step 3/4 not done: no domain mapping and no entry page, so Kuma falls back to `302 /dashboard`, which has no external router |
| Pod rejected at admission | the image pin lost its `-rootless` suffix; the bare tag runs as root and the namespace enforces PSA `restricted` |
| DB errors after a node failure | SQLite-on-NFS single-writer assumption broken — confirm `replicas: 1` and `strategy: Recreate`, then consider the zvol fallback (§ Storage) |

## Related documentation

- [docs/29-flux-operations.md](29-flux-operations.md) - how the manifests reconcile
- [docs/31-observability.md](31-observability.md) - metrics, probes and alert routing
- [docs/40-authentik-terraform.md](40-authentik-terraform.md) - the proxy provider and access group as code
- [docs/41-homarr.md](41-homarr.md) - the sibling raw-manifest app this one is shaped after
- [docs/15-credential-rotation.md](15-credential-rotation.md) - the 1Password item inventory
