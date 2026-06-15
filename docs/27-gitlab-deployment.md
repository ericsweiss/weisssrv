# GitLab Deployment Guide

Complete guide for deploying GitLab EE (using CE features) on the homelab.

## Overview

GitLab is deployed as a standalone VM on pve-nas-01 with:
- **URL**: https://git.esweiss.com (internal) / https://git.ericsweiss.com (external)
- **Container Registry**: https://registry.git.ericsweiss.com
- **GitLab Pages**: https://*.pages.git.ericsweiss.com
- **CI/CD Runners**: Helm-deployed on k3s cluster
- **SSO**: Authentik SAML integration with group-based admin access

## Architecture

```
                   +-----------------+
                   |   Internet      |
                   +--------+--------+
                            |
                   +--------v--------+
                   |   Cloudflare    |
                   |  (DNS + Proxy)  |
                   +--------+--------+
                            |
                   +--------v--------+
                   |    Traefik      |
                   |  (k3s ingress)  |
                   +--------+--------+
                            |
        +-------------------+-------------------+
        |                   |                   |
+-------v-------+   +-------v-------+   +-------v-------+
|   GitLab Web  |   |   Registry    |   |    Pages      |
|  :443 (TLS)   |   |  :5050 (TLS)  |   |  :8443 (TLS)  |
+---------------+   +---------------+   +---------------+
        |                   |                   |
        +-------------------+-------------------+
                            |
                   +--------v--------+
                   |   GitLab VM     |
                   | 192.168.0.153   |
                   |   pve-nas-01    |
                   +-----------------+
```

## Specifications

| Component | Value |
|-----------|-------|
| IP Address | 192.168.0.153 |
| Proxmox Host | pve-nas-01 |
| Root Disk | ssd pool (100GB) - OS, GitLab binaries, configs |
| Repo Disk | ssd pool (200GB zvol) - Git repositories |
| Resources | 6 vCPUs, 16GB RAM |
| VMID | 153 |
| GitLab Version | See `gitlab_version` in `ansible/inventories/prod/group_vars/all.yml` |
| Git SSH Port | 22 (internal), 2222 (external via iptables redirect) |

**SSH Port Redirect:** GitLab's SSH daemon runs on port 22. External access on port 2222 is handled via iptables NAT redirect (`PREROUTING -p tcp --dport 2222 -j REDIRECT --to-ports 22`). This allows internal LAN users to use port 22 directly while external users connect on port 2222.

### Storage Architecture

GitLab uses **separate storage** for Git repository data:

```
VM Root Disk (100GB on ssd pool)
├── /                           # OS, packages
├── /opt/gitlab/                # GitLab binaries
├── /etc/gitlab/                # Configuration
├── /var/opt/gitlab/            # Runtime data
│   ├── backups/                # Backup files
│   ├── postgresql/             # PostgreSQL database
│   ├── redis/                  # Redis cache
│   ├── gitlab-rails/uploads/   # Issue/MR attachments
│   └── lfs-objects/            # Git LFS storage
└── /var/log/gitlab/            # Logs

Repository Disk (200GB zvol: ssd/appdata/gitlab/repos)
└── /mnt/gitlab-repos/git-data/ # Git repositories only
    └── repositories/           # Bare git repos (@hashed/)
```

**Note:** Only Gitaly repository storage is configured to use the separate disk.
Uploads (issue/MR attachments) and LFS objects remain on the root disk. This is
intentional - uploads and LFS are typically much smaller than repos, and keeping
them on root disk simplifies configuration while the separate disk provides the
primary benefit of isolating large repository I/O.

**Benefits:**
- Independent sizing - repos can grow without affecting root disk
- Better I/O isolation - git operations don't compete with PostgreSQL
- Easier backups - zvol can be snapshotted separately

## Prerequisites

1. **Authentik SSO** - Configure SAML provider before deployment
2. **1Password Items** - Create required items (see below)
3. **Terraform DNS** - Apply Cloudflare DNS records

## 1Password Items Required

Create these items in your **Homelab** vault:

