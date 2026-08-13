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

The resource **shape** now comes from the weisssrv-lib `authentik-sso` module at
the `?ref=` pinned in `main.tf` (v0.7.0); the files here hold this site's data as
one map per object class. Nothing about the workflow below changed with that
move — plan is still read-only, apply is still supervised, and no live object is
touched: `moved.tf` re-addresses all 78 resource instances, so the first plan
after the merge is moves and nothing else.

**Ordering, until the adoption apply lands.** `moved` blocks reach state only
through `apply`, so between the merge and the supervised
`task terraform:authentik-apply` that persists them, state still carries the
pre-move root addresses. Two consequences while that window is open: the
read-only `authentik-drift-plan` job is **expectedly yellow** (it exits 2 on the
78 pending moves — see § Drift handling), and `import.sh` must not be read as
"state is already migrated" (it isn't; the script's own guard handles it — see
`terraform/authentik/README.md` § Import methodology).

**Ground rules** (same posture as `terraform/tailscale`):

- **Apply is a supervised manual step.** `task terraform:authentik-apply`
  exists but refuses `-auto-approve`, so the plan review cannot be skipped; CI
  never applies. A wrong provider field can break login for every app at once —
  read the plan line by line, then apply and type `yes`.
- **Plan is always safe.** `task terraform:authentik-plan` is read-only
  against the API and the GitLab-hosted state (`terraform/state/authentik`).

## Renaming, removing, and the library ref

Two consequences of the module adoption an operator meets first:

- **`prevent_destroy` is module-side.** Every application, provider, group, the
  custom scope mapping and the embedded outpost carries it, and no edit in
  `terraform/authentik/` can drop it. Removing an object is
  `terraform state rm 'module.sso.<resource>.this["<key>"]'`, then the map entry,
  then the object in authentik. Renaming a map key is a destroy+create in
  disguise — add a `moved {}` block instead.
- **Bumping the module `?ref=` is a supervised change like any other.** The pin
  is not gated by `scripts/check-lib-pins.py`; bump it by hand, run
  `terraform init -upgrade`, and read the plan — a library default change lands
  as a live provider diff, which is why this root passes the flow slugs, signing
  key name, grant types and mapping lists explicitly.

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
objects differ from the code — **or** when state owes a pending `moved` block,
which Terraform also counts as a non-empty change. The second case is the one
documented expected-yellow: until the supervised apply persists `moved.tf`, the
plan is the 78 moves and nothing else, and any line that is *not* a move is real
drift. It closes with that apply. On real drift:

- **Intended UI hot-fix** → codify it: mirror the change in the module,
  MR it, and the next plan is clean. Do NOT apply first — apply would revert
  the hot-fix.
- **Unexpected drift** → treat as an incident (something writes to authentik
  besides you); inspect authentik's event log, then either codify or
  supervised-apply to revert.

Objects outside the module (flows, stages, brand, and the stock scope mappings,
which the module reads as `data` sources by managed id) are invisible to the
plan — UI changes there never surface as drift. **One mapping is the exception**:
the authored `custom:email_verified` scope mapping
(`local.custom_scope_mappings["email_verified"]`) IS a module-managed resource,
so drift on it is real, and losing it breaks Mealie login entirely — never
dismiss that signal as "just a mapping". The embedded outpost is half-in: its
**provider list** is managed (`outpost.tf`, drift surfaces), but its settings
JSON (`config`) is deliberately unconfigured and stays invisible.

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
re-deriving it here. Import addresses are module-qualified
(`module.sso.authentik_group.this["grafana-users"]`); `import.sh` refuses to run
if its table and `imports.tf` disagree.

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
