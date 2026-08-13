# terraform/authentik — Authentik SSO state as code

Codifies every **user-authored** object in the authentik deployment
(auth.esweiss.com, docs/40) — applications, providers, groups, and group
memberships — mirroring the `terraform/cloudflare` / `terraform/tailscale`
pattern (GitLab HTTP state backend + 1Password-injected credentials). The
pre-existing objects were **imported, never recreated** (see "Import
methodology" below); everything added since — the Hermes and Homarr OIDC sets,
the AdGuard SSO dashboards, the role groups, the policy bindings — is
**Terraform-authored** and created by supervised apply.

The resource **shape** — every resource, its `prevent_destroy` guard, the
unbound-application precondition and the security defaults — comes from the
weisssrv-lib `authentik-sso` module at the `?ref=` pinned in `main.tf`. What
lives here is site data: one map per object class, in the file it always lived
in (`applications.tf`, `providers_{oauth2,proxy,saml}.tf`, `groups.tf`,
`policy_bindings.tf`, `outpost.tf`), plus the credential variables, the import
identity map and `moved.tf`. See "Adoption onto the library module" below.

## ⚠️ Apply is a supervised step

`terraform apply` here rewrites live SSO objects — a wrong provider field can
break login for every application at once. Apply therefore runs only from an
operator's terminal, with the plan reviewed line by line:

```bash
task terraform:authentik-plan     # review
task terraform:authentik-apply    # confirm at the prompt
```

`terraform:authentik-apply` **refuses `-auto-approve`** (it exits non-zero
before invoking terraform if the flag is present), the same hard guard
`terraform:tailscale-apply` carries, so plan review cannot be bypassed by an
errant flag. CI never applies: it runs only the read-only `authentik-drift-plan`
job (`terraform plan -detailed-exitcode`, `allow_failure: true`, on the schedule
and on authentik-module MRs), so an Admin-UI hot-fix surfaces as drift instead
of being silently reverted later. A non-empty drift plan is always real — with
exactly one documented, self-closing exception, below.

**The one expected yellow.** `moved` blocks are written to state by `apply`, not
by `plan`, and Terraform counts a pending move as a non-empty change — so
`plan -detailed-exitcode` exits 2. From the merge commit of the module adoption
until the supervised `task terraform:authentik-apply` that persists `moved.tf`,
every `authentik-drift-plan` run (merge-commit and scheduled) is yellow with the
78 moves and **nothing else** in the plan. Confirm that by reading it: any line
that is not a move is real drift. Once the apply lands, the plan is clean again
and the always-real rule holds without qualification — there is no second
expected-yellow, and the exception disappears with `moved.tf`.

## Guardrails

Every application, group, provider, the custom scope mapping and the embedded
outpost carries `lifecycle { prevent_destroy = true }` — **module-side**, so it
is not something an edit here can drop. Renaming a `local.applications` key
would otherwise plan as destroy+create — and the slug *is* the OIDC issuer path
(`/application/o/dashboard/`) — while a group rename drops its memberships and
every binding referencing it. Policy bindings are exempt (all Terraform-created,
cheap to recreate, and the mechanism for widening or narrowing access).

Because the guard now lives in the module, removing an object is:

```bash
task terraform:authentik-init                 # state backend env
op run -- terraform state rm 'module.sso.authentik_application.this["<slug>"]'
# then delete the map entry here, and delete the object in authentik itself
```

Renaming a map key is the same operation in disguise — add a `moved {}` block
(moved.tf) instead, which `prevent_destroy` does not block.

## What is managed

| Kind | Count | Terraform address | Site data | Import ID |
|---|---|---|---|---|
| Applications | 19 | `module.sso.authentik_application.this[<slug>]` | `local.applications` | slug (3 of the 19 are Terraform-created — no import) |
| Proxy providers (forward_single) | 10 | `module.sso.authentik_provider_proxy.this[<key>]` | `local.proxy_provider_data` | provider pk (4-10, 17); `adguard_01`/`adguard_02` are Terraform-created |
| OAuth2/OIDC providers | 8 | `module.sso.authentik_provider_oauth2.this[<key>]` | `local.oauth2_provider_data` | provider pk (1-3, 13-15); `hermes_dashboard` / `homarr` are Terraform-created |
| SAML provider (GitLab) | 1 | `module.sso.authentik_provider_saml.this["gitlab"]` | `local.saml_providers` | provider pk (12) |
| Groups + memberships | 18 | `module.sso.authentik_group.this[<name>]` | `local.groups` | group uuid (`media-admins`, `dns-admins`, `bar-assistant-users`, `home-assistant-users`, `homarr-admins`, `homarr-users` are Terraform-created) |
| Policy bindings (group → application) | 20 | `module.sso.authentik_policy_binding.this[<key>]` | `local.policy_bindings` | — (all Terraform-created) |
| Embedded outpost (provider list) | 1 | `module.sso.authentik_outpost.embedded[0]` | `local.embedded_outpost` | outpost uuid (adopted — see `outpost.tf`) |
| Property mapping (scope) | 1 | `module.sso.authentik_property_mapping_provider_scope.custom["email_verified"]` | `local.custom_scope_mappings` | — (Terraform-created; applied to Mealie only) |

Map keys are state addresses. The group key is the group NAME (except
`authentik-admins`, whose name carries a space and is set explicitly); the
application key is the SLUG, so Homarr is `dashboard`; provider keys are the old
resource names unchanged.

Membership is modelled on the group's `users` list (the provider's model) and
carries **usernames**, which the module resolves to pks through `data` sources;
the users themselves are never managed. Every application carries **at least
one** group policy binding (`policy_bindings.tf`) — per-app access is enforced
by group membership. Homarr is the one two-tier case: two bindings
(`homarr-admins` order 0, `homarr-users` order 1) under
`policy_engine_mode = "any"`, so either grants access. That is the supported way
to express access tiers. `media-admins` / `dns-admins` additionally carry
basic-auth injection attributes (`local.group_secret_attributes` in `groups.tf`,
kept out of `local.groups` so that map stays non-sensitive) consumed by the
providers with `basic_auth_enabled` (nzbget, both adguard).

