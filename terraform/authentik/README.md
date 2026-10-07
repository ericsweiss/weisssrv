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
`policy_bindings.tf`, `outpost.tf`, `users.tf`), plus the credential variables
and the import identity map (`imports.tf`).

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
job (`terraform plan -detailed-exitcode`, `allow_failure: exit_codes: [2]`, on
the schedule and post-merge on `main`), so an Admin-UI hot-fix surfaces as drift
instead of being silently reverted later. There is deliberately **no**
`merge_request_event` rule — the job reads every 1Password item listed in
§ Secret injection and must not run an unmerged branch's code — so the pre-merge
control is a local `task terraform:authentik-plan`. A non-empty drift plan is
always real.

## Guardrails

Every application, group, provider, the custom scope mapping and the embedded
outpost carries `lifecycle { prevent_destroy = true }` — **module-side**, so it
is not something an edit here can drop. Renaming a `local.application_data` key
would otherwise plan as destroy+create — and the slug *is* the OIDC issuer path
(`/application/o/dashboard/`) — while a group rename drops its memberships and
every binding referencing it. Policy bindings are exempt (all Terraform-created,
cheap to recreate, and the mechanism for widening or narrowing access).

Because the guard now lives in the module, removing an object is:

```bash
task terraform:authentik-state -- rm 'module.sso.authentik_application.this["<slug>"]'
# then delete the map entry here, and delete the object in authentik itself
```

Renaming a map key is the same operation in disguise — add a `moved {}` block
instead, which `prevent_destroy` does not block.

`moved.tf` is the permanent map of the pre-module state addresses: the blocks
stay, and removing one rides a supervised plan and apply.

## What is managed

| Kind | Count | Terraform address | Site data | Import ID |
|---|---|---|---|---|
| Applications | 21 | `module.sso.authentik_application.this[<slug>]` | `local.application_data` | slug (16 imported; `dashboard`, `adguard-01`, `adguard-02`, `traefik`, `status` are Terraform-created) |
| Proxy providers (forward_single) | 12 | `module.sso.authentik_provider_proxy.this[<key>]` | `local.proxy_provider_data` | provider pk (4-10, 17); `adguard_01`, `adguard_02`, `traefik_dashboard`, `uptime_kuma` are Terraform-created |
| OAuth2/OIDC providers | 8 | `module.sso.authentik_provider_oauth2.this[<key>]` | `local.oauth2_provider_data` | provider pk (1-3, 13-15); `hermes_dashboard` / `homarr` are Terraform-created |
| SAML provider (GitLab) | 1 | `module.sso.authentik_provider_saml.this["gitlab"]` | `local.saml_providers` | provider pk (12) |
| Users | 5 | `module.sso.authentik_user.this[<username>]` | `local.managed_usernames` (`users.tf`) | user pk (`eric` = 7; the four family accounts are Terraform-created) |
| Groups + memberships | 20 | `module.sso.authentik_group.this[<name>]` | `local.groups` | group uuid (11 imported plus `authentik-admins`; `bar-assistant-users`, `dns-admins`, `home-assistant-users`, `homarr-admins`, `homarr-users`, `media-admins`, `status-admins`, `traefik-admins` are Terraform-created) |
| Policy bindings (group → application) | 22 | `module.sso.authentik_policy_binding.this[<key>]` | `local.policy_bindings` | — (all Terraform-created) |
| Embedded outpost (provider list) | 1 | `module.sso.authentik_outpost.embedded[0]` | `local.embedded_outpost` | outpost uuid (adopted — see `outpost.tf`) |
| Property mapping (scope) | 1 | `module.sso.authentik_property_mapping_provider_scope.custom["email_verified"]` | `local.custom_scope_mappings` | — (Terraform-created; applied to Mealie only) |

Map keys are state addresses. The group key is the group NAME (except
`authentik-admins`, whose name carries a space and is set explicitly); the
application key is the SLUG, so Homarr is `dashboard`; provider keys are the old
resource names unchanged.

Membership is modelled on the group's `users` list (the provider's model) and
carries **usernames**, which the module resolves to pks through `data` sources.
The accounts in `local.managed_usernames` are managed here (`users.tf`); any
other username a group names is resolved through a `data` source. Every application carries **at least
one** group policy binding (`policy_bindings.tf`) — per-app access is enforced
by group membership. Homarr is the one two-tier case: two bindings
(`homarr-admins` order 0, `homarr-users` order 1) under
`policy_engine_mode = "any"`, so either grants access. That is the supported way
to express access tiers. `media-admins` / `dns-admins` additionally carry
basic-auth injection attributes (`local.group_secret_attributes` in `groups.tf`,
kept out of `local.groups` so that map stays non-sensitive) consumed by the
providers with `basic_auth_enabled` (nzbget, both adguard).

