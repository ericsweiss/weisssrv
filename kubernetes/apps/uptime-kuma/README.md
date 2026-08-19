# uptime-kuma

[Uptime Kuma](https://github.com/louislam/uptime-kuma) — endpoint monitoring and
the public status page, running in the `uptime-kuma` namespace. Raw manifests
(Deployment + Service + IngressRoutes + Certificate + NetworkPolicy + VPA), not
a Helm chart — upstream publishes none, and the whole app is one container plus
a SQLite DB populated through the UI.

- **URLs**: `status.ericsweiss.com` (external, **public status page only**) /
  `status.esweiss.com` (internal — status page plus the admin UI,
  LAN/Tailscale-scoped).
- **Routing split** (`ingress-routes.yaml`): the read-only status-page paths
  (`/status/*`, `/api/status-page/*`, `/api/entry-page`, the SPA bundle,
  `/upload/*`, `/`) are served unauthenticated on both hostnames; everything
  else — dashboard, `/socket.io`, `/metrics` — exists only on the internal
  hostname behind `lan-tailscale-only` + the `authentik-auth` forward-auth
  middleware. The external hostname has **no catch-all**, so
  `https://status.ericsweiss.com/dashboard` is a Traefik 404. Kuma has no OIDC
  support, so forward-auth is its identity gate (same posture as the Traefik
  dashboard); its own single local account sits underneath it.
- **Image**: `louislam/uptime-kuma:<version>-rootless` (public) — version pinned
  via `uptime_kuma_version` in
  `ansible/inventories/prod/group_vars/all.yml`. The `-rootless` suffix is
  load-bearing: it is what lets the namespace enforce PSA `restricted`.
- **Storage**: NFS `/appdata/uptime-kuma` (encrypted `ssd/appdata`,
  archive-backed). The SQLite DB rides this RWO volume; `replicas: 1` +
  `strategy: Recreate` guarantee a single writer. Fallback if WAL/locking ever
  bites: a dedicated ext4-on-zvol PV (docs/45).
- **Secrets**: none in this directory. Kuma's single admin account is created at
  first login from the credentials recorded in the 1Password item
  `Uptime Kuma`; the same pair is ESO-synced into the **observability**
  namespace (`observability-exporter-secrets`) for the `/metrics` scrape.
- **Observability**: `/metrics` (Kuma's built-in Prometheus endpoint, HTTP Basic
  against the admin credentials) is scraped by
  `kubernetes/infrastructure/observability/service-monitors/uptime-kuma.yaml`;
  `UptimeKumaDown` alerts on the Deployment losing its replica, and both
  hostnames carry blackbox probe targets.
- **Egress is the monitor inventory** (`networkpolicy.yaml`): public :443, the
  Traefik namespace + both MetalLB VIPs on :443, the two resolvers on :53, the
  SMTP relay on :587, the six Proxmox APIs on :8006 and the k3s API VIP on
  :6443. A monitor whose target is not listed fails as a timeout — extend the
  policy in the same change. ICMP ("ping") monitors are unsupported: the pod
  drops `NET_RAW`.

Full architecture, the monitor/notification design, and the post-deploy UI
checklist: **[`docs/45-uptime-kuma.md`](../../../docs/45-uptime-kuma.md)**.
