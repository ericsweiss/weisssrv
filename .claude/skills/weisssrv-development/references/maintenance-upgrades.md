# Maintenance & upgrades

Canonical workflow: `docs/12-runbooks.md` (update workflow). Roadmap /
outstanding decisions: `docs/16-next-steps.md`. All version pins are
single-sourced in `ansible/inventories/prod/group_vars/all.yml` (Home Assistant
OS is the one exception — updated manually via its UI).

## Version-bump flow

1. **Discover** — `task maintenance:check-versions` (or `:check-versions-json`,
   `-- --service <x>`, `-- --category helm`). The task already injects
   `GITHUB_TOKEN` via `op run`; you only need to set it by hand if you invoke
   `scripts/check-versions.py` directly. When deciding what is current, trust the
   **pipeline's**
   `version-check` job output, not a local run — the local cache can be stale
   (this is why bumps ride with an MR revision instead of getting their own).
2. **Edit** — `task maintenance:update-version SERVICE=<x>` or
   `maintenance:update-all-versions` (edits `all.yml`, no deploy). Review
   `git diff` on `all.yml`.
3. **Sync** — `task flux:sync-versions`, then commit **both** `all.yml` and
   `kubernetes/infrastructure/sources/versions-configmap.yaml`. Never hand-edit
   the ConfigMap.
4. **Deploy** — k8s pins reconcile via Flux on merge (~1 min). Base-infra binary
   pins deploy via `task maintenance:update-full`; k3s node binaries via
   `task maintenance:update-k3s-nodes` (rolling cordon → upgrade → uncordon;
   kernel reboots go through kured).
5. **Verify** — `task flux:status`, `task infra:verify`, `task k3s:status`.

Fold every available bump into the current MR revision; do not open a revision
just to bump versions.

## Node / cluster upgrades & reboots

- `task maintenance:update-packages` / `:update-applications` / `:update-full` /
  `:update-full-auto` — base-infra package + app updates.
- `task maintenance:update-k3s-nodes` — rolling k3s binary upgrade.
- `task maintenance:update-cluster` — the 4-phase cluster update.
- Reboots are coordinated by **kured**; the maintenance path gates on a
  reboot-safety check (`docs/12-runbooks.md`, `docs/32-zfs-encryption.md` for NAS unlock).

## Known holds & traps

- **MetalLB is held below the 0.16.x line** — 0.16.x has an apiserver-flood
  regression (upstream `metallb#3063`); unhold only once the fix (`metallb#3079`)
  merges and ships. The exact held pin lives in `all.yml`; check both upstream
  issues before bumping.
- **Helm CRD lifecycle** — a chart with `crds.enabled: false` can DELETE existing
  CRDs across an up/downgrade (this took out MetalLB VIPs once). Verify a chart's
  CRD handling before changing its version or that flag.
- **Reloader** watches ConfigMaps only (`ignoreSecrets: true`); a credential
  Secret rotation is a manual pod restart (`task flux:rotate-secret -- <app>`).

## Doc updates that ride with a change

`docs/16-next-steps.md` (mark done / remove from planned), the relevant
`docs/NN-*.md`, `README.md` tables where applicable, and `CLAUDE.md` only if a
top-level fact changed. `task lint` runs the docs-link checker over `docs/`,
`README.md`, and `CLAUDE.md` (it does not scan `.claude/`).
