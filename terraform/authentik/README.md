# terraform/authentik — Authentik SSO state as code

Codifies every **user-authored** object in the authentik deployment
(auth.esweiss.com, docs/40) — applications, providers, groups, and group
memberships — mirroring the `terraform/cloudflare` / `terraform/tailscale`
pattern (GitLab HTTP state backend + 1Password-injected credentials). The
pre-existing objects were **imported, never recreated** (see "Import
methodology" below); everything added since — the Hermes and Homarr OIDC sets,
the AdGuard SSO dashboards, the role groups, the policy bindings — is
**Terraform-authored** and created by supervised apply.

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
of being silently reverted later. A non-empty drift plan is always real — there
is no expected-yellow exception.

## Guardrails

Every application, group, provider and the embedded outpost carries
`lifecycle { prevent_destroy = true }`. Renaming a `local.applications` key
would otherwise plan as destroy+create — and the slug *is* the OIDC issuer path
(`/application/o/dashboard/`) — while a group rename drops its memberships and
every binding referencing it. Removing one is a deliberate two-step change:
delete the `lifecycle` block, then the resource. Policy bindings are exempt
(all Terraform-created, cheap to recreate).

## What is managed

| Kind | Count | Terraform | Import ID |
|---|---|---|---|
| Applications | 19 | `authentik_application.app[<slug>]` + explicit `adguard_01` / `adguard_02` / `homarr` | slug (the three explicit ones are Terraform-created — no import) |
| Proxy providers (forward_single) | 10 | `authentik_provider_proxy.<name>` | provider pk (4-10, 17); `adguard_01`/`adguard_02` are Terraform-created |
| OAuth2/OIDC providers | 8 | `authentik_provider_oauth2.<name>` | provider pk (1-3, 13-15); `hermes_dashboard` / `homarr` are Terraform-created |
| SAML provider (GitLab) | 1 | `authentik_provider_saml.gitlab` | provider pk (12) |
| Groups + memberships | 18 | `authentik_group.app[<name>]` + explicit `media_admins` / `dns_admins` / `authentik_admins` | group uuid (`media-admins`, `dns-admins`, `bar-assistant-users`, `home-assistant-users`, `homarr-admins`, `homarr-users` are Terraform-created) |
| Policy bindings (group → application) | 20 | `authentik_policy_binding.app_group[<slug>]` + explicit `adguard_01` / `adguard_02` / `homarr` / `homarr_users` | — (all Terraform-created) |
| Embedded outpost (provider list) | 1 | `authentik_outpost.embedded` | outpost uuid (adopted — see `outpost.tf`) |
| Property mapping (scope) | 1 | `authentik_property_mapping_provider_scope.email_verified` | — (Terraform-created; applied to Mealie only) |

Membership is modelled on the group's `users` list (the provider's model);
the users themselves are `data` sources only. Every application carries exactly
one group policy binding (`policy_bindings.tf`) — per-app access is enforced by
group membership. `media-admins` / `dns-admins` additionally carry basic-auth
injection attributes (`groups.tf`) consumed by the providers with
`basic_auth_enabled` (nzbget, both adguard).

## What is deliberately UNMANAGED (and why)

- **Flows, stages, policies** — all stock authentik defaults (`default-*` /
  `managed`-flagged); providers reference the two default flows via `data`
  sources. (Application **group bindings** ARE managed — see
  `policy_bindings.tf`; expression/other policies remain unmanaged because
  none exist.)
- **Property mappings** — the stock scope mappings are `data` sources, with
  **one exception that IS authored**: `email_verified` in
  `providers_oauth2.tf`, given to Mealie only. Losing it breaks Mealie login
  entirely.
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
  Terraform. All three are read via `data` sources where needed.
- **Group "authentik Read-only"** — auto-generated alongside its managed RBAC
  role.

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

## Adding a new application + provider

1. **Provider** — copy the closest block: a forward-auth block in
   `providers_proxy.tf` (new `.esweiss.com` internal app behind Traefik
   forward-auth) or an OAuth2 block in `providers_oauth2.tf` (app with native
   OIDC; add a `oauth2_client_secret_<app>` variable, create the `<App> SSO`
   1Password item, and wire the `TF_VAR` into the Taskfile anchor + the
   `authentik-drift-plan` CI job).
2. **Application** — one entry in `local.applications`
   (`applications.tf`): slug, name, `group` (`Home`/`Software`/`Downloads`),
   launch URL, dashboard icon, provider reference. Do **not** add it to
   `imports.tf` — that file's `local.imported_application_slugs` is the frozen
   adopted set, and a new slug has no live object to import.
3. **Group + binding** — one name in `local.member_groups` (`groups.tf`) and
   one entry in `local.application_group_bindings` (`policy_bindings.tf`):
   every application gets exactly one group binding. (A group that must carry
   basic-auth injection attributes is an explicit resource instead — mirror
   `media_admins` / `dns_admins`.)
4. For a **proxy** provider, append it to the embedded outpost's
   `protocol_providers` list (`outpost.tf` — no Admin-UI step) and add the
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