| Item | Type | Fields |
|------|------|--------|
| **GitLab** | Login | `root-password` |
| **GitLab SSO** | Password | `saml-cert-fingerprint` |
| **GitLab Runner** | Password | `runner-token` (runner authentication token, `glrt-*` format) |
| **GitLab Runner Privileged** | Password | `runner-token` (runner authentication token, `glrt-*` format, tags: `infrastructure`) |
| **SMTP Relay Auth** | Login | `username`, `password` (for GitLab email notifications) |
| **Email Config** | Login | `root_alias` (admin email address, e.g., `admin@example.com`) |
| **SSH Key** | SSH Key | `public key` (for VM access via cloud-init) |

**Note:** The SMTP Relay Auth, Email Config, and SSH Key items should already exist if you've deployed the base infrastructure. GitLab uses SMTP Relay Auth to send email notifications (merge requests, CI/CD alerts, etc.) through the smtp-relay service.

## Deployment Steps

### Step 1: Create 1Password Items

```bash
# Create GitLab item with root password
op item create --vault Homelab --category login \
  --title "GitLab" \
  "root-password=$(openssl rand -base64 32)"

# Create placeholder for SSO (populate after Authentik setup)
op item create --vault Homelab --category password \
  --title "GitLab SSO" \
  "saml-cert-fingerprint=placeholder"

# Create placeholder for Runner (populate after GitLab is running)
op item create --vault Homelab --category password \
  --title "GitLab Runner" \
  "runner-token=placeholder"
```

### Step 2: Apply Terraform DNS Records

The GitLab DNS records are defined in `terraform/cloudflare/dns.tf`. Apply them:

```bash
task terraform:plan
task terraform:apply
```

This creates (or updates):
- `git.ericsweiss.com` → A record (DNS-only, IP updated by DDNS) - unified URL for web + SSH
- `registry.git.ericsweiss.com` → CNAME to `direct.ericsweiss.com` (DNS-only, TLS via Traefik)
- `pages.git.ericsweiss.com` → CNAME to `direct.ericsweiss.com` (DNS-only, TLS via Traefik)
- `*.pages.git.ericsweiss.com` → CNAME to `direct.ericsweiss.com` (DNS-only wildcard, TLS via Traefik)
- `direct.ericsweiss.com` → A record (DNS-only, IP updated by DDNS)

**Note:** `git.ericsweiss.com` uses DNS-only mode (not Cloudflare-proxied) to allow both HTTPS
and SSH access on the same hostname. Nested subdomains (registry.git, pages.git, *.pages.git)
use `direct.ericsweiss.com` because Cloudflare Universal SSL only covers first-level wildcards.

**DNS cutover verification:** If `git.ericsweiss.com` was previously a CNAME (e.g., pointing
to `direct.ericsweiss.com`) and is being changed to an A record, verify the transition:

```bash
# 1. Preview the change
task terraform:plan
# Look for: cloudflare_record.git changing type from CNAME to A

# 2. Apply the change
task terraform:apply

# 3. Verify DNS resolution (may take a few minutes for propagation)
dig +short git.ericsweiss.com
# Should return your public IP (same as direct.ericsweiss.com)

# 4. Verify GitLab is accessible
curl -sf --max-time 10 "https://git.ericsweiss.com/-/health"
# Should return: GitLab OK
```

### Step 3: Deploy AdGuard DNS Rewrites

```bash
task dns:deploy
```

This adds internal DNS rewrites for `git.esweiss.com`, `registry.git.esweiss.com`, etc.

### Step 4: Configure Authentik SSO (SAML)

1. Log into Authentik at https://auth.ericsweiss.com
2. Navigate to **Applications → Providers**
3. Click **Create** and select **SAML Provider**
4. Configure:

| Field | Value |
|-------|-------|
| Name | `GitLab` |
| Authorization flow | `default-authorization-flow` |
| ACS URL | `https://git.ericsweiss.com/users/auth/saml/callback` |
| Issuer | `https://git.ericsweiss.com` |
| Service Provider Binding | `Redirect` |
| Signing Certificate | Select available certificate |

5. Click **Finish** to create the provider
6. Navigate to **System → Certificates**
7. Expand the signing certificate and copy the **SHA1 Fingerprint**
8. Update 1Password `GitLab SSO` item with the fingerprint as `saml-cert-fingerprint`
9. Navigate to **Applications → Applications**
10. Click **Create** and configure:

