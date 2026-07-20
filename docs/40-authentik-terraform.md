# Authentik SSO as Code (terraform/authentik)

Every user-authored authentik object — 16 applications, 9 forward-auth proxy
providers, 6 OAuth2 providers, the GitLab SAML provider, and 12 groups with
their memberships — is codified in [`terraform/authentik/`](../terraform/authentik/)
and was **imported** from the live deployment with a zero-diff plan (one
approved exception, below). The module README is the reference for what is
managed vs deliberately unmanaged, the secret-injection table, provider
quirks, and the add-an-app recipe; this page covers day-2 operations.

**Ground rules** (same posture as `terraform/tailscale`):

- **Apply is a supervised manual step.** No `terraform:authentik-apply` task
  exists; CI never applies. A wrong provider field can break login for every
  app at once — review the plan line by line, then `op run -- terraform apply`
  from `terraform/authentik/`.
- **Plan is always safe.** `task terraform:authentik-plan` is read-only
  against the API and the GitLab-hosted state (`terraform/state/authentik`).

## Routine change flow

1. Edit the module on a feature branch (usual MR workflow — never push main).
2. `task terraform:authentik-plan` — the diff must contain exactly what you
   meant to change and nothing else.
3. MR → merge. The `authentik-drift-plan` CI job (advisory, `allow_failure`)
   re-plans on authentik-module MRs and on the schedule.
4. Supervised apply (see ground rules), then `task terraform:authentik-plan`
   again to confirm clean.

For a **new application + provider** follow the module README ("Adding a new
application"); remember a new OAuth2 provider needs its `<App> SSO` 1Password
item (docs/15), a `TF_VAR` wired into the Taskfile env anchor AND the
`authentik-drift-plan` job, and a proxy provider needs the manual embedded-
outpost assignment plus the Traefik forward-auth wiring (docs/23).

## Drift handling (Admin-UI edits)

The scheduled `authentik-drift-plan` job exits 2 (yellow) when the live
objects differ from the code. On drift:

- **Intended UI hot-fix** → codify it: mirror the change in the module,
  MR it, and the next plan is clean. Do NOT apply first — apply would revert
  the hot-fix.
- **Unexpected drift** → treat as an incident (something writes to authentik
  besides you); inspect authentik's event log, then either codify or
  supervised-apply to revert.

Objects outside the module (flows, stages, brand, outpost, mappings) are
invisible to the plan — UI changes there never surface as drift.

## Known advisory diff: the Sonarr basic-auth clear

The live Sonarr proxy provider carries a leaked literal credential in its
(disabled) basic-auth attribute fields; the module pins them empty, which is
a user-approved clear. Until a supervised apply flushes it, every plan —
including the CI drift job — shows exactly that single in-place update
(`Plan: 0 to add, 1 to change, 0 to destroy`). Details in the module README
("The Sonarr exception"). After clearing, also rotate the leaked password
wherever it is a real credential.

## Credentials

- **API token**: 1Password item `Authentik Terraform Token` (field
  `credential`), an admin API token for the authentik API at
  `https://auth.esweiss.com`. Rotate: create a new token in authentik
  (Directory → Tokens), update the 1Password field, delete the old token —
  next `op run` picks it up; nothing in the repo changes.
- **OAuth2 client secrets**: injected from the same `<App> SSO` items the
  apps consume (table in the module README). Rotating one (docs/15) means
  updating the 1Password item, the app side, AND running a supervised apply
  so authentik matches — until then the plan shows the pending secret change
  (values are never printed; the attribute is sensitive).

## Disaster recovery

State is in the GitLab HTTP backend (`terraform/state/authentik`). If it is
lost (or you are rebuilding tooling from scratch) nothing is destroyed —
state is only a mapping:

```bash
task terraform:authentik-init      # fresh backend init
task terraform:authentik-import    # import.sh: re-imports all 44 objects (idempotent)
task terraform:authentik-plan      # must be clean again
```

`imports.tf` carries the same address↔object map declaratively (import
blocks are a no-op once state is populated), so a future supervised
`terraform apply` can also perform first-time imports by itself.

If **authentik itself** is rebuilt from scratch, the module recreates every
managed object (`terraform plan` will show all-create) — but the unmanaged
prerequisites (flows, mappings, cert) are authentik's own defaults and the
OAuth2 client ids/secrets are pinned, so app configs keep working. The
embedded-outpost assignment of the nine proxy providers is the one manual
post-step (module README).
