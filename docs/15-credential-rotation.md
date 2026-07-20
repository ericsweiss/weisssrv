# Credential Rotation Guide

This document explains how to rotate passwords, API tokens, and SSH keys stored in 1Password.

## Overview

All secrets are stored in 1Password and injected at use time. There are two
consumer paths and they rotate differently:

- **Kubernetes workloads** (Authentik, download clients, recipes, GitLab
  runners / agent, cert-manager DNS-01 token, etc.) consume 1Password via
  the External Secrets Operator (ESO). Rotate via `task flux:rotate-secret
  -- <app>` — see § "Kubernetes workloads (External Secrets Operator)"
  below and `docs/29-flux-operations.md` for the full day-2 playbook.
- **Host-side / Ansible-managed state** (SMTP, SSH keys, acme.sh, Tailscale
  auth keys, GitLab VM root password, etc.) uses `op run --` to inject
  secrets at playbook-run time. Rotate by updating 1Password, then
  re-running the relevant Ansible playbook.

## Required 1Password Items

This is the canonical, authoritative inventory of every item the deployment
expects in the **Homelab** vault. CLAUDE.md, `docs/02-install.md`,
`docs/13-ci-cd.md`, `docs/27-gitlab-deployment.md`, and
`docs/28-gitlab-migration.md` all point here; update this list (not those files)
when an item is added or its fields change.