| Field | Value |
|-------|-------|
| Name | `GitLab` |
| Slug | `git` |
| Provider | `GitLab` (SAML provider created above) |
| Launch URL | `https://git.ericsweiss.com` |

11. **Configure group-based access** (REQUIRED):

    **IMPORTANT**: The default configuration enforces group-based access. Users must be in
    one of the required groups to log in via SSO. Complete these steps BEFORE deploying:

    - Create Authentik groups: `gitlab-users`, `gitlab-admins`
    - Assign users to appropriate groups (at minimum, add yourself to `gitlab-users`)
    - The groups are configured in `ansible/inventories/prod/group_vars/gitlab_servers.yml`:
      ```yaml
      gitlab_saml_required_groups:
        - "gitlab-users"
        - "gitlab-admins"
      gitlab_saml_admin_groups:
        - "gitlab-admins"
      ```

    If you skip this step, SSO login will fail with "User is not allowed" errors.

### Step 5: Deploy GitLab VM and Application

```bash
# Full deployment (provisions VM + installs GitLab)
task gitlab:deploy

# Or check mode first
task gitlab:deploy-check
```

### Step 6: Traefik IngressRoutes (Flux-managed)

The Traefik IngressRoutes for `git.ericsweiss.com` (web + SSH port 2222),
`registry.git.ericsweiss.com`, and `*.pages.git.ericsweiss.com` live under
`kubernetes/apps/vm-ingress/gitlab.yaml`, with ExternalName Services in
`services-gitlab.yaml` and certificates in `gitlab-certificate.yaml`. Flux
reconciles them from the `apps` Kustomization. To change them, edit the
YAML + commit + push; the change is live within ~1 minute.

### Step 7: Verify Deployment

```bash
# Check GitLab status
task gitlab:status

# Test web access
curl -I https://git.esweiss.com
```

### Step 8: Get Runner Authentication Token

**Note**: GitLab 16.0+ uses runner authentication tokens (`glrt-*` format) instead of the deprecated registration tokens. The old `runnerRegistrationToken` method was removed in GitLab 18.0.

Two runners are needed:
- **Shared runner** (unprivileged): For other GitLab projects and collaborators to deploy to k3s. Runs untagged jobs. Tag: `k8s-deploy`.
- **Infrastructure runner** (privileged): Handles ALL weisssrv CI/CD jobs (lint, test, deploy). Uses DinD for Molecule tests, SSH for Ansible deploys. Tag: `infrastructure`.

The weisssrv `.gitlab-ci.yml` sets `default: tags: [infrastructure]` so all its jobs route to the infrastructure runner.

**Runner 1 (Shared - Non-privileged):**
1. Log into GitLab as root (use password from 1Password)
2. Navigate to **Admin Area → CI/CD → Runners**
3. Click **New instance runner**
4. Configure: Tags = `k8s-deploy`, Run untagged jobs = **Yes**
5. Click **Create runner** and copy the `glrt-*` token
6. Update 1Password `GitLab Runner` item with this token as `runner-token`

**Runner 2 (Infrastructure - Privileged):**
1. Click **New instance runner** again
2. Configure: Tags = `infrastructure`, Run untagged jobs = **No**
3. Click **Create runner** and copy the `glrt-*` token
4. Create 1Password item `GitLab Runner Privileged` with this token as `runner-token`

> **Security note on privileged runner:** The infrastructure runner (`gitlab-runner-privileged`)
> has `privileged = true` and `protected = false`, meaning it can run jobs from unprotected
> branches. This is acceptable for a single-user homelab where you control all branches and
> merge requests. If collaborators are added to the GitLab instance, switch to
> `protected = true` so the privileged runner only executes jobs on protected branches (e.g.,
> `main`). This prevents untrusted MR code from running with elevated privileges.

### Step 9: GitLab Runners (Flux-managed)

Both runners (shared and privileged) are Flux `HelmRelease`s under
`kubernetes/apps/gitlab-runner/` and `kubernetes/apps/gitlab-runner-privileged/`.
Their runner tokens flow from 1Password through `ExternalSecret`s. To deploy
or update them, edit the relevant `release.yaml` (or bump
`gitlab_runner_helm_version` in `all.yml` + run `task flux:sync-versions`)
and push. Flux reconciles the HelmRelease within ~1 minute.

