# Credential Rotation Guide

This document explains how to rotate passwords, API tokens, and SSH keys stored in 1Password.

## Overview

Secrets live in three 1Password vaults and are injected at use time.
**Nothing sensitive is ever committed to git** — only references.

- **Homelab** — everything the deployment reads at run time: the `op run`
  consumers and the ESO `ClusterSecretStore`, which reads this vault only.
- **Homelab-Admin** — admin and CI-only items.
- **Homelab-Boot** — the two ZFS pool passphrases and nothing else, so the
  boot-unlock Connect token can be scoped to them alone.

### Secrets model: three consumers

This is the canonical description of the model; other pages (README.md,
CLAUDE.md, `ansible/README.md`, `docs/29-flux-operations.md`) point here.

1. **Ansible / Terraform / the Task wrapper** — `op run --` resolves
   `op://Homelab/<Item Title>/<field>` references at run time. The reference
   strings live next to the thing that needs them: each Task task's `env:`
   block, in the root `Taskfile.yml` or the owning `taskfiles/<ns>.yml`,
   mirrored by the `variables:` of the CI job that runs the same
   playbook. `task secrets:show` prints the set actually in use. Item titles
   with spaces are fine — `op run` parses the full path. Rotate by updating
   1Password, then re-running the relevant playbook (per-credential procedures
   below).