- **Cloudflare DNS Token** - API token (credential, scoped DNS:Edit + Zone:Read only) + account ID (username field); consumed by the in-cluster ESO trio (cert-manager, external-dns, cloudflare-ddns) and acme_certs
- **Cloudflare Terraform Token** - API token (credential, scoped Zone:Read + DNS:Edit + **Zone Settings:Edit** — Terraform manages `cloudflare_zone_settings_override`) + account ID (username field); Terraform via Taskfile + the terraform-plan/deploy-terraform CI jobs only
- **SMTP Relay Gmail** - username + app password
- **SMTP Relay Auth** - username + password (for null client auth to smtp-relay)
- **Email Config** - root_alias (ericsweiss1@gmail.com)
- **AdGuard Home** - admin username + password (the `adguard_home` role generates the bcrypt hash in `AdGuardHome.yaml` from this plaintext password at deploy time; no `password_hash` field is injected or consumed). Also consumed by `terraform/authentik` (`TF_VAR_basic_auth_adguard_*`) as the injected credentials on the AdGuard SSO dashboard proxy providers (docs/08) — single source, so the injection can never drift from the deployed admin login. Rotation therefore needs BOTH `task dns:deploy` and a supervised terraform apply (docs/40)
- **Tailscale Auth Key** - auth key
- **Tailscale OAuth** - client id + credential (OAuth client scoped `acl` **and `dns`** write; used by `terraform/tailscale` to manage the tailnet ACL policy AND the `esweiss.com` Split-DNS nameservers — see that module's README)
- **Tailscale Operator OAuth** - `client-id` + `client-secret` (OAuth client scoped **write** on Devices/Core, Keys/Auth Keys, and Services, associated with tag `tag:k8s-operator`; consumed in-cluster by ESO → Secret `operator-oauth` for the Tailscale Kubernetes operator that exposes the internal Traefik ingress + `ts-dns` resolver to the tailnet — see docs/05). Distinct from the two items above.
- **SSH Key** - public + private key
- **Samba NAS User** - nas user password
- **DNS-01 SSH Key** - private + public key (for cert distribution)
- **K3s Cluster Token** - cluster join token (credential)
- **K3s Agent Token** - lower-privilege worker-join token (credential). Optional: when absent the role falls back to the cluster token. See docs/19 "Agent token".
- **Authentik Secrets** - secret-key, postgresql-password, postgresql-admin-password
- **Authentik Terraform Token** - credential (admin API token for the authentik API; used by `terraform/authentik` to manage applications/providers/groups — see that module's README and docs/40). Rotate by minting a new token in authentik (Directory > Tokens), updating this field, then deleting the old token
- **NZBGet** - username, password (MUST match nzbget.conf's `ControlUsername`/`ControlPassword` — NZBGet validates HTTP Basic against them). Consumed by `terraform/authentik` (`TF_VAR_basic_auth_nzbget_*`) as the injected credentials on the NZBGet proxy provider (docs/21 §Authentik SSO Integration). Rotating the pair = change it in NZBGet (Settings > Security) AND here, then a supervised terraform apply (docs/40)
- **PrivadoVPN Credentials** - openvpn-user, openvpn-password (default Gluetun VPN provider; user/pass auth)
- **VPN Unlimited Credentials** - alternate Gluetun provider (KeepSolid). Gluetun needs **all four** of openvpn-user, openvpn-password, openvpn-clientcrt and openvpn-clientkey: its generated OpenVPN config is cert/key-based (auth-user-pass off) but its settings validation still requires a non-empty user + password for the provider. To enable `task downloads:vpn-provider -- PROVIDER=vpnunlimited`, generate a Manual/OpenVPN config for one device in the VPN Unlimited portal, then add fields **openvpn-user**, **openvpn-password**, **openvpn-clientcrt** (full PEM `<cert>` block) and **openvpn-clientkey** (full PEM `<key>` block), and uncomment the `vpnunlimited-*` entries in `kubernetes/apps/download-clients/externalsecret.yaml` (docs/21)
- **WireGuard VPN** - init-username, init-password (wg-easy admin, bootstrapped on first boot; later rotation is a UI action), metrics-token (Bearer password for the `/metrics/prometheus` endpoint — must be pasted into Admin Panel > General to enable metrics). See docs/38-wireguard-vpn.md
- **Mealie Secrets** - postgres-password
- **Mealie SSO** - oidc-client-id, oidc-client-secret (Authentik OIDC, REQUIRED - password login disabled)
- **Bar Assistant Secrets** - meilisearch-master-key
- **Bar Assistant SSO** - authentik-client-id, authentik-client-secret (Authentik OIDC, REQUIRED - password login disabled)
- **OpenAI API Key** - api-key (for Mealie recipe parsing, optional). No longer ESO-synced into the cluster — the key is entered in Mealie's UI (Settings > AI), so rotation is a manual in-app update there
- **Hermes Secrets** - dashboard-username, dashboard-password, dashboard-session-secret, api-server-key, claude-code-oauth-token, discord-bot-token, hermes-dashboard-oidc-client-secret (**seven fields — all must exist**, a missing field fails the whole ESO Secret sync). The three `dashboard-*` fields feed the dashboard's `basic` break-glass auth provider (the 0.0.0.0 bind is fail-closed and will not start without a registered provider); api-server-key is the bearer key for the gateway's in-process OpenAI-compatible API server, enabled as the gateway's health-probe surface (the server refuses to start without it); claude-code-oauth-token is the Claude Code delegate's Max-subscription OAuth token from `claude setup-token` (sk-ant-oat01-*, docs/37 §Coding delegates); discord-bot-token is the gateway's Discord platform token, upserted into `/opt/data/.env` by the init container on every pod start (gateway config never reads container env — docs/37 §Gateway platform config); hermes-dashboard-oidc-client-secret is the dashboard's Authentik OIDC client secret (docs/37 §SSO), read by BOTH the `hermes-secrets` ExternalSecret and `terraform/authentik` (`TF_VAR_oauth2_client_secret_hermes_dashboard`, docs/40) so authentik and the dashboard can never disagree. dashboard-session-secret, api-server-key, and hermes-dashboard-oidc-client-secret are >=32-byte random values, `openssl rand -hex 32`. No LLM-provider API key here: Hermes' LLM turns use ChatGPT-subscription OAuth held in Hermes' own store (`hermes auth add openai-codex`, ~/.hermes/auth.json on the encrypted NFS volume), and the codex CLI keeps its separate `codex login` token in `CODEX_HOME=/opt/data/.codex/auth.json` for the app-server runtime + MCP delegate — neither lives in 1Password (docs/37 §LLM engine, two-credential-store table). Other Hermes provider/platform keys are entered in the dashboard UI (persisted to the encrypted, backed-up `/opt/data/.env`), not here- **Hermes Registry Pull** - username, token (GitLab **deploy token** scoped `read_registry`, created on the weisssrv project; used by the `hermes-registry-pull` ExternalSecret to pull the self-built hermes-agent image from `registry.git.esweiss.com`)
- **Home Assistant SSO** - authentik-client-id, authentik-client-secret (Authentik OIDC via hass-openid)
- **Home Assistant API Token** - token (long-lived access token for Prometheus /api/prometheus endpoint)
- **GitLab** - root-password (initial GitLab root user password)
- **GitLab API Token** - credential (admin personal access token, `api` scope; used by PR-Agent AI code review and hard-asserted by `task gitlab:deploy` for the Web IDE Application Settings block)
- **GitLab SSO** - saml-cert-fingerprint (Authentik SAML)
- **GitLab Runner** - runner-token (glrt-* format, tags: k8s-deploy, run untagged: yes, shared multi-project runner)
- **GitLab Runner Privileged** - runner-token (glrt-* format, tags: infrastructure, run untagged: no, weisssrv infrastructure runner)
- **GitLab Agent Token** - credential (agent token for GitLab Kubernetes Agent, registered via Operate > Kubernetes clusters)
- **Nextcloud Secrets** - admin-password (break-glass local admin, reachable at `/login?direct=1`), postgres-password (nextcloud DB role), serverinfo-token (random ≥32 chars; the token the nextcloud-exporter authenticates to Nextcloud's serverinfo API with — the role sets it via `occ config:app:set serverinfo token`)
- **Nextcloud SSO** - client-id, client-secret (Authentik OIDC via the `user_oidc` app, REQUIRED — SSO-only, local login form hidden)
- **Immich Secrets** - postgres-password (compose DB_PASSWORD); admin-bootstrap-password (operator-only — the password you set on the one-time Immich admin-registration page during SSO bootstrap; NOT injected by Ansible)
- **Immich SSO** - client-id, client-secret (Authentik OIDC, REQUIRED - password login disabled after bootstrap)
- **GitHub Token** - credential (personal access token for version checker API rate limits)
- **GitLab Terraform State Token** - credential (project access token for Terraform HTTP state backend, local use)
- **K3s Kubeconfig** - kubeconfig file content (used by .k3s-deploy-base CI template as fallback; agent is preferred)
- **Service Account Auth Token weisssrv** - 1Password Service Account token used by CI (`OP_SERVICE_ACCOUNT_TOKEN` in `.gitlab-ci.yml`)
- **Flux GitLab PAT** - personal access token used by Flux to read `kubernetes/` from the GitLab repo
- **Flux Webhook Token** - auto-generated hex token shared between GitLab webhook config and a Flux `Receiver` (reserved for the optional Receiver-based webhook path; day-to-day push-triggered reconciliation comes from the GitLab agent's Flux integration — see docs/29)
- **Plex Token** - token (X-Plex-Token for Plex exporter metrics)
- **Download Client API Keys** - sonarr-api-key, radarr-api-key, lidarr-api-key, prowlarr-api-key (from each app's Settings > General); **gluetun-control-apikey** (auth key for the Gluetun control server; generate with `openssl rand -hex 32`) — rendered into the `gluetun-control-auth` Secret's `config.toml` and consumed by the gluetun-exporter as `GLUETUN_APIKEY`. Rotating this field requires re-syncing the `gluetun-control-auth` ExternalSecret from 1Password **and then** restarting the pods: ESO only re-fetches on its 24h `refreshInterval` and Reloader ignores Secret changes (`ignoreSecrets: true`), so a bare pod restart would just re-read the stale apikey and the rotation would be a silent no-op. Use `task flux:rotate-secret -- downloads` (force-syncs both `gluetun-control-auth` and `vpn-credentials`, then restarts nzbget/qbittorrent), or manually `task flux:refresh-secret -- downloads/gluetun-control-auth && kubectl rollout restart deployment/qbittorrent -n downloads`. See docs/21 § Control-Server Auth
- **Grafana SSO** - oidc-client-id, oidc-client-secret (Authentik OIDC for Grafana)
- **Loki Push Auth** - htpasswd (bcrypt users file for the Loki push IngressRoute basicAuth middleware)
- **Proxmox API Token** - user, token-name, token-secret (PVEAuditor role, for Proxmox exporter)
- **Discord Alert Webhook** - url (Discord channel webhook for Alertmanager notifications)
- **Healthchecks Watchdog** - ping url (healthchecks.io check pinged by Alertmanager's always-firing Watchdog dead-man's switch; consumed by the alertmanager-config ExternalSecret)
- **ZFS Encryption Connect Token** - credential (Connect access token used by Proxmox hosts to fetch ZFS pool passphrases at boot; created via `op connect token create weisssrv-zfs --server <id> --vaults Homelab`)
- **ZFS Pool tank Passphrase** - passphrase (random ≥32 chars; consumed by zfs-load-key@tank.service on pve-nas-01)
- **ZFS Pool ssd Passphrase** - passphrase (zfs-load-key@ssd.service on pve-nas-01)
- **Plex Custom Certificate** - password (PFX bundle passphrase used by `/usr/local/sbin/plex-cert-reload.sh` when converting the renewed PEM cert into the PKCS#12 form Plex requires; matching value must be configured under Plex Settings -> Network -> "Custom certificate encryption key")

## Kubernetes workloads (External Secrets Operator)

For any secret consumed inside the k3s cluster (every `ExternalSecret` in
`kubernetes/apps/*/externalsecret.yaml` and
`kubernetes/infrastructure/configs/shared-cloudflare-secrets/`):

```bash
# 1. Update the 1Password item (web/desktop app or CLI).
eval $(op signin)   # if needed
# op item edit <item-id> <field>[password]=<new-value>

# 2. Refresh ExternalSecret + restart consuming pods in one go:
task flux:rotate-secret -- authentik    # or: downloads, recipes, gitlab-runner,
                                        #     gitlab-runner-privileged, gitlab-agent

# 3. (Optional) Refresh an ExternalSecret without restarting pods — useful
#    for secrets that don't require a pod restart to take effect.
task flux:refresh-secret -- authentik/authentik-secrets
```

The `task flux:rotate-secret -- <app>` command annotates the ExternalSecret
with a force-sync timestamp, waits for ESO to re-fetch from 1Password, then
restarts the Deployments/StatefulSets that consume the produced Secret. See
`docs/29-flux-operations.md` § Secret Operations for the full dispatch table
and rate-limit considerations.

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

# 4. Deploy SMTP configuration
ansible-playbook ansible/playbooks/site.yml --tags smtp

# 5. Verify mail still works
ssh eric@192.168.0.102 "echo 'Test' | mail -s 'Rotation Test' root"

# Check mail arrives at your root_email_alias
```

**What Happens**:
- Gmail password: `/etc/postfix/sasl_passwd` updated on smtp-relay, postmap rebuilds hash
- Relay user password: SASL database (`/etc/sasldb2`) updated on smtp-relay.
  `saslpasswd2` reruns on every deploy, but it reports changed (and reloads
  postfix) only on an actual rotation — sasldb stores no recoverable
  plaintext, so the role detects rotation via a sha256 fingerprint sentinel
  at `/etc/postfix/.sasl_relay_user.sha256` (root:root, 0600)
- Null client passwords: `/etc/postfix/sasl_passwd` updated on all Proxmox hosts and DNS LXCs
- Postfix reloads on all affected hosts

**Affected Hosts**:
- `smtp-relay` - Both passwords
- `pve-nas-01`, `pve-laptop-01`, `pve-opt-01`, `pve-opt-02`, `pve-opt-03`, `pve-prec-01` - Relay auth password
- `dns-01`, `dns-02` - Relay auth password
- `plex`, `gitlab` - Relay auth password

---

### SSH Keys

**Location**: 1Password → Homelab vault → SSH Key item

**Rotation Procedure**:

```bash
# 1. Generate new key pair
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com" -f ~/.ssh/id_ed25519_new

# 2. Update public key in 1Password
# Copy contents of ~/.ssh/id_ed25519_new.pub to 1Password

# 3. Verify new key is readable
op read "op://Homelab/SSH Key/public key"

# 4. Deploy new key to all hosts
ansible-playbook ansible/playbooks/base.yml --tags ssh

# 5. Test SSH with new key (before removing old one!)
ssh -i ~/.ssh/id_ed25519_new eric@192.168.0.102

# 6. Once verified, replace old key
mv ~/.ssh/id_ed25519_new ~/.ssh/id_ed25519
mv ~/.ssh/id_ed25519_new.pub ~/.ssh/id_ed25519.pub

# 7. Update SSH agent
ssh-add -D
ssh-add ~/.ssh/id_ed25519
```

**What Happens**:
- `authorized_keys` updated on all managed hosts
- Old key still works until you remove it from 1Password and redeploy

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
- acme.sh on dns-01 reads it at issue/renew time (`CF_Token`).
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
task terraform:plan

# 5. If plan succeeds, token is valid
# No deployment needed - Terraform reads at runtime
```

**Affected Systems**: Terraform only — the Taskfile `terraform:*` wrappers
and the `terraform-plan` / `deploy-terraform` CI jobs. No in-cluster or
host-side consumer reads this item.

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
ansible-playbook ansible/playbooks/postflight.yml --limit dns-01,dns-02

# 4. Test login with new password
open https://192.168.0.150:3000
# Login with username: eric, new password from step 1
```

**Alternative: Python bcrypt generation**:

```bash
# If htpasswd is not available, use Python
python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt(rounds=10)).decode())"
```

**What Happens**:
- AdGuard Home admin password hash updated in `AdGuardHome.yaml` on both dns-01 and dns-02
- AdGuard Home service restarted on both hosts
- Configuration synced from dns-01 to dns-02 automatically via adguardhome-sync

**Affected Hosts**: `dns-01`, `dns-02`

**Security Notes**:
- The bcrypt hash uses a random salt generated by htpasswd/bcrypt
- Each password rotation generates a new unique hash
- Plaintext password never touches production servers
- Hash is validated (must start with `$2a$`, `$2b$`, or `$2y$`) before deployment

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
smbclient //192.168.0.102/share -U nas
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
ansible-playbook ansible/playbooks/base.yml --tags tailscale --limit new-host
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
ansible-playbook ansible/playbooks/site.yml --limit affected-hosts

# 4. For Proxmox firewall or network-level security:
ansible-playbook ansible/playbooks/site.yml --tags firewall

# 5. Revoke old credential at source (Cloudflare, Tailscale, Gmail, etc.)
```

### SSH Key Compromise

```bash
# 1. Generate new key immediately
ssh-keygen -t ed25519 -C "eric@MacBookPro.esweiss.com" -f ~/.ssh/id_ed25519_emergency

# 2. Update in 1Password

# 3. Deploy to ALL hosts immediately
ansible-playbook ansible/playbooks/base.yml --tags ssh

# 4. Verify emergency key works
ssh -i ~/.ssh/id_ed25519_emergency eric@192.168.0.102

# 5. Remove compromised key from laptop and 1Password
# 6. Check all hosts for unauthorized access:
ansible all -m shell -a "last -20"
```

---

## Verification After Rotation

### SMTP

```bash
# Test mail relay
ssh eric@192.168.0.102 "echo 'Test after rotation' | mail -s 'Test' root"

# Check smtp-relay logs
ssh eric@192.168.0.151 "sudo tail -f /var/log/mail.log"

# Verify authentication
ssh eric@192.168.0.151 "sudo grep sasl /var/log/mail.log | tail -20"
```

### SSH

```bash
# Test all hosts
ansible all -m ping

# Check authorized_keys was updated
ansible all -m shell -a "wc -l ~/.ssh/authorized_keys"
```

### DNS/AdGuard

```bash
# Test login
curl -k https://192.168.0.150:3000

# Check sync status (if password changed)
ssh eric@192.168.0.150 "sudo systemctl status adguardhome-sync"
```

---

## Scheduled Rotation Policy

**Recommended Schedule**:

| Credential Type | Rotation Frequency | Reason |
|-----------------|-------------------|--------|
| SSH Keys | Annually | Low risk, high impact if rotated incorrectly |
| SMTP Passwords | Every 6 months | Medium risk, Gmail app passwords |
| API Tokens | Every 6 months | Medium risk, scoped permissions |
| AdGuard Password | Annually | Low exposure, local network only |
| Samba NAS Password | Every 6 months | Medium risk, network file sharing |
| Tailscale Auth Keys | Generate new for each node | One-time use |

**Automation** (future):

```bash
# Add to crontab or calendar reminder
# Every 6 months: Review and rotate SMTP and API credentials
# Every 12 months: Review and rotate SSH keys
```

---

## Troubleshooting

### "Permission denied" after rotation

**Cause**: New credential not deployed or wrong format

**Fix**:
```bash
# Verify credential in 1Password
op read "op://Homelab/Item/field"

# Re-run deployment with verbose output
ansible-playbook playbook.yml -vv

# Check specific host
ansible host -m setup
```

### Mail stops working after SMTP rotation

**Cause**: Password mismatch or postmap not run

**Fix**:
```bash
# Check sasl_passwd on smtp-relay
ssh eric@192.168.0.151 "sudo cat /etc/postfix/sasl_passwd"

# Manually rebuild hash
ssh eric@192.168.0.151 "sudo postmap /etc/postfix/sasl_passwd && sudo systemctl reload postfix"

# Check logs
ssh eric@192.168.0.151 "sudo journalctl -u postfix -n 50"
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
task terraform:plan

# Or manually export and retry
# export CLOUDFLARE_API_TOKEN=$(op read "op://Homelab/Cloudflare Terraform Token/credential")
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

# 2. Rotate the passphrase BEFORE the drive leaves. Each dataset is its own
#    encryption root (Model B) and the plaintext pool root is NOT a key holder,
#    so change-key every encryption root in the pool — `zfs change-key <pool>`
#    alone fails ("not an encryption root"). See docs/32-zfs-encryption.md §4.
for root in $(zfs get -H -t filesystem,volume -o name,value -r encryptionroot <pool> \
                | awk -F'\t' '$1==$2{print $1}'); do
  sudo zfs change-key -o keyformat=passphrase -o keylocation=prompt "$root"  # paste new passphrase
done
op item edit "ZFS Pool <pool> Passphrase" 'passphrase=<new value>'

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

## Related Documentation

- [02-install.md](02-install.md) - Initial 1Password setup
- [03-ssh-users.md](03-ssh-users.md) - SSH key management
- [10-mail.md](10-mail.md) - SMTP relay configuration