See `docs/29-flux-operations.md` for the full workflow (rotating runner
tokens, bumping chart versions, troubleshooting stuck releases).

## Task Commands

```bash
# VM deployment (Ansible)
task gitlab:deploy          # Full deployment (VM + GitLab application)
task gitlab:deploy-check    # Dry-run deployment

# Operations
task gitlab:status          # Show GitLab and runner status
task gitlab:verify          # Run smoke tests (web UI, registry, pages, SSH)
task gitlab:backup          # Create GitLab backup
task gitlab:console         # SSH to GitLab VM
task gitlab:logs            # View GitLab logs
task gitlab:reconfigure     # Reconfigure GitLab after changes

# Ingress, runners, agent — all Flux-managed: edit YAML + git push.
# See kubernetes/apps/{gitlab-runner,gitlab-runner-privileged,gitlab-agent}/
# and kubernetes/apps/vm-ingress/gitlab*.yaml.
```

## Backups

GitLab backups run daily at 2:00 AM via cron. The cron entry calls
`/usr/local/sbin/gitlab-backup-run.sh` (deployed by the gitlab role), which
runs `gitlab-backup create CRON=1`, copies `gitlab-secrets.json` + `gitlab.rb`
into the backup path on success (a restore needs them to decrypt CI variables,
2FA, and runner tokens), and emits backup-freshness metrics.

```bash
# Manual backup (also re-seeds the freshness metric)
task gitlab:backup

# List backups
ssh gitlab "sudo ls -la /var/opt/gitlab/backups/"

# Restore from backup
ssh gitlab "sudo gitlab-backup restore BACKUP=<timestamp>"
```

Proxmox VM snapshots provide additional disaster recovery capability.

### Backup monitoring

The wrapper writes `/var/lib/node_exporter/gitlab_backup.prom` with:

- `gitlab_backup_last_run_success` (1/0)
- `gitlab_backup_last_run_duration_seconds`
- `gitlab_backup_last_success_timestamp_seconds` (preserved across failed runs,
  so staleness measures time-since-last-success, not time-since-last-attempt)
- `gitlab_backup_last_size_bytes` (newest tarball; informational, no alert)

`node_exporter_host` runs on the GitLab VM (port 9101) so the textfile metric is
scraped alongside the host's other metrics. Prometheus discovers the VM via the
`node-exporter-host-gitlab` Service/Endpoints
(`kubernetes/infrastructure/observability/exporters/node-exporter-host.yaml`,
pinned to 192.168.0.153). The `sg-metrics` security group already authorizes the
9101 scrape from the k3s nodes, so no firewall change is needed.

Two alerts (`homelab.scripts` group) cover backup health, on different
timescales — `GitLabBackupFailed` fires ~1h after a single failed run, while
`GitLabBackupStale` only fires once no run has succeeded for 48 hours:

| Alert | Condition |
|-------|-----------|
| `GitLabBackupFailed` | `gitlab_backup_last_run_success == 0` for 1h (a single failed run) |
| `GitLabBackupStale` | no successful backup in > 2 days, or the metric is absent, for 1h |

The `absent()` arm of `GitLabBackupStale` fires before the first 02:00 run. After
a fresh deploy, run `task gitlab:backup` once to seed the metric.

## Updates

### Updating GitLab

1. Update `gitlab_version` in `ansible/inventories/prod/group_vars/all.yml`
2. Run deployment:
   ```bash
   task gitlab:deploy
   ```

### Updating GitLab Runner

Both runners share the same Helm chart version and are Flux-managed. Update:

1. Bump `gitlab_runner_helm_version` in `ansible/inventories/prod/group_vars/all.yml`
2. Regenerate the ConfigMap and commit:
   ```bash
   task flux:sync-versions
   git add ansible/inventories/prod/group_vars/all.yml \
           kubernetes/infrastructure/sources/versions-configmap.yaml
   git commit -m "Bump GitLab Runner chart to <version>"
   git push
   ```
3. Flux reconciles both `gitlab-runner` and `gitlab-runner-privileged`
   HelmReleases on the next cycle. Verify with `flux get hr -n gitlab-runner`.

