# terraform/authentik — Authentik SSO state as code

Codifies every **user-authored** object in the authentik deployment
(auth.esweiss.com, docs/23) — applications, providers, groups, and group
memberships — mirroring the `terraform/cloudflare` / `terraform/tailscale`
pattern (GitLab HTTP state backend + 1Password-injected credentials). The live
objects were **imported, never recreated**: the module was built field-for-field
against the API and accepted only when `terraform plan` against the imported
state showed zero changes (bar the one approved exception below). Objects added
since the import — the Hermes dashboard OIDC provider + `agent-sso`
application, the AdGuard SSO dashboard providers/applications, the role
groups (`media-admins`, `dns-admins`, `bar-assistant-users`,
`home-assistant-users`), and the per-application policy bindings — are
**Terraform-authored** and created by supervised apply (no import involved).
The embedded outpost was later **adopted** (imported) so its provider list is
code too (`outpost.tf`).

## ⚠️ Apply is a supervised step (and there is no apply task yet)

`terraform apply` here rewrites live SSO objects — a wrong provider field can
break login for every application at once. There is deliberately **no
`terraform:authentik-apply` Taskfile task**: until the module has soaked, any
apply is a supervised manual `op run -- terraform apply` with the plan reviewed
line by line. CI runs only the read-only `authentik-drift-plan` job
(`.gitlab-ci.yml`): `terraform plan -detailed-exitcode`, `allow_failure: true`,
on the schedule and on authentik-module MRs — an Admin-UI hot-fix surfaces as
drift instead of being silently reverted later.

> Until the Sonarr basic-auth clear below is applied, `authentik-drift-plan`
> reports a **non-empty plan (yellow / exit 2)** for exactly that one in-place
> update — that is the *designed* advisory signal, not a failure.

## What is managed

| Kind | Count | Terraform | Import ID |
|---|---|---|---|
| Applications | 19 | `authentik_application.app[<slug>]` + explicit `agent_sso` / `adguard_01` / `adguard_02` | slug (the three explicit ones are Terraform-created — no import) |
| Proxy providers (forward_single) | 11 | `authentik_provider_proxy.<name>` | provider pk (4-10, 16, 17); `adguard_01`/`adguard_02` are Terraform-created |
| OAuth2/OIDC providers | 7 | `authentik_provider_oauth2.<name>` | provider pk (1-3, 13-15); `hermes_dashboard` is Terraform-created |
| SAML provider (GitLab) | 1 | `authentik_provider_saml.gitlab` | provider pk (12) |
| Groups + memberships | 16 | `authentik_group.app[<name>]` + explicit `media_admins` / `dns_admins` / `authentik_admins` | group uuid (`media-admins`, `dns-admins`, `bar-assistant-users`, `home-assistant-users` are Terraform-created) |
| Policy bindings (group → application) | 19 | `authentik_policy_binding.app_group[<slug>]` + explicit `agent_sso` / `adguard_01` / `adguard_02` | — (all Terraform-created) |
| Embedded outpost (provider list) | 1 | `authentik_outpost.embedded` | outpost uuid (adopted — see `outpost.tf`) |

Membership is modelled on the group's `users` list (the provider's model);
the users themselves are `data` sources only. Every application carries exactly
one group policy binding (`policy_bindings.tf`) — per-app access is enforced by
group membership. `media-admins` / `dns-admins` additionally carry basic-auth
injection attributes (`groups.tf`) consumed by the providers with
`basic_auth_enabled` (nzbget, both adguard).

## What is deliberately UNMANAGED (and why)

