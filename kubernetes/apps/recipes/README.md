# recipes

Two self-hosted apps in the `recipes` namespace, both behind Authentik OIDC:
**Mealie** (recipes/meal planning, `food.esweiss.com` / `food.ericsweiss.com`)
and **Bar Assistant** + its Salt Rim frontend (`bar.esweiss.com` /
`bar.ericsweiss.com`). Deployment guide:
**[`docs/22-recipes-deployment.md`](../../../docs/22-recipes-deployment.md)**;
SSO/provider values: **[`docs/23-recipes-sso-setup.md`](../../../docs/23-recipes-sso-setup.md)**.

- **Workloads**: `mealie.yaml` (Mealie + its PostgreSQL, which carries a
  postgres_exporter sidecar) and `bar-assistant.yaml` (API, Redis, Meilisearch,
  Salt Rim). Both apps disable password login — Authentik is the only path in.
- **Storage** (`storage.yaml`): app configs on NFS `/appdata/{mealie,bar-assistant}`;
  the Mealie PostgreSQL on a dedicated ZFS zvol pinned to `k3s-agt-nas-01`; the
  pg-dump landing zone on NFS `/backups-apps/mealie`.
- **Backup**: `pg-dump.yaml` dumps the Mealie DB nightly to that landing zone,
  which rides the archive + restic B2 chain (docs/42). `MealieBackupStale`
  alerts on staleness.
- **Autoscaling**: `hpa.yaml` (Salt Rim, min=2 HA floor) + `vpa.yaml`. Per the
  repo invariant, any HPA'd workload's VPA is memory-only (docs/33).
- **Network**: `networkpolicy.yaml` is default-deny egress with per-app allows;
  the Authentik backend path resolves to the internal Traefik VIP, so the
  traefik namespaceSelector is the load-bearing peer.
- **Terraform**: the Authentik applications, providers and group bindings for
  both apps live in `terraform/authentik` (docs/40), not in the UI.