2. **External Secrets Operator, in-cluster** — the `onepassword-homelab`
   `ClusterSecretStore` (namespace `external-secrets`, 1Password **Connect**
   provider) syncs `ExternalSecret` resources into Kubernetes `Secret`s. Connect
   runs in-cluster and serves from a local encrypted cache — no calls out to the
   1Password cloud on each read. `remoteRef.key` is the **item title** and
   `remoteRef.property` is the **field name** (format reference:
   `docs/29-flux-operations.md` § "1Password Connect Provider Reference
   Format"). Rotate via `task flux:rotate-secret -- <app>` — a refresh alone is
   not enough, because Reloader deliberately ignores Secret changes
   (`ignoreSecrets: true`), so consuming pods must be restarted.
   The bootstrap Secrets `op-credentials` and `onepassword-connect-token` in the
   `external-secrets` namespace are the **only** manually created Kubernetes
   Secrets (`task flux:bootstrap-onepassword`); every other in-cluster Secret is
   produced by ESO from a manifest Flux reconciles — with one credential this
   estate does not own at all (below).
3. **CI pipelines** — `.gitlab-ci.yml` uses `op run` / `op read` with
   `OP_SERVICE_ACCOUNT_TOKEN` (a service-account token, not Connect) to inject
   secrets into jobs at run time.

**One in-cluster Secret is owned by GitLab, not by us.** The GitLab agent's Flux
module creates the `gitlab-flux-system` `Receiver` and its
`gitlab-receiver-flux-system` HMAC Secret in `flux-system` out of band (docs/29
§ Push-Triggered Reconciliation). The token is minted by KAS, has no 1Password
item, and is **not rotated from this repo** — re-minting it means re-creating
the agent registration in GitLab. It is allowlisted in
`scripts/check-unmanaged-secrets.py` so its owner is on the record.

**The `weisssrv.infra` collection adds no fourth consumer.** The roles ship no
credentials and no `op://` references of their own, so every secret arrives as
an inventory variable resolved by consumer 1 above. Several roles declare these
values as required inputs rather than carrying a site default, so the reference
must exist in this repo's inventory. Each role's README in the collection lists
every input it requires.

## Required 1Password Items

This is the canonical, authoritative inventory of every item the deployment
expects. Each subsection names its vault; unless stated otherwise the item lives
in **Homelab**. CLAUDE.md, `docs/02-install.md`, `docs/13-ci-cd.md` and
`docs/27-gitlab-deployment.md` all point here; update this list (not those
files) when an item is added or its fields change.

The **Inventory** table below is the complete list. Items whose handling needs
more than a table cell have a subsection under
[Item detail](#item-detail); everything else is fully described by its row.

Unless a row says otherwise, "rotate" means: update the field in 1Password, then
re-run the consuming deploy (`task <area>:deploy`) for `op run` consumers, or
`task flux:rotate-secret -- <app>` for in-cluster ESO consumers. That task
dispatches on a fixed list of apps (`task flux:rotate-secret` with no argument
prints it). The ExternalSecrets with no written procedure yet are listed in
`DECLARED_MANUAL` in `scripts/check-secret-rotation-coverage.py`; rotate one by
hand with `task flux:refresh-secret -- <ns>/<name>`, then
`kubectl -n <ns> delete pod -l <selector>`. Reloader sets `ignoreSecrets: true`,
so a refreshed Secret never rolls the pods on its own.

### Inventory

#### DNS, TLS and network

| Item | Fields | Consumed by |
|---|---|---|
| Cloudflare DNS Token | `credential` (DNS:Edit + Zone:Read), `username` (account ID) | ESO → cert-manager, external-dns, cloudflare-ddns; `acme_certs` role |
| Cloudflare Terraform Token | `credential` (Zone:Read + DNS:Edit + Zone Settings:Edit), `username` (account ID) | `terraform/cloudflare` via Taskfile and the terraform-plan / deploy-terraform CI jobs |
| AdGuard Home | admin username + password | `adguard_home` role; `terraform/authentik` basic-auth injection — see [detail](#adguard-home) |
| Tailscale Auth Key | auth key | `tailscale` role |
| Tailscale OAuth | `client id`, `credential` (scopes `acl` + `dns`, write) | `terraform/tailscale` — tailnet ACL policy and the `esweiss.com` split-DNS nameservers |
| Tailscale Operator OAuth | `client-id`, `client-secret` | ESO → `operator-oauth` for the Tailscale Kubernetes operator — see [detail](#tailscale-operator-oauth) |
| UniFi Controller | `url` (gateway base URL), `api-key` (Control Plane integration key), `username` + `password` (local-only admin) | `terraform/unifi` via Taskfile and the `unifi-drift-plan` CI job — see [detail](#unifi-controller) |
| WiFi TheRevengers, WiFi Panopticon, WiFi kugel-tikka-masala, WiFi DunderMiffLAN | `password` (WPA-PSK, 8-63 chars) | `terraform/unifi` — one item per SSID, read as `TF_VAR_wlan_passphrase_*` — see [detail](#wifi-ssid-pre-shared-keys) |
| DNS-01 SSH Key | private + public key | cert distribution to the DNS LXCs |
| Plex Custom Certificate | password | `plex-cert-reload.sh` PKCS#12 passphrase — see [detail](#plex-custom-certificate) |

#### Mail

| Item | Fields | Consumed by |
|---|---|---|
| SMTP Relay Gmail | username + app password | `smtp_relay` role (upstream auth) |
| SMTP Relay Auth | username + password | null-client auth to smtp-relay |
| Email Config | `root_alias` | root mail aliasing on every host |

#### Hosts and storage

| Item | Fields | Consumed by |
|---|---|---|
| SSH Key | public + private key | `base` role (operator key distribution) |
| Samba NAS User | nas user password | `nas_storage` role (Samba) |
| Proxmox API Token | `user`, `token-name`, `token-secret` (PVEAuditor) | Proxmox exporter and the Homarr Proxmox integration. `proxmox_firewall` creates `monitoring@pve!exporter` only when it is absent, and the secret is printed once at creation — rotating it is `pveum user token remove` then `add`, capturing the printed value into this item |
| ZFS Encryption Connect Token | `credential` | `zfs_encryption` role — boot-time pool passphrase fetch. Create with `op connect token create weisssrv-zfs --server <id> --vaults Homelab-Boot`; the token reads only the passphrase vault, while the item holding it stays in Homelab |

#### ZFS boot unlock (vault: Homelab-Boot)

This vault holds only these two items. The Connect server must be granted it
before a token can cover it (`zfs_encryption` role README § Scoping the Connect
token).

| Item | Fields | Consumed by |
|---|---|---|
| ZFS Pool tank Passphrase | `passphrase` (≥32 random chars) | `zfs-load-key@tank.service` on pve-nas-01 |
| ZFS Pool ssd Passphrase | `passphrase` | `zfs-load-key@ssd.service` on pve-nas-01 |

#### k3s platform

| Item | Fields | Consumed by |
|---|---|---|
| K3s Cluster Token | `credential` | server join |
| K3s Agent Token | `credential` | agent (worker) join — the lower-privilege token; see docs/19 |
| K3s Kubeconfig | kubeconfig file content | `.k3s-deploy-base` CI template fallback (the GitLab agent is preferred) |
| Flux GitLab PAT | personal access token | Flux `GitRepository` read access |
| Flux Webhook Token | hex token | optional Flux `Receiver` path; day-to-day reconcile comes from the GitLab agent (docs/29) |
| Service Account Auth Token weisssrv | service-account token | `OP_SERVICE_ACCOUNT_TOKEN` in CI |
| GitHub Token | `credential` | version-checker API rate limits (`task maintenance:check-versions`, the `version-bump-bot` CI job) |

`Flux Webhook Token` and `Service Account Auth Token weisssrv` live in
**Homelab-Admin**, not `Homelab` — they are CI/admin-only and ESO never reads
them.

#### GitLab

| Item | Fields | Consumed by |
|---|---|---|
| GitLab | `root-password` | initial GitLab root user |
| GitLab API Token | `credential` (admin PAT, `api` scope) | `deploy-gitlab` (`GITLAB_ADMIN_API_TOKEN`); hard-asserted by `task gitlab:deploy` for the Web IDE settings block. **Not** the PR-Agent credential — that job is `secrets_source: env` and uses the `weisssrv-review-bot` project token |
| GitLab Version Bump Bot Token | `credential` (`api` + `write_repository`, Developer+) | the `VERSION_BUMP_BOT_TOKEN` CI variable — see [detail](#gitlab-version-bump-bot-token) |
| GitLab SSO | `saml-cert-fingerprint` | Authentik SAML |
| GitLab Runner | `runner-token` (`glrt-*`) | shared multi-project runner, tags `k8s-deploy`, untagged yes |
| GitLab Runner Privileged | `runner-token` (`glrt-*`) | infrastructure runner, tags `infrastructure`, untagged no |
| GitLab Agent Token | `credential` | GitLab Kubernetes Agent registration |
| CI Cache Garage | `access-key`, `secret-key` (S3 key pair), `rpc-secret`, `admin-token`, `metrics-token` | ESO → `ci-cache-garage-secrets`, the `s3access` Secret in both runner namespaces, and `observability-exporter-secrets` — see [detail](#ci-cache-garage) |
| GitLab Registry Cache Deploy Token | `username` + `token` (deploy token, `read_registry`) | `registry-cache-secrets` ExternalSecret — see [detail](#gitlab-registry-cache-deploy-token) |
| GitLab Terraform State Token | `credential` | Terraform HTTP state backend (local use) |

#### Authentik and SSO

| Item | Fields | Consumed by |
|---|---|---|
| Authentik Secrets | `secret-key`, `postgresql-password`, `postgresql-admin-password` | Authentik server/worker; `postgresql-admin-password` is also the password of the hand-created `postgres` superuser the exporter sidecar connects as, so rotating it needs a matching `ALTER ROLE` — see `kubernetes/apps/authentik/README.md` § 1Password items |
| Authentik Terraform Token | `credential` (admin API token) | `terraform/authentik` — see [detail](#authentik-terraform-token) |
| Authentik User Identities | the item's **Notes** (`notesPlain`) holds ONLY a JSON object: `{"<username>": {"name": …, "email": …}, …}` covering every username in `terraform/authentik/users.tf` | `terraform/authentik` — keeps managed-user names + emails out of the public-mirrored repo (docs/40 § Managed users) |
| Mealie SSO | `oidc-client-id`, `oidc-client-secret` | Mealie (password login disabled) |
| Bar Assistant SSO | `authentik-client-id`, `authentik-client-secret` | Bar Assistant (password login disabled) |
| Home Assistant SSO | `authentik-client-id`, `authentik-client-secret` | hass-openid |
| Nextcloud SSO | `client-id`, `client-secret` | `user_oidc` app (SSO-only, local form hidden) |
| Immich SSO | `client-id`, `client-secret` | Immich (password login disabled after bootstrap) |
| Homarr SSO | `client-id`, `client-secret`, `secret-encryption-key`, `admin-username`, `admin-password` | ESO + `terraform/authentik` — see [detail](#homarr-sso) |
| Grafana SSO | `oidc-client-id`, `oidc-client-secret` | Grafana OIDC |

#### Applications

| Item | Fields | Consumed by |
|---|---|---|
| NZBGet | `username`, `password` | NZBGet HTTP Basic **and** the Authentik proxy provider — see [detail](#nzbget) |
| PrivadoVPN Credentials | `openvpn-user`, `openvpn-password` | Gluetun (default provider) |
| VPN Unlimited Credentials | four openvpn-* fields | alternate Gluetun provider — see [detail](#vpn-unlimited-credentials) |
| Download Client API Keys | `sonarr-api-key`, `radarr-api-key`, `lidarr-api-key`, `prowlarr-api-key`, `gluetun-control-apikey` | the \*arr apps and the gluetun-exporter — see [detail](#download-client-api-keys) |
| WireGuard VPN | `init-username`, `init-password`, `metrics-token` | wg-easy first-boot admin + `/metrics/prometheus` bearer (docs/38) |
| Mealie Secrets | `postgres-password` | Mealie Postgres |
| Bar Assistant Secrets | `meilisearch-master-key` | Meilisearch |
| OpenAI API Key | `api-key` | Mealie recipe parsing — entered in Mealie's UI, not ESO-synced |
| Hermes Secrets | five synced fields + three reserve fields | Hermes gateway/dashboard — see [detail](#hermes-secrets) |
| Hermes Agent 1P Service Account | `credential` | Hermes' 1Password skill — see [detail](#hermes-agent-1p-service-account) |
| Hermes Registry Pull | `token` (GitLab deploy token, `read_registry` on `eric/weisssrv`) | `hermes-registry-pull` ExternalSecret — see [detail](#hermes-registry-pull) |
| Home Assistant API Token | `token`, `backup_encryption_key` | HA Prometheus endpoint + offsite backup decryption — see [detail](#home-assistant-api-token) |
| Nextcloud Secrets | `admin-password`, `postgres-password`, `serverinfo-token` | Nextcloud break-glass admin, DB role, exporter — see [detail](#nextcloud-secrets) |
| Immich Secrets | `postgres-password`, `admin-bootstrap-password` | Immich compose DB; the bootstrap password is operator-only |
| Homarr Proxmox Token | `token-id`, `token-secret` (PVEAuditor) | the same `monitoring@pve!exporter` pair as the exporter, entered in the Homarr UI rather than ESO-consumed (docs/41) |
| Homarr Integrations | per-integration API keys | DR-convenience record of UI-entered credentials, not ESO-consumed |
| Uptime Kuma | `admin-username`, `admin-password` | Kuma's single admin account **and** its `/metrics` scrape — see [detail](#uptime-kuma) |

#### Observability and backups

| Item | Fields | Consumed by |
|---|---|---|
| Loki Push Auth | `htpasswd` (bcrypt) | Loki push IngressRoute basicAuth middleware |
| Discord Alert Webhook | `url` | Alertmanager notifications |
| Healthchecks Watchdog | ping url | Alertmanager Watchdog dead-man's switch |
| B2 Archive Backup | two key pairs + `restic_repo_password` | offsite restic/B2 chain — see [detail](#b2-archive-backup) |

### Item detail

#### AdGuard Home

The `adguard_home` role generates the bcrypt hash in `AdGuardHome.yaml` from the
plaintext password at deploy time; no `password_hash` field is injected or
consumed. `terraform/authentik` reads the same item
(`TF_VAR_basic_auth_adguard_*`) as the injected credentials on the AdGuard SSO
dashboard proxy providers (docs/08), so the injection can never drift from the
deployed admin login. Rotation needs **both** `task dns:deploy` and a supervised
terraform apply (docs/40).

#### Tailscale Operator OAuth

An OAuth client scoped **write** on Devices/Core, Keys/Auth Keys and Services,
associated with tag `tag:k8s-operator`. ESO syncs it to the `operator-oauth`
Secret for the Tailscale Kubernetes operator, which exposes the internal Traefik
ingress and the `ts-dns` resolver to the tailnet (docs/05). Distinct from
**Tailscale OAuth** and **Tailscale Auth Key**.

#### Plex Custom Certificate

The PFX bundle passphrase `/usr/local/sbin/plex-cert-reload.sh` uses when
converting the renewed PEM cert into the PKCS#12 form Plex requires. The same
value must be configured under Plex Settings → Network → "Custom certificate
encryption key".

#### UniFi Controller

The UCG-Fiber's own credentials. `api-key` is what Terraform authenticates with
— a Control Plane → Integrations key belonging to a **local-access-only** admin
with no 2FA, because the provider cannot satisfy an MFA prompt. `username` /
`password` are that admin's console login: the break-glass path when the API
key is revoked or the API is unreachable, never read by Terraform. Two other
console admins exist and are **not** in this vault by design: the ui.com
**Owner** (`ericsweiss1@gmail.com`, an SSO account whose credentials and MFA
live at account.ui.com, not here), and a local **`homeassistant`** admin with
full rights inside the UniFi Network application — the same reach as the
`terraform` admin, no 2FA, no cloud access — kept deliberately for a few Home
Assistant write actions and left unvaulted to avoid a rotation burden it does
not earn. Neither is read by any tooling. `url`
is the **production** gateway address (`https://10.0.10.1`); while the gateway is on
a bench, override `TF_VAR_unifi_api_url` per invocation instead of editing the
item (docs/46).

Rotate: mint a replacement key in Control Plane → Integrations, update the
field, revoke the old key, then verify with `task terraform:unifi-plan` (an
empty plan proves the new key reads every object). **The local plan is the
check that matters.** One transitional window aside: between the v0.13.1 pin
bump and the first supervised apply the plan is legitimately non-empty — the
enumerable set in `terraform/unifi/README.md` — and what proves the key there
is that the plan *renders* rather than failing to read. Once that apply has
converged the root, the empty plan is again the pass condition and any diff
during a rotation is real drift, not an accepted exception. The key is also read by `unifi-drift-plan`. That job's `allow_failure` is scoped
to `exit_codes: [2]`, so only real drift renders yellow — a revoked key (401) or
a renamed field fails the job red and is visible on the next scheduled pipeline.
Once a supervised apply has converged the root, a yellow `unifi-drift-plan` is
real drift (`terraform/unifi/README.md` owns the plan/apply rules).

#### WiFi SSID pre-shared keys

One item per SSID (`WiFi TheRevengers`, `WiFi Panopticon`,
`WiFi kugel-tikka-masala`, `WiFi DunderMiffLAN`), each holding just `password` —
the WPA-PSK `terraform/unifi` pushes to that WLAN. Separate items rather than
one multi-field item because each is a separate `TF_VAR_wlan_passphrase_*`
reference in the Taskfile anchor and the `unifi-drift-plan` job, and because
the guest key is shared with visitors while the others are not.

Rotate: update the field, then a supervised `task terraform:unifi-apply`. This
is **disruptive by design** — every device on that VLAN drops off the moment the
apply lands and has to be re-joined with the new key, which for the IoT SSID
means re-onboarding each device. Rotate one SSID at a time, and never rotate
`WiFi TheRevengers` remotely: the admin workstation is on it.

Rotating the PSK does **not** scrub the old one. `terraform/unifi` stores every
WLAN passphrase in its GitLab-managed state in the clear, and GitLab retains
previous state versions, so the superseded key stays readable to anyone who can
read `terraform/state/unifi` — any project Maintainer with an `api`-scoped
token. A routine rotation can accept that. A rotation prompted by a
*compromised* key also needs the retained state versions purged.

#### Authentik Terraform Token

An admin API token for the Authentik API, used by `terraform/authentik` to
manage applications, providers and groups (docs/40). Rotate by minting a new
token in Authentik (Directory → Tokens), updating this field, then deleting the
old token.

#### GitLab Version Bump Bot Token

The item of record for the **masked, protected** `VERSION_BUMP_BOT_TOKEN` CI/CD
variable, which is what the `version-bump-bot` job reads. The job cannot use
`CI_JOB_TOKEN` (it cannot push, and the merge-requests API is read-only for job
tokens) and cannot `op read` the value either, since it is consumed as a CI
variable. Deliberately not the admin **GitLab API Token**: this one is scoped to
what a bot needs (push a branch, open/refresh/close one MR — never merge) and can
be revoked without taking PR-Agent or `task gitlab:deploy` down with it.

Rotate: mint a replacement (Settings → Access Tokens), update this field **and**
the CI variable, revoke the old token, then verify by playing `version-bump-bot`
from a web pipeline on `main` (docs/13 § Version bump bot).

#### GitLab Registry Cache Deploy Token

A GitLab **deploy token** (not a personal or runner token) scoped
`read_registry` on the `eric/weisssrv` project, minted at deploy time under
Project → Settings → Repository → Deploy tokens. ESO maps it to
`REGISTRY_PROXY_USERNAME` / `REGISTRY_PROXY_PASSWORD` for the CI pull-through
registry cache (`kubernetes/apps/registry-cache`, docs/27). `read_registry` is
least privilege — the cache only pulls upstream blobs. Rotate: create a new
deploy token, update both fields, then `task flux:rotate-secret -- registry-cache`.

A missing or revoked item is not silent. Both fields land in the Deployment as
`secretKeyRef` env vars, so the pod never starts and `RegistryCacheDown` fires
15 minutes later and keeps firing. If the credential has to be away for a while,
silence that alert or drop `- registry-cache` from
`kubernetes/apps/kustomization.yaml` until the item is back.

#### CI Cache Garage

The Garage S3 backend for the GitLab runner cache
(`kubernetes/apps/ci-cache`). Garage imports the `access-key` / `secret-key`
pair from `GARAGE_DEFAULT_*` on **first boot**, so this item is the single
source for the server and for the runner-side `s3access` copies in both runner
namespaces. `rpc-secret` is the loopback RPC secret, `admin-token` the admin
API, and `metrics-token` the bearer the ci-cache ServiceMonitor uses — that last
one also lands in `observability-exporter-secrets`, where a missing property
fails the whole Secret sync and takes every other exporter credential with it.

Rotate: update all five fields, then run the task. It force-syncs the four
ExternalSecrets and replaces the consuming pods.

```bash
task flux:rotate-secret -- ci-cache
```

Replacing the ci-cache pod is what re-imports the rotated key pair: `Recreate`
plus an emptyDir gives the new pod an empty metadata dir. The cached artifacts
are lost and the next pipeline runs cold, by design. Prometheus reads the
metrics bearer from its mounted Secret via the config-reloader, so nothing there
is restarted.

#### Homarr SSO

`client-secret` is read by **both** ESO (the `homarr-secrets` ExternalSecret →
`oidc-client-secret`) and `terraform/authentik`
(`TF_VAR_oauth2_client_secret_homarr`, docs/40), so the two can never disagree.
`secret-encryption-key` (`openssl rand -hex 32`) is ESO-synced and encrypts the
SQLite-stored integration credentials — **do not lose it**, or those stored
credentials become unreadable.

`admin-username` / `admin-password` are operator-set, not ESO-injected: a DR
convenience no auth path consumes. Break-glass DR mints its own username and
one-time password via `homarr-cli recreate-admin` (docs/41 § SSO).

#### NZBGet

The pair MUST match `nzbget.conf`'s `ControlUsername` / `ControlPassword` —
NZBGet validates HTTP Basic against them. `terraform/authentik` consumes the same
item (`TF_VAR_basic_auth_nzbget_*`) as the injected credentials on the NZBGet
proxy provider (docs/21 § Authentik SSO Integration). Rotating the pair means
changing it in NZBGet (Settings → Security) **and** here, then a supervised
terraform apply (docs/40).

#### VPN Unlimited Credentials

The item holds four fields: `openvpn-user`, `openvpn-password`,
`openvpn-clientcrt` and `openvpn-clientkey`. Gluetun's generated OpenVPN config
is cert/key-based (auth-user-pass off), but its settings validation still
requires a non-empty user and password for the provider.

This item is an unwired spare. Privado is the only provider the download clients
can select today, so nothing reads these four fields. Wiring a second provider
takes the five edits listed in
[docs/16](16-next-steps.md) § Open work, "A second download-client VPN provider".

#### Download Client API Keys

The four \*arr keys come from each app's Settings → General.
`gluetun-control-apikey` is the auth key for the Gluetun control server
(`openssl rand -hex 32`); it is rendered into the `gluetun-control-auth` Secret's
`config.toml` and consumed by the gluetun-exporter as `GLUETUN_APIKEY`.

Rotating `gluetun-control-apikey` requires re-syncing the `gluetun-control-auth`
ExternalSecret **and then** restarting the pods: ESO only re-fetches on its 24h
`refreshInterval`, and Reloader ignores Secret changes (`ignoreSecrets: true`), so
a bare pod restart would re-read the stale key and the rotation would silently
no-op. Use `task flux:rotate-secret -- downloads` (force-syncs both
`gluetun-control-auth` and `vpn-credentials`, then restarts nzbget/qbittorrent).
See `kubernetes/apps/download-clients/README.md` § Control-Server Auth.

#### Hermes Secrets

Five ESO-consumed fields — `api-server-key`, `claude-code-oauth-token`,
`discord-bot-token`, `hermes-dashboard-oidc-client-secret`, `hass-token`. A
missing one fails the whole `hermes-secrets` sync.

- `api-server-key` — the gateway API server's mandatory bearer key (≥32 random
  bytes, `openssl rand -hex 32`).
- `claude-code-oauth-token` — the Claude Code delegate's Max-subscription
  `claude setup-token` value (docs/37 § Coding delegates).
- `discord-bot-token` and `hass-token` are upserted into `/opt/data/.env` by the
  init container (docs/37 § Gateway platform config).
- `hermes-dashboard-oidc-client-secret` is read by both ESO and
  `terraform/authentik` (docs/40) so they cannot disagree.

The item also holds `dashboard-username` / `dashboard-password` /
`dashboard-session-secret` as **unsynced** emergency-revert values for the
retired `basic` password provider; dashboard auth is OIDC-only (docs/37 § SSO).

There is no LLM-provider API key here: Hermes' LLM turns use ChatGPT-subscription
OAuth in Hermes' own store, and the codex CLI keeps its token in `CODEX_HOME`
(docs/37 § LLM engine).

#### Hermes Agent 1P Service Account

A 1Password service-account token consumed as `OP_SERVICE_ACCOUNT_TOKEN`. It is a
**dedicated** item, separate from **Hermes Secrets**, so the service account's
blast radius and rotation stay isolated. ESO syncs it into the `hermes-secrets`
Secret as `op-service-account-token`; the Deployment sets it as gateway container
env (so the baked-in `op` CLI authenticates), and the init container also upserts
it into `/opt/data/.env`. It backs Hermes' first-party 1Password skill (docs/37 §
1Password).

A service account authenticates to the 1Password **cloud** API
(`*.1password.com`), not the in-cluster Connect that backs ESO — so scope it in
the service-accounts console to **only** the isolated Agent vault (read+write),
never Homelab. Rotate by minting a replacement token, updating this field, then
revoking the old one.

#### Hermes Registry Pull

A GitLab **deploy token** named `hermes`, scoped `read_registry` on
`eric/weisssrv`. ESO builds a `kubernetes.io/dockerconfigjson` Secret
(`hermes-registry-pull`) for `registry.git.esweiss.com` from the `token` field,
used by the Hermes pod's `imagePullSecrets` (docs/37). Only `token` is synced —
the username is a stable literal in the ExternalSecret template, because the
Connect provider does not resolve a LOGIN item's primary username field.

Rotate: create a replacement deploy token with the same name and scope, update
`token`, then `task flux:refresh-secret -- hermes/hermes-registry-pull` and
revoke the old token.

#### Uptime Kuma

Operator-chosen **before** the app is deployed, then typed verbatim into Kuma's
first-run setup form (docs/45). They are one credential doing two jobs: Kuma's
only login, and the HTTP Basic pair Prometheus scrapes `/metrics` with — Kuma
protects that endpoint with the admin account while its "API Keys" feature is
off. So a password change is a two-step rotation: change it in Kuma's UI, update
this item, then let ESO re-sync (`observability-exporter-secrets` →
`uptime-kuma-username`/`uptime-kuma-password`) or force it with
`task flux:rotate-secret -- observability-exporters` (Prometheus reads the
mounted Secret through its config-reloader, so it needs no restart). Enabling
API Keys in Kuma switches `/metrics` to key-only auth and the scrape 401s until
`admin-password` is replaced with the key.

Both fields must exist before the manifests merge: they join the shared
`observability-exporter-secrets` ExternalSecret, and a missing property fails
that whole Secret sync.

#### Home Assistant API Token

`token` is a long-lived access token for HA's `/api/prometheus` endpoint.
`backup_encryption_key` is HA's backup encryption key — automatic backups are
`protected: true`, so **without this key the offsite HA tars in B2 are
undecryptable**. Take it from Settings → System → Backups → emergency kit, and
re-save it here if HA ever regenerates it.

#### Nextcloud Secrets

- `admin-password` — break-glass local admin, reachable at `/login?direct=1`.
- `postgres-password` — the `nextcloud` DB role.
- `serverinfo-token` — random ≥32 chars; the token the nextcloud-exporter
  authenticates to Nextcloud's serverinfo API with. The role sets it via
  `occ config:app:set serverinfo token`.

#### B2 Archive Backup

Two key pairs (a deliberate capability split, docs/42) plus the repo password:

- `b2_key_id` / `b2_application_key` — the **full bucket-settings key** (all
  bucket read/write capabilities including `readBucketRetentions`, which the
  Terraform provider needs to avoid phantom drift). Consumed only by
  `scripts/b2-bucket-drift.py` (the `b2-drift-plan` CI job and supervised
  `task b2:apply`).
- `restic_key_id` / `restic_application_key` — the **restricted key**
  (`listBuckets,listFiles,readFiles,writeFiles,readBucketEncryption`, no
  `deleteFiles`; rclone deletes by hiding and the B2 lifecycle expires hidden
  versions). Consumed by the `restic_offsite` role on pve-nas-01.
- `restic_repo_password` — `RESTIC_PASSWORD`. **Losing it loses the entire restic
  repo**; keep an offline copy outside this vault, and never change it once the
  repo exists.

Rotate the bucket-settings key in the Backblaze console (bucket-scoped, all
read+write bucket capabilities); rotate the restricted key with
`b2 key create --bucket weisssrv-backup ...` (docs/42), update the `restic_*`
fields, and re-run `deploy-ansible-storage`. Restic keeps working throughout — it
never issues a real delete.

## Kubernetes workloads (External Secrets Operator)

For any secret consumed inside the k3s cluster (every `ExternalSecret` in
`kubernetes/apps/*/externalsecret.yaml` and
`kubernetes/infrastructure/configs/shared-cloudflare-secrets/`):

```bash
# 1. Update the 1Password item (web/desktop app or CLI).
eval $(op signin)   # if needed
# op item edit <item-id> <field>[password]=<new-value>

# 2. Refresh ExternalSecret + restart consuming pods in one go.
#    Known apps (the task's own `--` dispatch; run it with no argument to have
#    it print the current list):
#      authentik, ci-cache, downloads, hermes, homarr, recipes, gitlab-runner,
#      gitlab-runner-privileged, gitlab-agent, registry-cache, wg-easy,
#      observability-exporters
task flux:rotate-secret -- authentik

# 3. (Optional) Refresh an ExternalSecret without restarting pods — useful
#    for secrets that don't require a pod restart to take effect.
task flux:refresh-secret -- authentik/authentik-secrets
```

The `task flux:rotate-secret -- <app>` command annotates the ExternalSecret
with a force-sync timestamp, waits for ESO to re-fetch from 1Password, then
restarts the Deployments/StatefulSets that consume the produced Secret. The
per-app dispatch (which ExternalSecrets it force-syncs and which workloads it
rolls) lives in `scripts/flux-rotate-secret.sh`, which the task runs, and that
script is the source of truth; running the task with no argument prints the
current app list. See `docs/29-flux-operations.md` § Rotating a Secret for the surrounding
procedure and § Rate Limits for the 1Password read budget.

The single exception is the Connect bootstrap secrets
(`external-secrets/op-credentials` and `external-secrets/onepassword-connect-token`)
— these are what ESO and 1Password Connect use to talk to 1Password itself.
Rotate by regenerating the Connect server credentials and token, then
recreating the k8s secrets manually (see `task flux:bootstrap-onepassword`
for instructions). The old ExternalSecret machinery can't self-rotate its
own auth source.

## Host-side / Ansible-managed credentials

### SMTP Passwords

**Location**: 1Password → Homelab vault

**Items to Update**:
- `SMTP Relay Gmail` - Gmail app password (smtp-relay → Gmail)
- `SMTP Relay Auth` - Relay user password (null clients → smtp-relay)

**Rotation Procedure**:

```bash
# 1. Update password in 1Password (web/desktop app)
# 2. Ensure 1Password CLI is signed in
eval $(op signin)

# 3. Verify new password is readable
op read "op://Homelab/SMTP Relay Gmail/password"
op read "op://Homelab/SMTP Relay Auth/password"

# 4a. Relay + the null clients site.yml carries (proxmox + dns)
task infra:deploy -- --tags smtp_relay,postfix_null_client

# 4b. The app VMs and k3s nodes — site.yml does NOT carry them, and `--tags`
#     against their own playbooks rotates nothing (see "Why --tags" below)
task mail:rotate-credential

# 4c. GitLab stores the credential a SECOND time, in /etc/gitlab/gitlab.rb.
#     Only the gitlab role templates it, and it must run untagged so the role
#     re-templates gitlab.rb and reconfigures.
task gitlab:deploy

# 5. Verify mail still works
ssh eric@10.0.10.102 "echo 'Test' | mail -s 'Rotation Test' root"

# Check mail arrives at your root_email_alias
```

Steps 4a-4c are the sequence that actually rotates every consumer; `site.yml`
alone leaves most of the fleet on the revoked password. Use the `task` wrappers,
not bare `ansible-playbook` — the credential reaches Ansible only through the
`op run` env injection they carry. A host that is knowingly out (hardware work)
belongs in `deploy_expected_absent_hosts` (`group_vars/all.yml`), which
downgrades 4a's reachability gate to a loud warning; 4b has no such gate and
fails on an unreachable host by design, since a host silently left on the
revoked credential is the failure being avoided.

**What Happens**:
- Gmail password: `/etc/postfix/sasl_passwd` updated on smtp-relay, postmap rebuilds hash
- Relay user password: SASL database (`/etc/sasldb2`) updated on smtp-relay.
  `saslpasswd2` reruns on every deploy, but it reports changed (and reloads
  postfix) only on an actual rotation — sasldb stores no recoverable
  plaintext, so the role detects rotation via a sha256 fingerprint sentinel
  at `/etc/postfix/.sasl_relay_user.sha256` (root:root, 0600)
- Null client passwords: `/etc/postfix/sasl_passwd` updated on all Proxmox hosts
  and DNS LXCs by 4a, on the app VMs and k3s nodes by 4b
- Postfix reloads on all affected hosts
- Both roles assert the compiled `sasl_passwd.db` / `aliases.db` against their
  source on every run and rebuild on a mismatch, rather than relying on the
  `notify` handler alone: a play that dies before `flush_handlers` would
  otherwise leave a correct source next to a stale database, and the host would
  keep authenticating with the old credential with nothing reporting changed

**Affected Hosts**:
- `smtp-relay` - Both passwords
- `pve-nas-01`, `pve-laptop-01`, `pve-opt-01`, `pve-opt-02`, `pve-opt-03`, `pve-prec-01` - Relay auth password
- `dns-01`, `dns-02` - Relay auth password
- `plex`, `gitlab`, `immich`, `immich-ml`, `nextcloud` - Relay auth password
- every k3s node (server + agent) - Relay auth password

**Why `--tags` cannot do this on its own.** `site.yml --tags
smtp_relay,postfix_null_client` covers ONLY `proxmox` and `dns` — the two plays
that tag the role. The app VMs and k3s nodes get `postfix_null_client` from
their own playbooks (`gitlab.yml`, `plex.yml`, `immich.yml`, `immich-ml.yml`,
`nextcloud.yml`, `k3s.yml`), where the role is listed **untagged**, so
`--tags postfix_null_client` selects none of its tasks. Verified with
`--list-tasks` on each; the two outcomes differ, and the silent one is worse:

| Playbook | `--tags postfix_null_client` selects | Outcome |
|---|---|---|
| `plex.yml`, `immich.yml`, `immich-ml.yml`, `nextcloud.yml`, `gitlab.yml` | only base's two `tags: [always]` fact detections | **Fails.** Those plays are `gather_facts: false` with an untagged gathering pre-task, so `Detect if running in a VM` templates `ansible_facts` that were never gathered |
| `k3s.yml` | the same two tasks | **Exits 0 having rotated nothing.** The play gathers facts by default, so the always-tasks succeed and the run reports success with zero postfix tasks executed |

So: run `task mail:rotate-credential`
(`ansible/playbooks/rotate-mail-credential.yml` — the role, and only the role,
on exactly those hosts), then `task gitlab:deploy` for gitlab.rb. Running the
app playbooks untagged also works but reconfigures the whole app.
`scripts/test_mail_credential_rotation.py` fails if the rotation playbook's host
pattern drifts from the set of playbooks that carry the role.

**Beyond Ansible** — the same 1Password item feeds consumers no playbook touches:

| Consumer | Path | Rotation step |
|---|---|---|
| Authentik | ESO -> `authentik-secrets` (`smtp-username`/`smtp-password`) | restart `authentik-server` + `authentik-worker` |
| Mealie + Bar Assistant | ESO -> `recipes-secrets` (shared) | restart both deployments |
| Alertmanager | ESO -> `alertmanager-config`, templated into `alertmanager.yaml` | none - its config-reloader sidecar picks it up |
| Home Assistant | **UI config entry** (`smtp_notify`), stored in HA's `.storage` | **manual, in the HA UI** - no automation reaches it |

Reloader sets `ignoreSecrets: true` (docs/29), so a refreshed ExternalSecret does
NOT roll the pods that consume it. Force the sync and restart by hand:

```bash
kubectl -n <ns> annotate externalsecret <name> force-sync="$(date +%s)" --overwrite
kubectl -n <ns> delete pod -l <selector>   # delete, not `rollout restart`:
                                           # kustomize-controller reverts the annotation
```

---

### SSH Keys

**Location**: 1Password → Homelab vault → SSH Key item

**Rotation Procedure**:

```bash
# 1. Generate new key pair
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com" -f ~/.ssh/id_ed25519_new

# 2. Update BOTH halves in 1Password
#    public key  <- ~/.ssh/id_ed25519_new.pub
#    private key <- ~/.ssh/id_ed25519_new
#    fingerprint <- ssh-keygen -lf ~/.ssh/id_ed25519_new.pub

# 3. Verify the item holds a matching pair
op read "op://Homelab/SSH Key/public key"
op read "op://Homelab/SSH Key/private key" | ssh-keygen -y -f /dev/stdin
#    ^ must print the same public key as the line above

# 4. Deploy new key to all hosts
task infra:base -- --tags ssh

# 5. Test SSH with new key (before removing old one!)
ssh -i ~/.ssh/id_ed25519_new eric@10.0.10.102

# 6. Once verified, replace old key
mv ~/.ssh/id_ed25519_new ~/.ssh/id_ed25519
mv ~/.ssh/id_ed25519_new.pub ~/.ssh/id_ed25519.pub

# 7. Update SSH agent
ssh-add -D
ssh-add ~/.ssh/id_ed25519

# 8. Revoke the old key — the additive deploy in step 4 never removes it
ansible -i ansible/inventories/prod 'all:!home:!windows' -b -m ansible.posix.authorized_key \
  -a "user=eric key='ssh-ed25519 <OLD-PUBKEY>' state=absent"
```

**What Happens**:
- `authorized_keys` is deployed **additively**
  (`base_ssh_authorized_keys_exclusive` defaults to `false`). Updating the
  1Password item and redeploying ADDS the new key and revokes nothing — the old
  key keeps working on every host until step 8 removes it.
- CI deploy jobs read `op://Homelab/SSH Key/private key` (the shared
  Ansible-deploy `before_script`). Confirm the next `main` pipeline's deploy
  stage is green before running step 8.

**Do not** set `base_ssh_authorized_keys_exclusive: true` to force revocation.
The `acme_certs` cert-distribution key lives in the same `eric`
`authorized_keys` on every distribution target, and exclusive mode strips it,
breaking cert pushes until `acme_certs` re-seeds it.

Re-provisioning a guest strands the same key for the same reason: the guest
playbook runs `weisssrv.infra.base`, which rewrites `authorized_keys`. Each
cert-target guest playbook (`plex.yml`, `immich.yml`, `nextcloud.yml`,
`gitlab.yml`, `mail.yml`) therefore ends with a play on the `dns_primary`
group that includes `tasks/_reseed-cert-target.yml`, re-pinning that target
from the cert authority; `scripts/test_cert_reseed_coverage.py` fails a
playbook that applies base to a target without it. A guest that is down at
re-seed time is named in the playbook output; re-run it, or
`task infra:deploy -- --tags acme_certs`, once it is back.
Without the re-seed the loss is invisible until the next renewal fails.

The pubkey the re-seed pins comes from `DNS01_SSH_PUBLIC_KEY` when the job
carries it, and otherwise from `id_ed25519_certs.pub` on the authority host,
which `acme_certs` wrote there. A deploy job that does not pass the env variable
therefore still re-seeds; the loud skip in the playbook output means both
sources were empty, and `task infra:deploy -- --tags acme_certs` (under `op
run`) rewrites the key file.

**Surfaces step 8 does not reach**: HAOS (.154) keeps the key in
`/root/.ssh/authorized_keys` over port 22222 — remove it through the Files
add-on (docs/24). The LXC guests' file is additionally rewritten wholesale by
`proxmox_lxc` on the next deploy. The `!home:!windows` exclusion is load-bearing:
both are `ansible_connection: local`, so a bare `ansible all` would edit the
control laptop's own file.

**Affected Hosts**: All (proxmox, dns, mail)

---

### Cloudflare DNS Token (in-cluster + acme_certs)

**Location**: 1Password → Homelab vault → Cloudflare DNS Token

**Rotation Procedure**:

```bash
# 1. Generate new token in Cloudflare dashboard
# Scope: Zone:DNS:Edit, Zone:Zone:Read (nothing more — this item is
# deliberately unable to touch zone-wide settings)

# 2. Update in 1Password
# Update both credential and username (account ID) if needed

# 3. Verify new token is readable
op read "op://Homelab/Cloudflare DNS Token/credential"
```

**What Happens**:
- In-cluster consumers (cert-manager DNS-01, external-dns, cloudflare-ddns)
  pick up the new token on the next ESO refresh (default: 24h). To rotate
  immediately across all three namespaces, force refresh each ExternalSecret:
  ```bash
  task flux:refresh-secret -- cert-manager/cloudflare-api-token
  task flux:refresh-secret -- external-dns/cloudflare-api-token
  task flux:refresh-secret -- cloudflare-ddns/cloudflare-api-token
  ```
- acme.sh on dns-01 renews from the copy it saved at first issuance
  (`SAVED_CF_Token` in `/root/.acme.sh/account.conf`), so a rotation must
  re-run the issuance command in [docs/09](09-certs.md) with the new values
  or the next renewal fails.
- Old token can be revoked in Cloudflare after verification.

**Affected Systems**: cert-manager, external-dns, cloudflare-ddns (all three
read it via ESO from 1Password), plus acme_certs on dns-01. Terraform is
**not** affected — it uses the separate token below.

---

### Cloudflare Terraform Token

**Location**: 1Password → Homelab vault → Cloudflare Terraform Token

**Rotation Procedure**:

```bash
# 1. Generate new token in Cloudflare dashboard
# Scope: Zone:Zone:Read, Zone:DNS:Edit, Zone:Zone Settings:Edit
# (Zone Settings:Edit is required for cloudflare_zone_settings_override)

# 2. Update in 1Password (credential; username holds the account ID)

# 3. Verify new token is readable
op read "op://Homelab/Cloudflare Terraform Token/credential"

# 4. Test with Terraform
task terraform:cloudflare-plan

# 5. If plan succeeds, token is valid
# No deployment needed - Terraform reads at runtime
```

**Affected Systems**: Terraform only — the Taskfile `terraform:*` wrappers
and the `terraform-plan` / `deploy-terraform` CI jobs. No in-cluster or
host-side consumer reads this item.

**Artifact exposure**: the `terraform-plan` job artifact
(`terraform/cloudflare/tfplan`) stores every `TF_VAR` value in the clear, so
read access to it is token-equivalent. The artifact is `access: none`, so only
the pipeline itself can read it — `deploy-terraform` via `needs:artifacts`.

---

### AdGuard Home Password

**Location**: 1Password → Homelab vault → AdGuard Home

**Fields Required**:
- `password` - Plaintext password (for login and API config)

The `adguard_home` role generates the bcrypt hash in `AdGuardHome.yaml` from
this plaintext password at deploy time (passlib on the target host), and only
regenerates it when the existing hash no longer verifies. There is **no**
separate `password_hash` field to maintain — rotating the plaintext password is
sufficient.

**Rotation Procedure**:

```bash
# 1. Update the 'password' field in 1Password with the new plaintext password.
eval $(op signin)
op read "op://Homelab/AdGuard Home/password"   # verify the new value reads back

# 2. Deploy AdGuard configuration. The role regenerates the bcrypt hash in
#    AdGuardHome.yaml from the new password (idempotent: it only rewrites the
#    hash when the existing one no longer verifies).
task dns:deploy

# 3. Verify deployment
task infra:verify -- --limit dns-01,dns-02

# 4. Test login with new password
open http://10.0.10.150:3000
# Login with username: eric, new password from step 1
```

**What Happens**:
- AdGuard Home admin password hash updated in `AdGuardHome.yaml` on both dns-01 and dns-02
- AdGuard Home service restarted on both hosts
- Configuration synced from dns-01 to dns-02 automatically via adguardhome-sync

**Affected Hosts**: `dns-01`, `dns-02`

**Security Notes**:
- Each rotation generates a new unique hash, salted by bcrypt
- The role hashes on the target host, so the plaintext reaches it through the
  `op run` env and is never written to disk
- The hash is validated (must start with `$2a$`, `$2b$`, or `$2y$`) before
  deployment

---

### Samba NAS User Password

**Location**: 1Password → Homelab vault → Samba NAS User

**Rotation Procedure**:

```bash
# 1. Update password in 1Password

# 2. Verify new password is readable
op read "op://Homelab/Samba NAS User/password"

# 3. Deploy storage configuration
task storage:deploy

# 4. Test Samba access
smbclient //10.0.10.102/share -U nas
# Enter new password when prompted
```

**What Happens**:
- Samba password updated using `smbpasswd` command
- Password change takes effect immediately
- No service restart required

**Affected Hosts**: `pve-nas-01`

---

### Tailscale Auth Key

**Location**: 1Password → Homelab vault → Tailscale Auth Key

**Notes**:
- Auth keys are one-time use for initial registration
- Rotating the key in 1Password only affects NEW nodes
- Existing nodes remain authenticated with their own node keys
- To re-authenticate existing nodes, you must:
  1. `sudo tailscale down`
  2. `sudo tailscale up --auth-key=<new-key>`

**Rotation Procedure** (for adding new nodes):

```bash
# 1. Generate new auth key in Tailscale admin console
# Settings → Keys → Generate auth key
# Check: Reusable, Ephemeral (optional)

# 2. Update in 1Password

# 3. Verify new key is readable
op read "op://Homelab/Tailscale Auth Key/credential"

# 4. New nodes will automatically use new key on deployment
task infra:deploy -- --tags tailscale --limit <new-host>
```

**Affected Hosts**: Only new hosts being provisioned

---

## Emergency Rotation (Compromised Credential)

If a credential is compromised, rotate immediately:

### Quick Rotation Checklist

```bash
# 1. Update secret in 1Password immediately
# 2. Sign in to 1Password CLI
eval $(op signin)

# 3. Deploy to all affected hosts
task infra:deploy -- --limit <affected-hosts>

# 4. For Proxmox firewall or network-level security:
task infra:deploy -- --tags proxmox_firewall

# 5. Revoke old credential at source (Cloudflare, Tailscale, Gmail, etc.)
```

### SSH Key Compromise

```bash
# 1. Generate new key immediately
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com" -f ~/.ssh/id_ed25519_emergency

# 2. Update in 1Password

# 3. Deploy to ALL hosts immediately
task infra:base -- --tags ssh

# 4. Verify emergency key works
ssh -i ~/.ssh/id_ed25519_emergency eric@10.0.10.102

# 5. Remove compromised key from the laptop and 1Password (this does not touch
#    the hosts — step 6 is what revokes it there)

# 6. Revoke the compromised key on every managed host
ansible -i ansible/inventories/prod 'all:!home:!windows' -b -m ansible.posix.authorized_key \
  -a "user=eric key='ssh-ed25519 <COMPROMISED-PUBKEY>' state=absent"

# 7. Check all hosts for unauthorized access
ansible -i ansible/inventories/prod 'all:!home:!windows' -m shell -a "last -20"
```

A host that is unreachable for step 6 is not revoked. Remove the key from its
console before treating the incident as closed, and remove it from HAOS through
the Files add-on (docs/24).

---

## Verification After Rotation

### SMTP

```bash
# Test mail relay
ssh eric@10.0.10.102 "echo 'Test after rotation' | mail -s 'Test' root"

# Check smtp-relay logs
ssh eric@10.0.10.151 "sudo tail -f /var/log/mail.log"

# Verify authentication
ssh eric@10.0.10.151 "sudo grep sasl /var/log/mail.log | tail -20"
```

### SSH

```bash
# Test all hosts
ansible -i ansible/inventories/prod all -m ping

# List the key fingerprints each host accepts — confirm the new one is present
# and the revoked one is gone (a line count proves neither)
ansible -i ansible/inventories/prod 'all:!home:!windows' -b -m shell -a "ssh-keygen -lf ~eric/.ssh/authorized_keys"
```

### DNS/AdGuard

```bash
# Test login
curl http://10.0.10.150:3000

# Check sync status (if password changed)
ssh eric@10.0.10.150 "sudo systemctl status adguardhome-sync"
```

---

## Scheduled Rotation Policy

The table below is the policy *and* the input `scripts/check-credential-age.py`
reads. The first column holds backticked 1Password item titles, `*` and `?`
glob; the most specific pattern that matches a title owns it, a literal beating
a glob. The second column is `<N>d` or `exempt`.

| Item (1Password title, glob) | Max age | Reason |
|---|---|---|
| `*` | 180d | Default for a scoped token, password or key pair |
| `*SSH Key*` | 365d | Low risk, high impact if rotated incorrectly |
| `AdGuard Home` | 365d | Low exposure, local network only |
| `WiFi *` | 365d | A PSK rotation re-onboards every device on that SSID — see § WiFi SSID pre-shared keys |
| `ZFS Pool * Passphrase` | 730d | Offline unlock material, and a rotation re-keys the pool |
| `B2 Archive Backup` | 365d | Holds the restic repository password, rotated against the offsite repo itself |
| `Email Config` | exempt | A mail alias, not a credential |
| `Authentik User Identities` | exempt | Managed-user names and emails, not a credential |
| `Tailscale Auth Key` | exempt | Minted per node and consumed at join, so the item holds no standing credential |

**Measurement.** `task secrets:age` lists every `Homelab` item with its
`updated_at` and flags the ones past the age above. It reads titles and
timestamps only, never a field value, and exits 1 on a finding. `Homelab-Boot`
and `Homelab-Admin` are separate grants, so they are opt-in:
`python3 scripts/check-credential-age.py --vault Homelab --vault Homelab-Boot`.
The `credential-age` CI job runs the default scope on the scheduled pipeline as
an advisory check — the clock moves without a commit, so no merge gate can catch
this. Rotating is still owner work; each item's procedure is § Item detail.

---

## Troubleshooting

Alerts: ExternalSecretSyncFailure, OnePasswordConnectDown.

### "Permission denied" after rotation

**Cause**: New credential not deployed or wrong format

**Fix**:
```bash
# Verify credential in 1Password
op read "op://Homelab/Item/field"

# Re-run deployment with verbose output
task infra:deploy -- -vv

# Check specific host
ansible -i ansible/inventories/prod host -m setup
```

### Mail stops working after SMTP rotation

**Cause**: Password mismatch or postmap not run

**Fix**:
```bash
# Check sasl_passwd on smtp-relay
ssh eric@10.0.10.151 "sudo cat /etc/postfix/sasl_passwd"

# Manually rebuild hash
ssh eric@10.0.10.151 "sudo postmap /etc/postfix/sasl_passwd && sudo systemctl reload postfix"

# Check logs
ssh eric@10.0.10.151 "sudo journalctl -u postfix -n 50"
```

### Terraform fails after Cloudflare rotation

**Cause**: Old token cached or wrong permissions

**Fix**:
```bash
# Clear environment
unset CLOUDFLARE_API_TOKEN
unset CLOUDFLARE_ACCOUNT_ID

# Re-read from 1Password
eval $(op signin)

# Retry using Taskfile (preferred - handles all env vars)
task terraform:cloudflare-plan

# Or manually export and retry
# CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare Terraform Token/credential")
# export CLOUDFLARE_API_TOKEN
# cd terraform/cloudflare && terraform plan
```

---

## Drive Decommission / RMA

When a drive leaves the building (RMA to vendor, sold, donated, or
disposed), the data on it is the credential that has to be rotated.
ZFS-native encryption (`docs/32-zfs-encryption.md`) plus this procedure
turn a worst-case "stolen disk" exposure into a zero-data-loss event.

### Decision tree

| Situation | Procedure |
|---|---|
| Drive was a member of an **encrypted** ZFS pool, and the pool's passphrase has not been rotated since | (a) Rotate the pool passphrase first, then (b) follow either Quick or Full procedure below |
| Drive was a member of an **unencrypted** pool (e.g. `tank/media`, or `archive` whose pool metadata is plaintext) | Full procedure required — wipe regardless of dataset-level raw encryption |
| Drive holds no live data (cold spare, never had data) | None — verify with `zdb -l <device>` and SMART; ship as is |

### Quick procedure (encrypted pool, working drive)

Used when the drive still responds to commands and the pool was
encrypted at the time the drive held data.

```bash
# 1. Confirm pool encryption was active for this drive's lifetime
sudo zfs get encryption,creation <pool>
# encryption should be aes-256-gcm; creation should pre-date drive insertion

# 2. Rotate the passphrase BEFORE the drive leaves. Generate it in 1Password
#    first, then read it back to paste at the prompts — the passphrase must
#    never appear on a command line or in shell history on the NAS.
op item edit --vault Homelab-Boot "ZFS Pool <pool> Passphrase" \
  --generate-password='letters,digits,symbols,48'
op read "op://Homelab-Boot/ZFS Pool <pool> Passphrase/passphrase"

#    Each dataset is its own encryption root (Model B) and the plaintext pool
#    root is NOT a key holder, so change-key every encryption root in the pool —
#    `zfs change-key <pool>` alone fails ("not an encryption root"). See
#    docs/32-zfs-encryption.md § Architecture.
for root in $(zfs get -H -t filesystem,volume -o name,value -r encryptionroot <pool> \
                | awk -F'\t' '$1==$2{print $1}'); do
  sudo zfs change-key -o keyformat=passphrase -o keylocation=prompt "$root"  # paste new passphrase
done

# 3. Issue ATA Secure Erase (SATA SSD/HDD) or NVMe sanitize
#    Pick the right command for the device class:

# SATA SSD - ATA Secure Erase (instant on most modern SSDs):
sudo hdparm --user-master u --security-set-pass weisssrv /dev/sdX
sudo hdparm --user-master u --security-erase weisssrv /dev/sdX

# SATA HDD - blkdiscard if device supports trim, else dd:
sudo blkdiscard -v /dev/sdX  # falls back gracefully if not supported

# NVMe - sanitize (fastest, hardware-level):
sudo nvme sanitize /dev/nvmeX --sanact=2  # 2 = block erase
sudo nvme sanitize-log /dev/nvmeX  # poll until SSTAT bit 0 = 0 (idle)
```

### Full procedure (unencrypted data ever lived on this drive)

Used when the drive ever held plaintext (any pre-Phase-3 drive on
`tank/media`, `archive`, etc.):

```bash
# 1. Detach from the pool if still attached
sudo zpool offline <pool> /dev/disk/by-id/<id>
sudo zpool detach <pool> /dev/disk/by-id/<id>      # mirrors only
sudo zpool replace <pool> <old-id> <new-id>        # raidz: requires resilver to a new drive

# 2. Wait for resilver (raidz) - hours to days for tank-class drives
sudo zpool status <pool>

# 3. Wipe in this order (each one is best-effort; combine for paranoia):
#    a. ATA Secure Erase / NVMe sanitize as above (fastest if supported)
#    b. Single-pass urandom overwrite (covers blocks the FW reallocated)
sudo dd if=/dev/urandom of=/dev/sdX bs=1M status=progress
#    For 22 TB drives this takes ~24 hours at 250 MB/s. Plan accordingly.

# 4. Verify wipe
sudo dd if=/dev/sdX of=- bs=1M count=1024 status=progress | \
    hexdump -C | head -200
# Expect entirely zeros (after secure erase) or garbage (after urandom);
# any recognizable filesystem signature means the wipe failed.
```

### After wipe, before shipping

- Photograph the SMART attributes (`smartctl -A /dev/sdX > smart-<serial>.txt`)
  so you have a record if the RMA dispute requires it.
- Update the inventory in `host_vars/<host>.yml` (smartd_*_disks
  lists, `vm_additional_disks` if applicable).
- Note the disposal date + serial in the related 1Password item or
  Linear/issue tracker.

### Drives that won't wipe (controller fault, dead heads)

If `hdparm`, `dd`, and `nvme sanitize` all fail:

1. **If encrypted**: the drive is already opaque. Confirm by attempting
   to import in a separate test pool with no key — should fail. Ship.
2. **If unencrypted**: physical destruction is the only safe path.
   Drill at least four holes through the platter stack for HDDs,
   physically destroy NAND chips for SSDs/NVMe. Don't ship.

## Best Practices

1. **Test before revoking**: Always verify new credential works before revoking old one
2. **One at a time**: Rotate one credential type at a time to isolate issues
3. **Document**: Note rotation date and reason in 1Password item notes
4. **Verify**: Always run verification checks after rotation
5. **Backup**: Keep old credentials in 1Password archive for 30 days before deleting
6. **Audit logs**: Check logs after rotation for failed auth attempts

---

## Related documentation

- [02-install.md](02-install.md) - Initial 1Password setup
- [03-ssh-users.md](03-ssh-users.md) - SSH key management
- [10-mail.md](10-mail.md) - SMTP relay configuration