> **Group membership is EXHAUSTIVE.** All 20 groups pin `users` to an explicit
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
- **Users** — `akadmin` (break-glass) and the outpost service account only. The
  five human accounts ARE managed (`users.tf`): usernames live in git, display
  name and email come from `var.user_identities` (the 1Password "Authentik User
  Identities" item), and passwords/MFA are set by the person through enrollment
  — see docs/40 § Managed users. Any other username a group names is read
  through a `data` source (`groups.tf` carries usernames, never pks).
- **Group "authentik Read-only"** — auto-generated alongside its managed RBAC
  role.

## Thin caller of the library module

This layer is a thin caller of `weisssrv-lib//terraform/modules/authentik-sso`,
like its `terraform/cloudflare` / `terraform/tailscale` siblings: site data is one
map per object class, and the module is planned here against the live IdP, so a
behavioural module change surfaces as a real plan instead of only in the cluster
template's static render.

The terraform `?ref=` pins are bumped **by hand** — `scripts/check-lib-pins.py
--fix` does not touch them — and `scripts/test_site_configs.py` fails when one is
not equal to `WEISSSRV_LIB_REF`. Confirm with `terraform init -upgrade` before
the plan.

**A pin that lands before its tag exists is red until the tag.** `terraform init`
cannot resolve a `?ref=` that no release carries yet, so `task
terraform:validate-local` and the CI `terraform-validate` job are red for this
root in the window between a library MR merging and the tag being cut — the same
ordering every other pin surface in a library release has. Validate against a
checkout meanwhile by pointing `source` at a local path, and re-run
`terraform init -upgrade` once the tag exists.

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
| `user_identities` | `op://Homelab/Authentik User Identities/notesPlain` |
| (state backend) | `op://Homelab/GitLab Terraform State Token/credential` |

OAuth2 `client_id`s are public identifiers (they appear in every authorize
redirect) and are pinned literally in `providers_oauth2.tf`.

### Basic-auth injection

Some proxy providers keep their own credential check upstream. For those,
`basic_auth_enabled` is true and the two `*_attribute` fields name user
attributes, not credentials. The outpost reads those attributes from the user,
where the access group's attributes merge in, and sends them as the
Authorization header. The dedicated `authentik-auth-basic` Traefik middleware
forwards that header upstream, so a route without it strips the credentials.
Injection is on for NZBGet (`nzbget_user` / `nzbget_password` on
`media-admins`) and both AdGuard providers (`adguard_user` /
`adguard_password` on `dns-admins`). Every other provider leaves injection off
with both attribute fields empty. Never put a literal credential in these
fields.

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

`bash terraform/authentik/import.sh --check` prints the address↔id pairs derived
from `imports.tf` and exits without touching terraform or the API.

## Import methodology and disaster recovery

Adoption was zero-diff: every live object was enumerated from the API and the
`.tf` files written field-for-field against that dump, `imports.tf` declared an
import block per resource, and a plan over empty state validated every ID and
field before `import.sh` wrote the objects into the GitLab backend state.
`terraform import` only reads the API; nothing was applied. Import blocks over
populated state are a silent no-op, so `imports.tf` stays committed as the
permanent address↔object map.

Its addresses are **module-qualified**, and `import.sh` DERIVES its address↔id
table from `imports.tf` at run time rather than carrying a second copy — so the
two cannot disagree and bind a resource to the wrong live object.
`import.sh --check` prints the derived pairs without touching terraform or the
API, and `scripts/test_terraform_roots.py` runs it in `task lint`, so an
`import {}` shape the extractor cannot parse fails the build instead of the DR.

`imports.tf` covers the **adopted objects only**. Everything this module has
authored since (5 applications, 6 providers, 8 groups, 4 users, 1 property
mapping, all 22 policy bindings) has no import block, because authentik assigns
their pks/uuids at create time.

**DR runbook (state lost, authentik intact).** A bare `terraform plan` is *not*
"N to import, 0 to change" — the uncovered objects plan as CREATES against
objects that already exist, and apply fails part-way (slugs and group names are
unique, so it errors rather than duplicating):

1. `task terraform:authentik-import` — adopts the objects listed in `imports.tf`.
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
- **A custom scope mapping must never return `request.user.attributes`.**
  `media-admins` / `dns-admins` carry the basic-auth injection credentials as
  group attributes, and group attributes merge into every member's user
  attributes — so a mapping that returns the attribute bag would emit those
  cleartext passwords into the ID token of every app the member logs into. The
  one authored mapping (`local.custom_scope_mappings`) returns `email` /
  `email_verified` only; keep it that way.
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
2. **Application** — one entry in `local.application_data`
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
   `proxy_provider_keys` list (`outpost.tf` — no Admin-UI step). Terraform
   cannot catch a miss here: the module builds the outpost's provider list
   purely from that list, so a provider defined but never appended plans clean
   and then 404s at the outpost. This checklist step is the only control. Then
   add the
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