- **Flows, stages, policies, property mappings** — all stock authentik
  defaults (`default-*` / `managed`-flagged); nothing user-authored exists.
  Providers reference the two default flows and the default mappings via
  `data` sources. (Application **group bindings** ARE managed — see
  `policy_bindings.tf`; expression/other policies remain unmanaged because
  none exist.)
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
task terraform:authentik-import   # one-time/DR state bootstrap (import.sh; idempotent)
# NO terraform:authentik-apply — supervised manual step only (see above)
```

## Import methodology (zero-diff acceptance)

1. Every live object was enumerated verbatim from the API and the `.tf` files
   written field-for-field against that dump (nothing invented, nothing
   omitted).
2. `imports.tf` declares an import block per resource (applications by slug,
   providers by pk, groups by uuid). A plan over empty state validated every
   ID and every field: `44 to import, 0 to add, 1 to change` — the one change
   being the approved Sonarr clear.
3. `import.sh` (idempotent, state-aware, **providers → groups → applications**
   order — see its header for why order matters to the legacy import command)
   then imported all 44 objects into the GitLab backend state. `terraform
   import` only reads the API; nothing was applied.
4. Final acceptance: `terraform plan` → `Plan: 0 to add, 1 to change, 0 to
   destroy` (the Sonarr exception below). Import blocks over populated state
   are a silent no-op, so `imports.tf` stays committed as the permanent
   address↔object map and the disaster-recovery re-import path.

## The Sonarr exception (approved, pending clear)

The live Sonarr proxy provider carries a **leaked literal credential** in
`basic_auth_password_attribute` (with `basic_auth_username_attribute: eric`)
even though `basic_auth_enabled` is false — these fields are meant to hold
*attribute names*, not literals, so a real password ended up stored in
authentik config. The module pins both fields empty (matching all eight other
proxy providers). Until the first supervised apply flushes it, every plan
shows exactly:

```
  # authentik_provider_proxy.sonarr will be updated in-place
      - basic_auth_password_attribute = (sensitive) -> null
      - basic_auth_username_attribute = "eric" -> null
Plan: 0 to add, 1 to change, 0 to destroy.
```

This clear is user-approved. The leaked password itself must also be rotated
wherever else it is used.

## Provider quirks (goauthentik/authentik 2026.5.0)

- **Exact version pin, in lockstep with the server.** The provider is released
  alongside authentik (2026.5.0 ↔ server 2026.5.x; we run 2026.5.5). Bump it
  with the server upgrade, never ahead.
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
- **No `lifecycle ignore_changes` anywhere** — none proved necessary.

## Adding a new application + provider

1. **Provider** — copy the closest block: a forward-auth block in
   `providers_proxy.tf` (new `.esweiss.com` internal app behind Traefik
   forward-auth) or an OAuth2 block in `providers_oauth2.tf` (app with native
   OIDC; add a `oauth2_client_secret_<app>` variable, create the `<App> SSO`
   1Password item, and wire the `TF_VAR` into the Taskfile anchor + the
   `authentik-drift-plan` CI job).
2. **Application** — one entry in `local.applications`
   (`applications.tf`): slug, name, `group` (`Home`/`Software`/`Downloads`),
   launch URL, dashboard icon, provider reference.
3. **Group + binding** — one name in `local.member_groups` (`groups.tf`) and
   one entry in `local.application_group_bindings` (`policy_bindings.tf`):
   every application gets exactly one group binding. (A group that must carry
   basic-auth injection attributes is an explicit resource instead — mirror
   `media_admins` / `dns_admins`.)
4. For a **proxy** provider, append it to the embedded outpost's
   `protocol_providers` list (`outpost.tf` — no Admin-UI step) and add the
   Traefik forward-auth middleware/ingress on the k8s side (docs/23 + the
   app's own doc; upstreams that expect injected credentials take the
   `authentik-auth-basic` middleware variant).
5. `task terraform:authentik-plan` → review → supervised apply.
6. New objects are **created** by Terraform (no import needed); only
   pre-existing UI-created objects ever need `imports.tf` / `import.sh`
   entries. The Hermes dashboard OIDC set (`hermes_dashboard` provider +
   `agent_sso` application + role groups + bindings) is the worked example of
   this recipe; the AdGuard SSO dashboards are the proxy-provider +
   basic-auth-injection variant of it.

Day-2 operations (drift handling, token rotation, DR): `docs/40-authentik-terraform.md`.
