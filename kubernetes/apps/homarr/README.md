# homarr

[Homarr](https://github.com/homarr-labs/homarr) — the homelab dashboard /
launcher, running in the `homarr` namespace. Raw manifests (Deployment +
Service + IngressRoutes + Certificate + ExternalSecret + NetworkPolicy + VPA),
not the Helm chart — Homarr's runtime config is env + the SQLite DB populated
through the UI, so a chart adds nothing over these.

- **URLs**: `dashboard.ericsweiss.com` (external) / `dashboard.esweiss.com`
  (internal, LAN/Tailscale-scoped).
- **SSO**: Homarr's own Authentik OIDC login (`AUTH_PROVIDERS=oidc`, SSO-only)
  — no forward-auth middleware; Traefik passes straight through and Homarr 302s
  unauthenticated browsers into Authentik (one login, both hostnames). The
  `homarr-admins` Authentik group (matched from the `groups` claim) maps to the
  same-named Homarr admin group — that grants admin. There is **no standing
  local admin** (the onboarding credentials break-glass was retired in !193). DR
  for a total Authentik outage: re-enable `credentials` via `task flux:dev-apply`
  + `homarr-cli recreate-admin` (see docs/41 §SSO). Authentik objects live in
  `terraform/authentik` (docs/40).
- **Image**: `ghcr.io/homarr-labs/homarr` (public, v-prefixed tag) — version
  pinned via `homarr_version` in
  `ansible/inventories/prod/group_vars/all.yml`.
- **Storage**: NFS `/appdata/homarr` (encrypted `ssd/appdata`, archive-backed).
  The SQLite DB rides this RWO volume; `replicas: 1` + `strategy: Recreate`
  guarantee a single writer (SQLite-on-NFS is safe only single-writer). Fallback
  if WAL/locking ever bites: a dedicated ext4-on-zvol PV (docs/41).
- **Secrets**: `homarr-secrets` (OIDC client secret + `SECRET_ENCRYPTION_KEY`)
  via ESO / 1Password (`Homarr SSO` item). The encryption key protects the
  integration credentials stored in the DB — **do not lose it**.
- **Integrations** (widgets poll each service's raw API on a DIRECT
  in-cluster/LAN URL, bypassing the Authentik forward-auth perimeter):
  Sonarr/Radarr/Lidarr/Prowlarr/qBittorrent/NZBGet (`*.downloads.svc`), the two
  AdGuard Home instances, Proxmox (read-only `PVEAuditor` token), Plex, Home
  Assistant, Nextcloud and Immich. Each direct URL/port is allowlisted in
  `networkpolicy.yaml`. Mealie/Bar Assistant/Grafana/Prometheus/GitLab/Pulsarr/
  wg-easy/Hermes are unsupported as native integrations and added as bookmark
  link tiles.

Full architecture, the Proxmox read-only token recipe, and the post-deploy UI
checklist (onboarding + integration wiring): **[`docs/41-homarr.md`](../../../docs/41-homarr.md)**.