## Troubleshooting

### GitLab Reconfigure Fails

```bash
ssh gitlab "sudo gitlab-ctl reconfigure"
ssh gitlab "sudo gitlab-ctl status"
```

### SAML/SSO Issues

1. Verify SAML certificate fingerprint matches Authentik certificate
2. Check ACS URL matches exactly: `https://git.ericsweiss.com/users/auth/saml/callback`
3. Verify Issuer matches `external_url`: `https://git.ericsweiss.com`
4. Check GitLab logs:
   ```bash
   ssh gitlab "sudo gitlab-ctl tail puma"
   ```
5. **Admin groups not syncing**: Ensure users are in the Authentik group and `gitlab_saml_admin_groups` includes the group name

### Container Registry Issues

```bash
# Check registry service
ssh gitlab "sudo gitlab-ctl status registry"

# Registry logs
ssh gitlab "sudo gitlab-ctl tail registry"

# Test registry access
docker login registry.git.ericsweiss.com
```

### Pages Issues

```bash
# Check pages service
ssh gitlab "sudo gitlab-ctl status gitlab-pages"

# Pages logs
ssh gitlab "sudo gitlab-ctl tail gitlab-pages"
```

### Runner Not Connecting

**Important**: The runner must use the external URL `https://git.ericsweiss.com` because
while k3s nodes are configured to use AdGuard DNS (192.168.0.150/160), the internal
`*.esweiss.com` domains may not be reliably resolvable from within pods if CoreDNS
configuration differs or caching causes issues. Using the external domain ensures
consistent connectivity regardless of DNS configuration.

```bash
# Check runner pod status
kubectl get pods -n gitlab-runner

# Check runner logs
kubectl logs -n gitlab-runner -l app=gitlab-runner

# Verify CI_SERVER_URL is using external domain
kubectl get deploy gitlab-runner -n gitlab-runner -o yaml | grep CI_SERVER_URL

# Verify registration token
kubectl get secret -n gitlab-runner
```

## Git SSH Access

### Summary

- **Unified URL**: `git.ericsweiss.com` works for both HTTPS and SSH (port 2222)
- **LAN (internal)**: Use `git.esweiss.com` on port 22 (direct to GitLab VM)
- **External (internet)**: Use `git.ericsweiss.com` on port 2222 (DNS-only, same as web URL)

**Architecture:** `git.ericsweiss.com` is DNS-only (not Cloudflare-proxied), allowing both
HTTPS (via Traefik) and SSH traffic on the same hostname. This provides a unified URL for
clone operations regardless of protocol.

### Internal Access (LAN)

From the LAN, you can SSH directly to the GitLab VM using the internal domain:

```bash
# Clone via SSH using internal domain (port 22)
git clone git@git.esweiss.com:username/repo.git
```

Add to `~/.ssh/config` for easy internal access:
```
Host git.esweiss.com
    Port 22
    User git
```

### External Access (Public Internet)

Git SSH access on port 2222 is open to the public internet, allowing collaborators and CI/CD
systems to clone and push without requiring Tailscale or VPN access. The Proxmox firewall
`sg-gitlab` security group allows port 2222 from any source.

**SSH Host:** External SSH uses `git.ericsweiss.com` - the same URL as the web UI. This is
possible because `git.ericsweiss.com` is DNS-only (not Cloudflare-proxied), allowing both
HTTPS and SSH traffic. GitLab displays this unified URL in clone URLs.

```bash
# Clone via SSH (port 2222) from external networks
git clone ssh://git@git.ericsweiss.com:2222/username/repo.git
```

Add to `~/.ssh/config` for easy external SSH access:
```
Host git.ericsweiss.com
    Port 2222
    User git
```

Then clone using standard syntax:
```bash
git clone git@git.ericsweiss.com:username/repo.git
```

### Universal SSH Config (Works Everywhere)

For a single config that works from any location, use `git.ericsweiss.com` with port 2222
for both hostnames. This works because LAN clients can reach the external IP (hairpin NAT)
and the iptables redirect on the GitLab VM maps port 2222 to port 22:

```
Host git.ericsweiss.com
    Port 2222
    User git

Host git.esweiss.com
    Port 2222
    User git
```

