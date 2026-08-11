# Authentik SSO as Code (terraform/authentik)

Every user-authored authentik object — applications, forward-auth proxy
providers, OAuth2 providers, the GitLab SAML provider, groups with their
memberships, and the per-application group policy bindings — is codified in
[`terraform/authentik/`](../terraform/authentik/) (current counts in the
module README's "What is managed" table). The original object set was
**imported** from the live deployment with a zero-diff plan (one approved
exception, below); later additions are Terraform-authored and created by
supervised apply. The module README is the reference for what is
managed vs deliberately unmanaged, the secret-injection table, provider
quirks, and the add-an-app recipe; this page covers day-2 operations.

**Ground rules** (same posture as `terraform/tailscale`):

- **Apply is a supervised manual step.** `task terraform:authentik-apply`
  exists but refuses `-auto-approve`, so the plan review cannot be skipped; CI
  never applies. A wrong provider field can break login for every app at once —
  read the plan line by line, then apply and type `yes`.
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
item (docs/15) and a `TF_VAR` wired into the Taskfile env anchor AND the
`authentik-drift-plan` job, while a proxy provider needs an entry in the
embedded outpost's Terraform-managed provider list (`outpost.tf`) plus the
Traefik forward-auth wiring (`kubernetes/apps/authentik/README.md`). The Hermes
dashboard OIDC set (`hermes_dashboard` provider on the `agent` application, role
groups + per-app bindings — docs/37 § SSO) is the worked example of this flow.

## Drift handling (Admin-UI edits)

The scheduled `authentik-drift-plan` job exits 2 (yellow) when the live
objects differ from the code. On drift:

- **Intended UI hot-fix** → codify it: mirror the change in the module,
  MR it, and the next plan is clean. Do NOT apply first — apply would revert
  the hot-fix.
- **Unexpected drift** → treat as an incident (something writes to authentik
  besides you); inspect authentik's event log, then either codify or
  supervised-apply to revert.

Objects outside the module (flows, stages, brand, mappings) are invisible to
the plan — UI changes there never surface as drift. The embedded outpost is
half-in: its **provider list** is managed (`outpost.tf`, drift surfaces), but
its settings JSON (`config`) is deliberately unconfigured and stays
invisible.

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

State is in the GitLab HTTP backend (`terraform/state/authentik`). Losing it
destroys nothing — state is only a mapping — but recovery is **not** a single
command: `imports.tf` and `import.sh` cover the originally adopted objects only,
and everything the module has authored since has no import block, because
authentik assigns those pks/uuids at create time. A bare `terraform plan` after
state loss therefore shows CREATES for objects that already exist.

The full runbook (import the adopted set, enumerate the rest from the API and
import each, then verify a clean plan) lives in
[`terraform/authentik/README.md`](../terraform/authentik/README.md)
§ "Import methodology and disaster recovery". Follow it there rather than
re-deriving it here.

If **authentik itself** is rebuilt from scratch, the module recreates every
managed object (`terraform plan` will show all-create) — but the unmanaged
prerequisites (flows, mappings, cert) are authentik's own defaults and the
OAuth2 client ids/secrets are pinned, so app configs keep working. The
embedded outpost is authentik's own object: re-import it (its uuid changes on
a rebuild — update `imports.tf`), after which the managed provider list
reapplies; no Admin-UI steps remain.

## Related documentation

- [`terraform/authentik/README.md`](../terraform/authentik/README.md) — the module reference (managed objects, secret injection, provider quirks, add-an-app recipe, DR runbook)
- [docs/15-credential-rotation.md](15-credential-rotation.md) — the `<App> SSO` 1Password items
- [`kubernetes/apps/authentik/README.md`](../kubernetes/apps/authentik/README.md) — the in-cluster Authentik deployment and Traefik forward-auth wiring