> **Group membership is EXHAUSTIVE.** All 18 groups pin `users` to an explicit
> list, and the provider treats that list as authoritative. Adding a household
> member to `homarr-users` / `mealie-users` / `home-assistant-users` in the Admin
> console is drift, and the next supervised apply DELETES them again — the diff
> is a list of pks, not names, so it is easy to approve by accident. Add people
> in `groups.tf` (one more username in that group's `users`), not in the UI.

> **An application with no binding fails OPEN** — it is reachable by every
> authenticated authentik user. The module's `authentik_application` therefore
> carries a `precondition` asserting each slug is named by a `policy_bindings`
> entry, so a forgotten binding fails the plan (including the read-only
> `authentik-drift-plan` job) instead of quietly widening access. Nothing here
> sets the module's `allow_unbound` escape hatch, and nothing should without a
> deliberate decision that the tile is open to every authenticated user.

## What is deliberately UNMANAGED (and why)

- **Flows, stages, policies** — all stock authentik defaults (`default-*` /
  `managed`-flagged); providers reference the two default flows via `data`
  sources. (Application **group bindings** ARE managed — see
  `policy_bindings.tf`; expression/other policies remain unmanaged because
  none exist.)
- **Property mappings** — the stock scope mappings are `data` sources (the
  module reads them by managed id), with **one exception that IS authored**:
  `local.custom_scope_mappings["email_verified"]` in `providers_oauth2.tf`,
  referenced by Mealie as `custom:email_verified` and given to no other
  provider. Losing it breaks Mealie login entirely.
- **Certificate keypair** — the install-generated self-signed keypair (data
  source; rotation is an authentik-side operation).
- **Brand, service connection, RBAC roles** — stock. The embedded outpost's
  *provider list* — its one user-touched knob — is now MANAGED
  (`authentik_outpost.embedded`, adopted by import); its settings JSON
  (`config`) is deliberately left unconfigured (Optional+Computed) so the
  authentik-managed outpost configuration is never diffed or rewritten — see
  `outpost.tf`.
- **Users** — `akadmin` (bootstrap admin) and the outpost service account are
  authentik's own; `eric` is a human whose password/MFA must never be in
  Terraform. The module reads the ones a group names via `data` sources
  (`groups.tf` carries usernames, never pks).
- **Group "authentik Read-only"** — auto-generated alongside its managed RBAC
  role.

## Adoption onto the library module (decision record)

This layer is now a thin caller of `weisssrv-lib//terraform/modules/authentik-sso`,
like its `terraform/cloudflare` / `terraform/tailscale` siblings. It was the
pre-module reference implementation, and the earlier decision record here said
adoption was blocked on three things. All three were resolved in the move:

- **The module could not express every shape.** It grew the three capabilities
  this site needs, in library v0.7.0 — `prevent_destroy` on applications,
  providers, groups, the custom mapping and the outpost; `custom_scope_mappings`
  (Mealie's asserted-verified `email_verified` scope, previously a root-only
  resource); and the unbound-application `precondition`. Nothing here is a
  weisssrv special case — the module API names none of it.
- **44+ `moved {}` blocks against a live IdP.** It is 78 blocks (`moved.tf`) —
  one per resource INSTANCE, not per resource — derived from the pre-move files
  and cross-checked both ways: every old address has a block, and every block
  targets a key the new configuration declares, so the plan is moves and nothing
  else. `imports.tf` and `import.sh` were rewritten to the same module addresses
  in the same change, and `import.sh`'s own guard proves those two agree.
- **`providers_proxy.tf` kept explicit resources where the module uses
  `for_each`.** The recorded reason was that indexing a partially-populated
  map during a one-address-at-a-time `terraform import` fails. That reasoning
  does not hold: a `for_each` key set comes from CONFIGURATION, not state, so a
  half-imported state cannot make `…this["sonarr"]` unresolvable. What a
  half-imported state does produce is a plan full of creates — which is why
  `import.sh` must run to completion before any plan is trusted.

Consequence for the library: `authentik-sso` now has a consumer that plans it
against a live IdP, so a behavioural change to the module surfaces here as a
real plan instead of only in the cluster template's static render check. The
`?ref=` pinned v0.7.0 ahead of the other two roots while those capabilities
existed in no earlier release; all three roots converged at v0.7.0 with the
`WEISSSRV_LIB_REF` bump. The terraform `?ref=` pins are bumped by hand and held equal to
`WEISSSRV_LIB_REF` by `scripts/test_site_configs.py`; confirm with
`terraform init -upgrade` before the plan.

**The pin lands before the tag exists.** `terraform init` cannot resolve
`?ref=v0.7.0` until that release is cut, so `task terraform:validate-local` and
the CI `terraform-validate` job are red for this root in the window between the
library MR merging and the tag — the same ordering every other pin surface in a
library release has. Validate against a checkout meanwhile by pointing `source`
at a local path, and re-run `terraform init -upgrade` once the tag exists.

**No `outputs.tf`, deliberately.** The module exposes `application_ids`,
`group_ids`, `policy_binding_ids` and the provider id maps, which would shorten
the DR runbook below — but declaring them makes the very next plan non-empty
("Changes to Outputs"), which turns the read-only `authentik-drift-plan` job
yellow until a supervised apply. Read the ids from the API instead (below), or
add the outputs deliberately, expecting one non-empty plan.

## Secret injection (no secrets in git — ever)

All credentials are `op run`-injected `TF_VAR_*`s (Taskfile locally, `op read`
in CI). The OAuth2 client secrets come from the **same 1Password items the
applications themselves consume** (docs/15-credential-rotation.md), so
Terraform and the app can never disagree:

| TF variable | 1Password reference |
|---|---|
| `authentik_token` | `op://Homelab/Authentik Terraform Token/credential` |
| `oauth2_client_secret_mealie` | `op://Homelab/Mealie SSO/oidc-client-secret` |
| `oauth2_client_secret_bar_assistant` | `op://Homelab/Bar Assistant SSO/authentik-client-secret` |
| `oauth2_client_secret_home_assistant` | `op://Homelab/Home Assistant SSO/authentik-client-secret` |
| `oauth2_client_secret_grafana` | `op://Homelab/Grafana SSO/oidc-client-secret` |
| `oauth2_client_secret_nextcloud` | `op://Homelab/Nextcloud SSO/client-secret` |
| `oauth2_client_secret_immich` | `op://Homelab/Immich SSO/client-secret` |
| `oauth2_client_secret_hermes_dashboard` | `op://Homelab/Hermes Secrets/hermes-dashboard-oidc-client-secret` |
| `oauth2_client_secret_homarr` | `op://Homelab/Homarr SSO/client-secret` |
| `basic_auth_nzbget_username` | `op://Homelab/NZBGet/username` |
| `basic_auth_nzbget_password` | `op://Homelab/NZBGet/password` |
| `basic_auth_adguard_username` | `op://Homelab/AdGuard Home/username` |
| `basic_auth_adguard_password` | `op://Homelab/AdGuard Home/password` |
| (state backend) | `op://Homelab/GitLab Terraform State Token/credential` |

OAuth2 `client_id`s are public identifiers (they appear in every authorize
redirect) and are pinned literally in `providers_oauth2.tf`.

## State backend

Same GitLab HTTP backend as the siblings, its own state name (no collision):

```
.../terraform/state/authentik   (+ /lock)
TF_HTTP_LOCK_METHOD=POST         # GitLab state backend locks via POST
TF_HTTP_UNLOCK_METHOD=DELETE     # and unlocks via DELETE (else apply → 405)
```

> **This state is secret-bearing.** Terraform stores `client_secret` and the
> group `attributes` JSON (which carries the injected NZBGet ControlPassword and
> AdGuard admin password) in state **in the clear**, regardless of the `sensitive`
> flag — and GitLab-managed state is downloadable over the API by any project
> Maintainer with an `api`-scoped token, a wider audience than the 1Password
> Homelab vault. Treat read access to `terraform/state/authentik` as
> vault-equivalent, and note that rotating a secret does **not** remove it from
> retained state versions (`docs/15-credential-rotation.md`).

Saved plans have the same property, which is why `terraform/.gitignore` ignores
`tfplan` / `tfplan.json` as well as `*.tfplan`.

## Taskfile wrappers

```bash
task terraform:authentik-init     # terraform init (GitLab state backend)
task terraform:authentik-plan     # review the diff vs the live authentik objects
task terraform:authentik-apply    # SUPERVISED — refuses -auto-approve
task terraform:authentik-import   # one-time/DR state bootstrap (import.sh; idempotent)
```

## Import methodology and disaster recovery

Adoption was zero-diff: every live object was enumerated from the API and the
`.tf` files written field-for-field against that dump, `imports.tf` declared an
import block per resource, and a plan over empty state validated every ID and
field before `import.sh` wrote the 44 objects into the GitLab backend state.
`terraform import` only reads the API; nothing was applied. Import blocks over
populated state are a silent no-op, so `imports.tf` stays committed as the
permanent address↔object map.

Its addresses are **module-qualified** since the move onto the library module,
and `import.sh` carries the same table with a guard that derives the address↔id
set from `imports.tf` and refuses to run if the two disagree — so a rewritten
address cannot bind a resource to the wrong live object unnoticed.

**Ordering against `moved.tf`.** `terraform state list` reads raw state, and
`moved` blocks reach state only through `apply` — so until the supervised apply
persists the moves, state still holds the pre-move root addresses. `import.sh`
handles that: its already-in-state check reads the old↔new pairs out of
`moved.tf` and treats a resource as present at **either** address, so running it
in that window skips rather than re-importing into the addresses the moves
target (which Terraform would then refuse to move onto, stranding the old
addresses as configuration-less orphans that plan as destroy). The import-block
path in `imports.tf` was never exposed to this — Terraform applies moves to
prior state before evaluating import blocks. Deleting `moved.tf` once the apply
has landed closes the window permanently and empties the pair list.

`imports.tf` covers the **44 adopted objects only**. Everything this module has
authored since (3 applications, 4 providers, 6 groups, 1 property mapping, all
20 policy bindings) has no import block, because authentik assigns their
pks/uuids at create time.

**DR runbook (state lost, authentik intact).** A bare `terraform plan` is *not*
"N to import, 0 to change" — the uncovered objects plan as CREATES against
objects that already exist, and apply fails part-way (slugs and group names are
unique, so it errors rather than duplicating):

1. `task terraform:authentik-import` — adopts the 44 objects in `imports.tf`.
2. Enumerate the rest from the API and `terraform import` each one:
   ```bash
   curl -sH "Authorization: Bearer $AUTHENTIK_TOKEN" \
     https://auth.esweiss.com/api/v3/core/applications/ | jq -r '.results[].slug'
   # …/core/groups/ for uuids, …/providers/all/ for pks,
   # …/policies/bindings/ for binding uuids
   ```
3. `terraform plan` — only now is "0 to add" the expected result.

Adding the new import blocks to `imports.tf` as you go shortens step 2 next time.

## Provider quirks (goauthentik/authentik)

- **Exact version pin, in lockstep with the server.** The provider is released
  alongside authentik and its minor must match `authentik_version` in
  `group_vars/all.yml`. Bump it with the server upgrade, never ahead.
- **Proxy `property_mappings` is left unconfigured.** authentik auto-assigns
  the five default scope mappings to every proxy provider, and the provider's
  Read only tracks the field once explicitly configured — setting it would
  leave a permanent phantom `+ property_mappings` diff on imported state. The
  live lists are exactly the auto-assigned defaults. (OAuth2/SAML providers
  DO pin their mapping lists — their Read tracks the field unconditionally.)
- **SAML `default_name_id_policy` has no schema field.** Live value equals the
  server default (`…nameid-format:persistent`), so nothing drifts; a UI change
  to it would be invisible to Terraform.
- **`allowed_redirect_uris` entries need `redirect_uri_type = "authorization"`**
  — the API returns the key, so omitting it in config diffs forever.
- **No `ignore_changes` anywhere** — none proved necessary. (`prevent_destroy`
  is unrelated and is on everywhere — see Guardrails.)
- **Group `attributes` is only sent when there is something to send.** The
  module emits `null` for a group with no attributes rather than `jsonencode({})`,
  which is exactly what the pre-module resources did by omitting the field — the
  16 groups without injection credentials keep whatever the provider's own
  default means.

## Adding a new application + provider

1. **Provider** — copy the closest entry: `local.proxy_provider_data`
   (`providers_proxy.tf`) for a new `.esweiss.com` app behind Traefik
   forward-auth, or `local.oauth2_provider_data` (`providers_oauth2.tf`) for an
   app with native OIDC. An OIDC app also needs a `oauth2_client_secret_<app>`
   variable, an entry in `local.oauth2_client_secrets`, the `<App> SSO`
   1Password item, and the `TF_VAR` wired into the Taskfile anchor + the
   `authentik-drift-plan` CI job. Entries carry only what differs from
   `local.*_provider_defaults` — those defaults are this site's pinned posture,
   not the module's.
2. **Application** — one entry in `local.applications`
   (`applications.tf`): key = slug, then name, `group`
   (`Home`/`Software`/`Downloads`), launch URL, dashboard icon, and
   `provider_type` + `provider_key` naming the entry from step 1. Do **not** add
   it to `imports.tf` — that file's `local.imported_application_slugs` is the
   frozen adopted set, and a new slug has no live object to import.
3. **Group + binding** — one name in `local.member_groups` (`groups.tf`) and
   one entry in `local.policy_bindings` (`policy_bindings.tf`): every
   application needs at least one group binding, and the module's precondition
   fails the plan if you skip it. Two or more bindings under
   `policy_engine_mode = "any"` is the supported way to express access tiers —
   mirror the Homarr pair. (A group that must carry basic-auth injection
   attributes also gets an entry in `local.group_secret_attributes` — mirror
   `media-admins` / `dns-admins`.)
4. For a **proxy** provider, append its key to the embedded outpost's
   `proxy_provider_keys` list (`outpost.tf` — no Admin-UI step) and add the
   Traefik forward-auth middleware/ingress on the k8s side
   (`kubernetes/apps/authentik/README.md` + the app's own doc; upstreams that
   expect injected credentials take the `authentik-auth-basic` variant).
5. `task terraform:authentik-plan` → review → supervised apply.
6. New objects are **created** by Terraform (no import needed); only
   pre-existing UI-created objects ever need `imports.tf` / `import.sh`
   entries. The Hermes dashboard OIDC set (the `hermes_dashboard` provider +
   role groups + bindings, attached to the pre-existing imported `agent`
   application) is the worked example of this recipe; the AdGuard SSO
   dashboards are the proxy-provider + basic-auth-injection variant of it.

Day-2 operations (drift handling, token rotation, DR): `docs/40-authentik-terraform.md`.