**Note:** This universal config requires hairpin NAT (NAT reflection) on your router. If
your router doesn't support hairpin NAT, LAN clients won't be able to reach
`git.ericsweiss.com` via the external IP. In that case, use the split internal/external
configs shown above instead.

**Why port 2222 for internal too?** While `git.esweiss.com` resolves to the GitLab VM's
internal IP (192.168.0.153), using port 2222 keeps the config consistent across networks.
The iptables PREROUTING rule on the GitLab VM redirects port 2222 to port 22 regardless of
source. If you prefer lower latency on LAN, use the split config shown above (port 22 for
`git.esweiss.com`, port 2222 for `git.ericsweiss.com`).

### Recommended: HTTPS for Consistency

For the simplest experience across all networks (LAN, Tailscale, external), use HTTPS:
```bash
git clone https://git.ericsweiss.com/username/repo.git
```

HTTPS works uniformly via `git.ericsweiss.com` from any location and supports GitLab
personal access tokens for authentication.

**Note:** Administrative SSH access to the GitLab VM (port 22) remains restricted to admin
networks (LAN and Tailscale) for security.

## Container Registry Usage

### Authenticate

```bash
docker login registry.git.ericsweiss.com
# Use GitLab username and personal access token
```

### Build and Push

```bash
# Build image
docker build -t registry.git.ericsweiss.com/myproject/myapp:v1.0 .

# Push to registry
docker push registry.git.ericsweiss.com/myproject/myapp:v1.0
```

### Pull in k3s

```yaml
# In Kubernetes manifests
image: registry.git.ericsweiss.com/myproject/myapp:v1.0
imagePullSecrets:
  - name: gitlab-registry-secret
```

## GitLab Pages

### Enable for a Project

1. Go to project **Settings → Pages**
2. Enable Pages
3. Configure `.gitlab-ci.yml`:

```yaml
pages:
  stage: deploy
  script:
    - mkdir .public
    - cp -r public/* .public/
    - mv .public public
  artifacts:
    paths:
      - public
  only:
    - main
```

### Access Pages

Pages are available at:
- `https://<user>.pages.git.ericsweiss.com/<project>/`
- Or with custom domain configured

## Security Notes

1. **SSH architecture**: GitLab uses a single OpenSSH service with `AuthorizedKeysCommand` for git user authentication. Both administrative SSH (user `eric`) and Git SSH (user `git`) use the same SSH daemon on port 22, with port 2222 NAT-redirected to port 22 for external access.
2. **fail2ban protection**: Brute-force attacks on SSH are automatically blocked
   - 5 failed attempts within 10 minutes = 1 hour ban
   - Uses systemd journal backend with sshd filter (aggressive mode)
   - Check status: `sudo fail2ban-client status gitlab-ssh`
   - View banned IPs: `sudo fail2ban-client get gitlab-ssh banned`
   - Test filter: `sudo fail2ban-regex systemd-journal /etc/fail2ban/filter.d/sshd.conf`
3. **SAML authentication**: Consider disabling password auth after confirming SSO works
4. **Firewall**: The `sg-gitlab` security group has differentiated access:
   - Port 2222 (Git SSH): Open to WAN for external collaborators
   - Port 22 (Admin SSH + LAN Git): Restricted to admin networks (LAN + Tailscale)
   - Port 443 (GitLab Web, TLS): k3s nodes + admin networks; port 80 stays open to admin sources for the HTTP→HTTPS redirect
   - Ports 5050/8443 (Registry/Pages, both TLS via the distributed wildcard cert): Restricted to k3s nodes only (routed via Traefik)
5. **Secrets**: All credentials via 1Password; never committed to git

## Related Documentation

- [Authentik SSO Setup](23-recipes-sso-setup.md) - Similar SSO configuration pattern
- [K3s Deployment](19-k3s-deployment.md) - Cluster where runners are deployed
- [Firewall Configuration](11-firewall.md) - Security group details

## Web IDE Extension Host

GitLab's Web IDE serves the VS Code editor and per-extension iframes from a separate "extension host" subdomain so the browser's same-origin policy isolates extension JavaScript from the GitLab session cookie. CVE-2026-5816 (CVSS 8.0, fixed in 18.11.1) showed that when the configured extension host is unreachable, GitLab falls back to serving those assets from the GitLab origin itself — at which point a malicious extension can hit `/api/v4/...` with the user's session cookie.

### Architecture

| Component | Value |
|---|---|
| Extension host | `*.ide.git.ericsweiss.com` (DNS-only via Cloudflare → MetalLB public VIP) |
| Apex | `ide.git.ericsweiss.com` (same target) |
| Cert | cert-manager `gitlab-web-ide-wildcard` → secret `gitlab-web-ide-ericsweiss-tls`, DNS-01 via Cloudflare |
| Route | Traefik `IngressRoute gitlab-web-ide` (HostRegexp single-label wildcard + apex Host) → `gitlab-web` Service:443 (HTTPS, `vm-tls-wildcard` ServersTransport) |
| Backend | Same GitLab nginx + Workhorse that fronts `git.ericsweiss.com` (catch-all server_name) |

### GitLab settings (Application Settings API)

Set by the `Web IDE | …` block in `ansible/roles/gitlab/tasks/main.yml`. These have no Omnibus `gitlab.rb` key on 18.11; they live only in the `application_settings` table.

| Field | Value |
|---|---|
| `vscode_extension_marketplace_enabled` | `true` (best-effort on Free; tier rejection is acceptable) |
| `vscode_extension_marketplace_extension_host_domain` | `ide.git.ericsweiss.com` (bare hostname; GitLab generates per-extension subdomains from this parent) |
| `vscode_extension_marketplace_single_origin_fallback_enabled` | `false` |

### Initial deployment

The first deploy must order infrastructure → settings so Web IDE doesn't break in the gap between disabling the fallback and the new extension host being reachable. Run from the repo root:

1. **DNS first** — creates the Cloudflare records before cert-manager polls:
   ```bash
   task terraform:apply
   ```
2. **Wait for cert** — Flux + cert-manager DNS-01 typically takes 60-120s:
   ```bash
   kubectl -n gitlab wait certificate gitlab-web-ide-wildcard --for=condition=Ready --timeout=5m
   ```
3. **Smoke gate (manual, MUST pass before step 4)** — confirms DNS + TLS + IngressRoute are live:
   ```bash
   curl -sI https://probe.ide.git.ericsweiss.com/-/readiness | head -1
   # Expected: HTTP/2 200
   ```
4. **Apply settings + version bump**:
   ```bash
   task gitlab:deploy
   ```
5. **Verify** — Test 8 covers the new host; manual editor check confirms the iframe origin:
   ```bash
   task gitlab:verify
   # Then visit https://git.ericsweiss.com/<group>/<project>/-/ide/
   ```

If step 3 fails, do **not** run step 4 — the security flip would break Web IDE entirely until the asset host is reachable. Diagnose with `kubectl -n gitlab describe certificate gitlab-web-ide-wildcard` and `kubectl -n gitlab logs deploy/traefik -n traefik | grep ide.git`.

### Verification

```bash
task gitlab:verify   # Test 8 probes https://probe.ide.git.ericsweiss.com/-/readiness
ssh gitlab "sudo gitlab-rails runner 'puts ApplicationSetting.last.vscode_extension_marketplace_single_origin_fallback_enabled'"
# expected: false
```

In the browser: open `https://git.ericsweiss.com/<group>/<project>/-/ide/`, edit a file, confirm the editor iframe `src=` points at `*.ide.git.ericsweiss.com` and DevTools shows no SOP violations.

### Rollback

If Web IDE breaks after the flip, restore the (insecure but functional) fallback behavior:

```bash
TOKEN=$(op read "op://Homelab/GitLab API Token/credential")
curl -X PUT "https://git.ericsweiss.com/api/v4/application/settings" \
  -H "PRIVATE-TOKEN: $TOKEN" \
  -d "vscode_extension_marketplace_single_origin_fallback_enabled=true"
```

For a persisted rollback, flip `gitlab_web_ide_single_origin_fallback` to `true` in `ansible/inventories/prod/group_vars/all.yml` (or set it as an override) and re-run `task gitlab:deploy`. The DNS records and cert are inert without the route and safe to leave in place.
